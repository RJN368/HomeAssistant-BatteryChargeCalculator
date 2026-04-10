"""ML Power Estimation orchestrator.

Coordinates data ingestion, training, and inference for the ML power
estimation feature. Designed to run within Home Assistant on a Raspberry Pi.

All blocking operations (I/O, training) run via HA's executor — never
on the event loop.

Lifecycle:
    estimator = MLPowerEstimator(hass, config_entry)
    await estimator.async_start()          # load or train on startup
    correction = estimator.predict(dt, temp_c, physics_kwh)  # fast, sync
    await estimator.async_shutdown()       # cleanup
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import aiohttp
import numpy as np
import pandas as pd

from homeassistant.core import HomeAssistant

from ..const import (
    DEFAULT_ML_TRAINING_LOOKBACK_DAYS,
    GIVENERGY_API_TOKEN,
    GIVENERGY_SERIAL_NUMBER,
    ML_CONSUMPTION_SOURCE,
    ML_CONSUMPTION_SOURCE_BOTH,
    ML_CONSUMPTION_SOURCE_GIVENERGY,
    ML_CONSUMPTION_SOURCE_OCTOPUS,
    ML_TEMP_ENTITY_ID,
    ML_TEMP_SOURCE,
    ML_TEMP_SOURCE_HA_ENTITY,
    ML_TEMP_SOURCE_OPENMETEO,
    ML_TRAINING_LOOKBACK_DAYS,
    OCTOPUS_APIKEY,
    OCTOPUS_METER_SERIAL,
    OCTOPUS_MPN,
)
from .data_pipeline import (
    InsufficientDataError,
    build_training_dataframe,
    detect_ev_blocks,
)
from .model_persistence import load_model, model_age_days, save_model, should_retrain
from .model_trainer import (
    FEATURE_COLUMNS,
    FEATURE_COLUMNS_WITH_OCTOPUS,
    TrainedModel,
    check_model_compatibility,
    compute_blend_weight,
    compute_power_surface,
    predict_correction,
    train_power_model,
)
from .sources import (
    GivEnergyHistorySource,
    OctopusHistorySource,
    OpenMeteoHistorySource,
)

if TYPE_CHECKING:
    from ..power_calculator import PowerCalulator

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# aiohttp session timeouts (seconds)
_SESSION_TIMEOUT_S = 30

# Sentinel for the D-17 EV notification
_EV_NOTIFICATION_ID = "bcc_ev_exclusion"


class MLPowerEstimator:
    """Orchestrates ML power model lifecycle: fetch → clean → train → predict.

    Attributes:
        is_ready (bool): True when a trained model is available.
        state (str): one of disabled|insufficient_data|training|ready|error
    """

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        """Initialise the estimator from the current config entry options."""
        self._hass = hass
        self._config_entry = config_entry

        # Derive frequently-used config values (all optional, default-safe)
        opts = config_entry.options if hasattr(config_entry, "options") else {}
        data = config_entry.data if hasattr(config_entry, "data") else {}

        def _get(key: str, default=None):
            return opts.get(key, data.get(key, default))

        self._consumption_source: str = _get(
            ML_CONSUMPTION_SOURCE, ML_CONSUMPTION_SOURCE_GIVENERGY
        )
        self._temp_source: str = _get(ML_TEMP_SOURCE, ML_TEMP_SOURCE_OPENMETEO)
        self._temp_entity_id: str | None = _get(ML_TEMP_ENTITY_ID)
        self._lookback_days: int = int(
            _get(ML_TRAINING_LOOKBACK_DAYS, DEFAULT_ML_TRAINING_LOOKBACK_DAYS)
        )

        # GivEnergy credentials (required for primary consumption source)
        self._givenergy_token: str = _get(GIVENERGY_API_TOKEN, "")
        self._givenergy_serial: str = _get(GIVENERGY_SERIAL_NUMBER, "")

        # Octopus credentials (required for octopus / both source mode)
        self._octopus_key: str = _get(OCTOPUS_APIKEY, "")
        self._octopus_mpan: str = _get(OCTOPUS_MPN, "")
        self._octopus_meter_serial: str = _get(OCTOPUS_METER_SERIAL, "")

        # State tracking (per spec)
        self._model: TrainedModel | None = None
        self._training_task: asyncio.Task | None = None
        self._training_in_progress: bool = False
        self._ev_stats: dict = {}
        self._last_fetch_error: str | None = None
        self._consumption_source_used: str = self._consumption_source
        self._consumption_fallback: bool = False
        self._temp_source_used: str = self._temp_source
        self._temp_fallback: bool = False
        self.state: str = "disabled"

        # Physics calculator — injected by coordinator after instantiation
        self._physics_calculator: PowerCalulator | None = None

    # ------------------------------------------------------------------
    # Coordinator injection
    # ------------------------------------------------------------------

    def set_physics_calculator(self, pc: "PowerCalulator") -> None:
        """Inject the physics calculator after instantiation.

        Called by the coordinator once PowerCalculator has been constructed.
        Must be called before async_start() triggers training.
        """
        self._physics_calculator = pc

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        """True when a trained model is loaded and available for prediction."""
        return self._model is not None and self.state == "ready"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Load existing model or trigger initial training.

        Called once during coordinator _async_setup(). Runs model load in
        executor; if no model found or model is stale, initiates training
        as a background task (does not block startup).
        """
        config_dir: str = self._hass.config.config_dir

        # Determine which feature columns the current config expects
        current_feature_cols = (
            FEATURE_COLUMNS_WITH_OCTOPUS
            if self._consumption_source == ML_CONSUMPTION_SOURCE_BOTH
            else FEATURE_COLUMNS
        )

        # Load any persisted model in the executor (blocking I/O)
        model: TrainedModel | None = await self._hass.async_add_executor_job(
            load_model, config_dir
        )

        if model is not None:
            # Schema compatibility check (D-18): if source changed, discard
            if not check_model_compatibility(model, current_feature_cols):
                _LOGGER.info(
                    "MLPowerEstimator: loaded model feature schema mismatch "
                    "(source config changed) — will retrain."
                )
                model = None
            else:
                self._model = model
                self.state = "ready"
                _LOGGER.info(
                    "MLPowerEstimator: loaded model from disk "
                    "(trained %s, n=%d, rmse=%.4f)",
                    model.trained_at.isoformat(),
                    model.n_training_samples,
                    model.training_rmse,
                )

        # Trigger background retrain if no valid model or model is stale (D-9)
        if model is None or should_retrain(model):
            await self.async_trigger_retrain()

    async def async_shutdown(self) -> None:
        """Cancel any pending training tasks and close aiohttp session."""
        if self._training_task is not None and not self._training_task.done():
            self._training_task.cancel()
            try:
                await self._training_task
            except (asyncio.CancelledError, Exception):
                pass
            self._training_task = None
        self._training_in_progress = False

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def predict(
        self,
        current_time: datetime,
        outdoor_temp_c: float | None,
        physics_kwh: float,
    ) -> float:
        """Return ML-corrected power estimate for a 30-min slot.

        Computes: ŷ = physics_kwh + blend_weight × δ_ml(features)

        Where features are computed from current_time and outdoor_temp_c,
        and δ_ml is clamped to ±2×training_rmse (D-1 safety clamp).

        Returns physics_kwh unchanged when:
        - Model not ready (cold start, training in progress, error)
        - outdoor_temp_c is None
        - blend_weight is 0.0

        This method is synchronous and fast (< 15 ms). MUST NOT await anything.
        """
        if not self.is_ready or self._model is None:
            return physics_kwh

        if outdoor_temp_c is None or math.isnan(outdoor_temp_c):
            return physics_kwh

        # Re-compute blend weight using current model sample count (D-8 ramp)
        blend_weight = compute_blend_weight(self._model.n_training_samples)
        if blend_weight == 0.0:
            return physics_kwh

        try:
            features = self._build_inference_features(
                current_time, outdoor_temp_c, physics_kwh
            )
            delta = predict_correction(self._model, features)
            correction = float(delta[0])
            return physics_kwh + blend_weight * correction
        except Exception:
            _LOGGER.debug(
                "MLPowerEstimator.predict: inference error — returning physics value",
                exc_info=True,
            )
            return physics_kwh

    # ------------------------------------------------------------------
    # Retrain scheduling
    # ------------------------------------------------------------------

    async def async_trigger_retrain(self) -> None:
        """Schedule a training run in the executor (does not block).

        Called by coordinator on the monthly schedule and RMSE trigger.
        If training is already in progress, this call is a no-op.
        Sets self.state = "training" immediately.
        """
        if self._training_in_progress:
            _LOGGER.debug(
                "MLPowerEstimator: retrain requested but training already in progress"
            )
            return

        self._training_in_progress = True
        self.state = "training"
        self._training_task = self._hass.async_create_task(
            self._async_run_training(),
            name="ml_power_estimator_training",
        )

    # ------------------------------------------------------------------
    # Status sensor
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return all sensor attributes for MLModelStatusSensor (D-11 + D-17).

        Returns dict with all fields from D-11 + D-15 + D-17 extensions.
        """
        model_age: float | None = None
        last_trained: str | None = None
        training_samples: int | None = None
        r2_score: float | None = None
        blend_weight: float = 0.0
        model_type: str | None = None

        if self._model is not None:
            model_age = model_age_days(self._model)
            last_trained = self._model.trained_at.isoformat()
            training_samples = self._model.n_training_samples
            blend_weight = compute_blend_weight(self._model.n_training_samples)
            model_type = self._model.model_type
            r2_score = getattr(self._model, "r2_score", None)

        ev = self._ev_stats
        return {
            # D-11 core fields
            "state": self.state,
            "last_trained": last_trained,
            "training_samples": training_samples,
            "r2_score": r2_score,
            "blend_weight": blend_weight,
            "model_age_days": model_age,
            "model_type": model_type,
            "ml_enabled": True,
            "error_message": self._last_fetch_error if self.state == "error" else None,
            # D-15 source fields
            "consumption_source": self._consumption_source,
            "consumption_source_fallback": self._consumption_fallback,
            "consumption_signal_quality": (
                "partial"
                if self._consumption_fallback
                or self._consumption_source == ML_CONSUMPTION_SOURCE_OCTOPUS
                else "full"
            ),
            "temp_source": self._temp_source_used,
            "temp_source_fallback": self._temp_fallback,
            "last_fetch_error": self._last_fetch_error,
            # D-17 EV detection fields
            "ev_detection_mode": ev.get("ev_detection_mode"),
            "ev_excluded_slots": ev.get("ev_excluded_slots", 0),
            "ev_excluded_fraction": ev.get("ev_excluded_fraction", 0.0),
            "ev_blocks_detected": ev.get("ev_blocks_detected", 0),
            "ev_blocks": ev.get("ev_blocks", []),
        }

    # ------------------------------------------------------------------
    # Private training pipeline
    # ------------------------------------------------------------------

    async def _async_run_training(self) -> None:
        """Full training pipeline. Runs as a background task.

        1. Create aiohttp.ClientSession
        2. Fetch consumption data (GivEnergy primary, Octopus fallback per D-15)
        3. Fetch temperature data (Open-Meteo primary, fallback per D-15)
        4. Build physics Series (use self._build_physics_series() helper)
        5. Run EV detection in executor to capture ev_stats for sensor
        6. Run build_training_dataframe() in executor
        7. Run train_power_model() in executor
        8. Run save_model() in executor
        9. Update self._model, self.state, self._ev_stats
        10. Raise HA persistent notification if EV blocks detected (D-17)
        11. On InsufficientDataError: set state = "insufficient_data", log info
        12. On any other Exception: set state = "error", log error, do NOT re-raise
        """
        try:
            await self._run_training_pipeline()
        except InsufficientDataError as exc:
            self.state = "insufficient_data"
            self._last_fetch_error = str(exc)
            _LOGGER.info(
                "MLPowerEstimator: insufficient training data — %s. "
                "Will retry at next scheduled interval.",
                exc,
            )
        except Exception as exc:
            self.state = "error"
            self._last_fetch_error = str(exc)
            _LOGGER.error(
                "MLPowerEstimator: training failed — %s",
                exc,
                exc_info=True,
            )
        finally:
            self._training_in_progress = False
            self._training_task = None

    async def _run_training_pipeline(self) -> None:
        """Inner training pipeline — exceptions propagate to _async_run_training."""
        # ------------------------------------------------------------------
        # Step 1: Determine date range
        # ------------------------------------------------------------------
        end_dt = datetime.now(tz=UTC)
        start_dt = end_dt - timedelta(days=self._lookback_days)

        # ------------------------------------------------------------------
        # Step 2 + 3: Fetch data using a short-lived aiohttp session
        # ------------------------------------------------------------------
        timeout = aiohttp.ClientTimeout(total=_SESSION_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            power_series, octopus_series = await self._fetch_consumption(
                session, start_dt, end_dt
            )
            temp_series = await self._fetch_temperature(session, start_dt, end_dt)

        # Both consumption and temperature must be present to proceed
        if power_series is None or len(power_series) == 0:
            raise InsufficientDataError(
                "No consumption data available — all sources failed"
            )
        if temp_series is None or len(temp_series) == 0:
            _LOGGER.warning(
                "MLPowerEstimator: no temperature data — proceeding with NaN temp "
                "(HistGBR handles natively)"
            )
            # Build an empty series aligned to power_series index but with NaN
            temp_series = pd.Series(
                np.nan, index=power_series.index, dtype="float64", name="temperature_c"
            )

        # ------------------------------------------------------------------
        # Step 4: Build physics Series in executor
        # ------------------------------------------------------------------
        physics_series: pd.Series | None = None
        if self._physics_calculator is not None:
            physics_series = await self._hass.async_add_executor_job(
                self._build_physics_series, start_dt, end_dt, temp_series
            )

        # ------------------------------------------------------------------
        # Step 5: Capture EV stats in executor (pre-pipeline)
        # ------------------------------------------------------------------
        ev_stats = await self._hass.async_add_executor_job(
            self._compute_ev_stats_sync,
            power_series,
            physics_series,
            temp_series,
        )

        # ------------------------------------------------------------------
        # Step 6: Build training DataFrame in executor
        # ------------------------------------------------------------------
        include_octopus_feature = (
            self._consumption_source == ML_CONSUMPTION_SOURCE_BOTH
            and octopus_series is not None
            and len(octopus_series) > 0
        )
        df: pd.DataFrame = await self._hass.async_add_executor_job(
            build_training_dataframe,
            power_series,
            temp_series,
            physics_series,
            octopus_series,
            include_octopus_feature,
        )

        # ------------------------------------------------------------------
        # Step 7: Train model in executor
        # ------------------------------------------------------------------
        model: TrainedModel = await self._hass.async_add_executor_job(
            train_power_model, df
        )

        # ------------------------------------------------------------------
        # Step 7b: Compute 3-D power surface in executor (for MLPowerSurfaceSensor)
        # ------------------------------------------------------------------
        if self._physics_calculator is not None:
            try:
                surface = await self._hass.async_add_executor_job(
                    compute_power_surface, model, self._physics_calculator
                )
                model.power_surface = surface
            except Exception:
                _LOGGER.warning(
                    "MLPowerEstimator: power surface computation failed — "
                    "surface sensor will be unavailable",
                    exc_info=True,
                )

        # ------------------------------------------------------------------
        # Step 8: Save model atomically in executor
        # ------------------------------------------------------------------
        config_dir: str = self._hass.config.config_dir
        await self._hass.async_add_executor_job(save_model, model, config_dir)

        # ------------------------------------------------------------------
        # Step 9: Update instance state
        # ------------------------------------------------------------------
        self._model = model
        self._ev_stats = ev_stats
        self.state = "ready"
        self._last_fetch_error = None

        _LOGGER.info(
            "MLPowerEstimator: training complete — model=%s n=%d rmse=%.4f "
            "w_ml=%.3f consumption_source=%s consumption_fallback=%s",
            model.model_type,
            model.n_training_samples,
            model.training_rmse,
            compute_blend_weight(model.n_training_samples),
            self._consumption_source_used,
            self._consumption_fallback,
        )

        # ------------------------------------------------------------------
        # Step 10: HA persistent notification for EV blocks (D-17)
        # ------------------------------------------------------------------
        n_blocks = ev_stats.get("ev_blocks_detected", 0)
        n_slots = ev_stats.get("ev_excluded_slots", 0)
        if n_blocks >= 1:
            await self._hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Battery Charge Calculator — ML Training",
                    "message": (
                        f"Excluded {n_slots} slots across {n_blocks} large-load "
                        f"blocks from ML training data. These are likely EV charging "
                        f"sessions. See the ML Model Status sensor for details."
                    ),
                    "notification_id": _EV_NOTIFICATION_ID,
                },
            )

    # ------------------------------------------------------------------
    # Data fetching helpers
    # ------------------------------------------------------------------

    async def _fetch_consumption(
        self,
        session: aiohttp.ClientSession,
        start: datetime,
        end: datetime,
    ) -> tuple[pd.Series | None, pd.Series | None]:
        """Fetch consumption series with fallback logic (D-15).

        Returns (power_series, octopus_series). Either may be None on failure.
        power_series is the primary consumption target (kWh/slot).
        octopus_series is the Octopus import feature (only for "both" mode).
        """
        octopus_series: pd.Series | None = None

        # Reset source tracking
        self._consumption_source_used = self._consumption_source
        self._consumption_fallback = False

        if self._consumption_source == ML_CONSUMPTION_SOURCE_OCTOPUS:
            # Octopus-only mode
            source = OctopusHistorySource(
                self._octopus_key, self._octopus_mpan, self._octopus_meter_serial
            )
            power_series = await source.fetch(session, start, end)
            if power_series is None or len(power_series) == 0:
                self._last_fetch_error = "Octopus consumption fetch failed"
                _LOGGER.warning("MLPowerEstimator: Octopus consumption fetch failed")
            return power_series, None

        if self._consumption_source == ML_CONSUMPTION_SOURCE_BOTH:
            # GivEnergy is target; Octopus is feature column
            giv_source = GivEnergyHistorySource(
                self._givenergy_token, self._givenergy_serial
            )
            power_series = await giv_source.fetch(session, start, end)

            if power_series is None or len(power_series) == 0:
                _LOGGER.warning(
                    "MLPowerEstimator: GivEnergy failed in 'both' mode — "
                    "trying Octopus as primary target"
                )
                # Attempt Octopus fallback for the primary target
                oct_source = OctopusHistorySource(
                    self._octopus_key, self._octopus_mpan, self._octopus_meter_serial
                )
                power_series = await oct_source.fetch(session, start, end)
                self._consumption_fallback = True
                self._consumption_source_used = ML_CONSUMPTION_SOURCE_OCTOPUS
                self._last_fetch_error = (
                    "GivEnergy failed; fell back to Octopus (partial signal)"
                )
                # Cannot include octopus as a feature if it is the primary target
                return power_series, None

            # GivEnergy succeeded — also fetch Octopus as feature
            oct_source = OctopusHistorySource(
                self._octopus_key, self._octopus_mpan, self._octopus_meter_serial
            )
            octopus_series = await oct_source.fetch(session, start, end)
            if octopus_series is None or len(octopus_series) == 0:
                _LOGGER.warning(
                    "MLPowerEstimator: Octopus feature fetch failed in 'both' mode — "
                    "training on GivEnergy only (signal quality: partial)"
                )
                self._last_fetch_error = (
                    "Octopus feature fetch failed; training with GivEnergy only"
                )
            return power_series, octopus_series

        # Default: GivEnergy primary with Octopus fallback
        giv_source = GivEnergyHistorySource(
            self._givenergy_token, self._givenergy_serial
        )
        power_series = await giv_source.fetch(session, start, end)

        if power_series is not None and len(power_series) > 0:
            self._consumption_source_used = ML_CONSUMPTION_SOURCE_GIVENERGY
            self._consumption_fallback = False
            return power_series, None

        # GivEnergy failed — try Octopus as fallback
        _LOGGER.warning(
            "MLPowerEstimator: GivEnergy consumption fetch failed — "
            "trying Octopus fallback (partial signal: grid import only)"
        )
        self._consumption_fallback = True
        self._consumption_source_used = ML_CONSUMPTION_SOURCE_OCTOPUS

        oct_source = OctopusHistorySource(
            self._octopus_key, self._octopus_mpan, self._octopus_meter_serial
        )
        power_series = await oct_source.fetch(session, start, end)

        if power_series is None or len(power_series) == 0:
            self._last_fetch_error = (
                "Both GivEnergy and Octopus consumption fetches failed"
            )
            _LOGGER.error("MLPowerEstimator: all consumption sources failed")
        else:
            self._last_fetch_error = (
                "GivEnergy failed; trained on Octopus import data (partial signal)"
            )
            # Raise a HA persistent notification for partial-signal fallback
            self._hass.async_create_task(
                self._hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "title": "Battery Charge Calculator — ML Training",
                        "message": (
                            "GivEnergy data unavailable. ML model trained on Octopus "
                            "grid-import data only (solar self-consumption not captured). "
                            "Accuracy may be reduced on sunny days."
                        ),
                        "notification_id": "bcc_partial_consumption_signal",
                    },
                )
            )

        return power_series, None

    async def _fetch_temperature(
        self,
        session: aiohttp.ClientSession,  # noqa: ARG002
        start: datetime,
        end: datetime,
    ) -> pd.Series | None:
        """Fetch temperature series with fallback logic (D-15).

        Primary: Open-Meteo archive API.
        Fallback: HA Recorder entity (ML_TEMP_ENTITY_ID).
        If both fail: returns None; training proceeds with NaN temp (HistGBR handles).
        """
        self._temp_source_used = self._temp_source
        self._temp_fallback = False

        if self._temp_source == ML_TEMP_SOURCE_HA_ENTITY:
            # User explicitly chose HA entity — no Open-Meteo attempt
            temp_series = await self._fetch_temp_from_recorder(start, end)
            if temp_series is not None and len(temp_series) > 0:
                return temp_series
            self._last_fetch_error = "HA temperature entity fetch failed"
            return None

        # Open-Meteo primary
        lat = self._hass.config.latitude
        lon = self._hass.config.longitude
        openmeteo_timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=openmeteo_timeout) as meteo_session:
            source = OpenMeteoHistorySource(lat, lon)
            temp_series = await source.fetch(meteo_session, start, end)

        if temp_series is not None and len(temp_series) > 0:
            self._temp_source_used = ML_TEMP_SOURCE_OPENMETEO
            return temp_series

        # Open-Meteo failed — try HA entity fallback
        _LOGGER.warning(
            "MLPowerEstimator: Open-Meteo temperature fetch failed — "
            "trying HA entity fallback"
        )
        self._temp_fallback = True
        self._temp_source_used = ML_TEMP_SOURCE_HA_ENTITY

        if self._temp_entity_id:
            temp_series = await self._fetch_temp_from_recorder(start, end)
            if temp_series is not None and len(temp_series) > 0:
                return temp_series
            _LOGGER.warning(
                "MLPowerEstimator: HA temperature entity '%s' fallback failed — "
                "training will proceed with NaN temperature",
                self._temp_entity_id,
            )
        else:
            _LOGGER.warning(
                "MLPowerEstimator: Open-Meteo failed and no ML_TEMP_ENTITY_ID "
                "configured — training will proceed with NaN temperature"
            )

        self._temp_source_used = "imputed"
        return None

    async def _fetch_temp_from_recorder(
        self,
        start: datetime,
        end: datetime,
    ) -> pd.Series | None:
        """Fetch temperature from the HA Recorder (blocking I/O in executor).

        Returns a 30-min resampled Series or None on failure.
        """
        entity_id = self._temp_entity_id
        if not entity_id:
            return None

        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import get_significant_states

            states = await get_instance(self._hass).async_add_executor_job(
                get_significant_states,
                self._hass,
                start,
                end,
                [entity_id],
                None,  # filters
                False,  # include_start_time_state
                False,  # significant_changes_only — capture all transitions
                False,  # minimal_response
                False,  # no_attributes
            )
        except Exception as exc:
            _LOGGER.warning(
                "MLPowerEstimator: HA Recorder query for '%s' failed: %s",
                entity_id,
                exc,
            )
            return None

        entity_states = states.get(entity_id, [])
        if not entity_states:
            return None

        records: list[tuple[datetime, float]] = []
        for state in entity_states:
            if state.state in ("unavailable", "unknown"):
                continue
            try:
                records.append((state.last_changed, float(state.state)))
            except (ValueError, TypeError):
                continue

        if not records:
            return None

        times, values = zip(*records)
        series = pd.Series(
            list(values),
            index=pd.DatetimeIndex(list(times), name="time"),
            dtype="float64",
            name="temperature_c",
        )
        # Ensure UTC
        if series.index.tz is None:
            series.index = series.index.tz_localize("UTC")
        else:
            series.index = series.index.tz_convert("UTC")

        # Resample to 30-min grid
        return series.resample("30min").mean()

    # ------------------------------------------------------------------
    # Physics series builder (called in executor)
    # ------------------------------------------------------------------

    def _build_physics_series(
        self,
        start: datetime,  # noqa: ARG002
        end: datetime,  # noqa: ARG002
        temp_series: pd.Series,
    ) -> pd.Series:
        """Build a Series of physics-based estimates for each 30-min slot.

        Uses self._physics_calculator.from_temp_and_time() for each slot in
        temp_series.index. Returns aligned pd.Series.
        This gives the data pipeline the physics baseline it needs for residual
        computation and EV detection. Called in executor.
        """
        if self._physics_calculator is None:
            return pd.Series(dtype="float64", name="physics_kwh")

        values: list[float] = []
        for ts in temp_series.index:
            # Convert pandas Timestamp to Python datetime for PowerCalulator
            dt: datetime = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            temp_c = temp_series.loc[ts]
            if pd.isna(temp_c):
                physics_val = self._physics_calculator.from_temp_and_time(dt, None)
            else:
                physics_val = self._physics_calculator.from_temp_and_time(
                    dt, float(temp_c)
                )
            values.append(float(physics_val))

        return pd.Series(
            values,
            index=temp_series.index,
            dtype="float64",
            name="physics_kwh",
        )

    # ------------------------------------------------------------------
    # EV stats computation (called in executor before full pipeline)
    # ------------------------------------------------------------------

    def _compute_ev_stats_sync(
        self,
        power_series: pd.Series,
        physics_series: pd.Series | None,
        temp_series: pd.Series | None,
    ) -> dict:
        """Compute EV exclusion statistics from raw series for the status sensor.

        Resamples series to a common 30-min UTC grid, aligns them, then
        calls detect_ev_blocks() to capture the ev_stats that build_training_dataframe
        uses internally. Called in executor; pure Python (no async).

        Returns a dict matching the D-17 ev_stats schema.
        """
        try:

            def _to_utc_30min(s: pd.Series) -> pd.Series:
                s = s.copy()
                if not isinstance(s.index, pd.DatetimeIndex):
                    s.index = pd.to_datetime(s.index)
                if s.index.tz is None:
                    s.index = s.index.tz_localize("UTC")
                elif str(s.index.tz) != "UTC":
                    s.index = s.index.tz_convert("UTC")
                return s.resample("30min").mean()

            power_30 = _to_utc_30min(power_series)
            phys_30 = (
                _to_utc_30min(physics_series) if physics_series is not None else None
            )
            temp_30 = _to_utc_30min(temp_series) if temp_series is not None else None

            # Align on common timestamps
            common_idx = power_30.index
            if phys_30 is not None:
                common_idx = common_idx.intersection(phys_30.index)
                power_30 = power_30.loc[common_idx]
                phys_30 = phys_30.loc[common_idx]
            if temp_30 is not None:
                common_idx = power_30.index.intersection(temp_30.index)
                power_30 = power_30.loc[common_idx]
                if phys_30 is not None:
                    phys_30 = phys_30.loc[common_idx]
                temp_30 = temp_30.loc[common_idx]

            ev_mask, ev_blocks = detect_ev_blocks(power_30, phys_30, temp_30)

            # Infer detection mode label
            if phys_30 is not None and temp_30 is not None:
                mode = "residual_iqr"
            elif temp_30 is not None:
                mode = "proxy_physics_cold_start"
            else:
                mode = "temporal_cv_fallback"

            return {
                "ev_detection_mode": mode,
                "ev_excluded_slots": int(ev_mask.sum()),
                "ev_excluded_fraction": float(ev_mask.mean())
                if len(ev_mask) > 0
                else 0.0,
                "ev_blocks_detected": len(ev_blocks),
                "ev_blocks": ev_blocks[:20],
            }
        except Exception as exc:
            _LOGGER.debug(
                "MLPowerEstimator: EV stats computation failed (non-critical): %s", exc
            )
            return {
                "ev_detection_mode": None,
                "ev_excluded_slots": 0,
                "ev_excluded_fraction": 0.0,
                "ev_blocks_detected": 0,
                "ev_blocks": [],
            }

    # ------------------------------------------------------------------
    # Inference feature builder (called synchronously from predict())
    # ------------------------------------------------------------------

    def _build_inference_features(
        self,
        current_time: datetime,
        outdoor_temp_c: float,
        physics_kwh: float,
    ) -> pd.DataFrame:
        """Build a single-row feature DataFrame for predict().

        Computes same time-based features as data_pipeline._add_features() for
        a single slot. Rolling/delta features (temp_delta_1slot, temp_delta_24h,
        rolling_mean_6h) are set to NaN — HistGBR handles them natively; Ridge
        pipeline uses mean imputation.

        For octopus_import_kwh: always NaN at inference (D-18 revised spec).
        Returns single-row DataFrame with columns matching model.feature_columns.
        """
        # Ensure UTC-aware for consistent slot_index computation
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)

        hour = current_time.hour
        minute = current_time.minute
        slot_index = hour * 2 + (1 if minute >= 30 else 0)
        doy = current_time.timetuple().tm_yday
        dow = current_time.weekday()  # 0 = Monday, 6 = Sunday

        row: dict[str, float] = {
            "outdoor_temp_c": outdoor_temp_c,
            "physics_kwh": physics_kwh,
            "hour_sin": math.sin(2 * math.pi * hour / 24),
            "hour_cos": math.cos(2 * math.pi * hour / 24),
            "dow_sin": math.sin(2 * math.pi * dow / 7),
            "dow_cos": math.cos(2 * math.pi * dow / 7),
            "doy_sin": math.sin(2 * math.pi * doy / 365),
            "doy_cos": math.cos(2 * math.pi * doy / 365),
            "is_weekend": float(dow >= 5),
            "slot_index": float(slot_index),
            # Rolling / lag features — NaN; handled by HistGBR natively or Ridge imputer
            "temp_delta_1slot": float("nan"),
            "temp_delta_24h": float("nan"),
            "rolling_mean_6h": float("nan"),
            "temp_delta_1slot_sq": float("nan"),
            "physics_kwh_sq": physics_kwh**2,
        }

        assert self._model is not None  # guarded by is_ready check in predict()
        feature_cols = self._model.feature_columns

        if "octopus_import_kwh" in feature_cols:
            # D-18: octopus import is always NaN at inference (GivEnergy-only signal)
            row["octopus_import_kwh"] = float("nan")

        return pd.DataFrame([row], columns=feature_cols)
