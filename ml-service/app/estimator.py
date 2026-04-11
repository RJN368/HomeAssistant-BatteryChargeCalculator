"""BCC ML Service estimator.

A self-contained rewrite of the HA integration's ``MLPowerEstimator`` with all
home-assistant dependencies removed.  Key differences:

- Configuration arrives via ``configure()`` instead of HA config entries.
- ``predict_batch()`` accepts a list of slots, returning corrected kWh values.
- ``asyncio.get_event_loop().run_in_executor`` replaces ``hass.async_add_executor_job``.
- ``asyncio.create_task`` replaces ``hass.async_create_task``.
- Temperature source is always Open-Meteo (no HA entity fallback).
- Model persistence uses the fixed ``/data/model.pkl`` Docker volume path.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
import pandas as pd

from .ml import (
    build_training_dataframe,
    compute_power_surface,
    PowerCalulator,
    TrainedModel,
    load_model,
    needs_retrain,
    save_model,
    train_power_model,
)
from .ml.sources import (
    GivEnergyHistorySource,
    OctopusHistorySource,
    OpenMeteoHistorySource,
)

_LOGGER = logging.getLogger(__name__)

UTC = timezone.utc

# Minimum samples before switching from Ridge to HistGBR
_MIN_SAMPLES_HISTGBR = 500

# Blend weight ramp: climb from 0→1 over this many samples
_BLEND_RAMP_SAMPLES = 200


class BccMlEstimator:
    """Service-side ML power estimator.

    Lifecycle:
        estimator = BccMlEstimator()
        estimator.configure(config_dict)   # call after POST /configure
        await estimator.async_start()      # loads model, schedules training if needed
        corrections = await estimator.predict_batch(slots)
    """

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._model: TrainedModel | None = None
        self._physics_calculator: PowerCalulator | None = None
        self._training_lock = asyncio.Lock()
        self._is_training = False
        self._state = "not_configured"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(self, config: dict[str, Any]) -> None:
        """Store credentials and physics parameters.  Safe to call multiple times."""
        self._config = config
        self._physics_calculator = self._build_physics_calculator(config)
        if self._state not in ("training", "ready"):
            self._state = "configured"
        # Log sanitised config (exclude secrets) so we can verify HA is sending everything
        _LOGGER.info(
            "BccMlEstimator.configure() called — keys=%s, "
            "consumption_source=%s, lat=%.4f, lon=%.4f, "
            "givenergy_serial=%s, octopus_mpan=%s",
            list(config.keys()),
            config.get("consumption_source"),
            config.get("latitude", 0.0),
            config.get("longitude", 0.0),
            bool(config.get("givenergy_inverter_serial")),
            bool(config.get("octopus_mpan")),
        )

    def _build_physics_calculator(
        self, config: dict[str, Any]
    ) -> PowerCalulator | None:
        try:
            heating_type = config.get("heating_type", "none")
            if heating_type == "none":
                return None
            return PowerCalulator(
                heating_type=heating_type,
                cop=float(config.get("cop", 3.0)),
                heat_loss=float(config.get("heat_loss_w_per_k", 100.0)),
                indoor_temp=float(config.get("indoor_temp_c", 20.0)),
                heating_flow_temp=float(config.get("heating_flow_temp_c", 45.0)),
                known_points=config.get("known_points", None),
                base_load_kwh_30min=config.get("base_load_profile", None),
            )
        except Exception as exc:
            _LOGGER.warning("Failed to build PowerCalulator: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Load persisted model and schedule initial training if needed."""
        loop = asyncio.get_event_loop()
        self._model = await loop.run_in_executor(None, load_model)

        if self._model is not None:
            self._state = "ready"
            _LOGGER.info(
                "BccMlEstimator: loaded model from disk "
                "(trained_at=%s, n_training_samples=%d, training_rmse=%.4f)",
                self._model.trained_at,
                self._model.n_training_samples,
                self._model.training_rmse or 0.0,
            )
            # Migrate old models that were saved without a power_surface.
            # Compute it now so the sensor has data immediately on startup.
            if not self._model.power_surface:
                _LOGGER.info(
                    "BccMlEstimator: model has no power_surface — computing now"
                )
                try:
                    self._model.power_surface = await loop.run_in_executor(
                        None,
                        compute_power_surface,
                        self._model,
                        self._physics_calculator,
                    )
                    await loop.run_in_executor(None, save_model, self._model)
                    _LOGGER.info("BccMlEstimator: power surface computed and saved")
                except Exception as exc:
                    _LOGGER.warning(
                        "BccMlEstimator: post-load power surface computation failed: %s",
                        exc,
                    )
        else:
            self._state = "configured"

        if needs_retrain(self._model):
            _LOGGER.info("BccMlEstimator: scheduling initial training")
            asyncio.create_task(self._run_training_pipeline())

    # ------------------------------------------------------------------
    # Prediction (batch)
    # ------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        """True when a trained model is in memory."""
        return self._model is not None

    async def predict_batch(
        self,
        slots: list[dict[str, Any]],
    ) -> list[float]:
        """Return ML-corrected kWh values for a list of planning slots.

        Each ``slot`` dict must contain:
            - ``slot_time``: ISO-8601 string (UTC preferred)
            - ``temp_c``: outdoor temperature in °C (can be null/None)
            - ``physics_kwh``: physics-based estimate for the slot

        Returns a list of corrected kWh values, one per slot.  Falls back to
        physics_kwh for any slot where ML inference fails.
        """
        if self._model is None:
            return [float(s.get("physics_kwh", 0.0)) for s in slots]

        results: list[float] = []
        for slot in slots:
            try:
                current_time = _parse_dt(slot["slot_time"])
                temp_c = slot.get("temp_c")
                temp_c = float(temp_c) if temp_c is not None else float("nan")
                physics_kwh = float(slot.get("physics_kwh", 0.0))
                corrected = self._predict_single(current_time, temp_c, physics_kwh)
                results.append(corrected)
            except Exception as exc:
                _LOGGER.debug("predict_batch: slot error %s", exc)
                results.append(float(slot.get("physics_kwh", 0.0)))
        return results

    def _predict_single(
        self,
        current_time: datetime,
        outdoor_temp_c: float,
        physics_kwh: float,
    ) -> float:
        """Single-slot ML inference with blend weight and residual correction."""
        model = self._model
        assert model is not None  # guarded by is_ready check

        features = self._build_inference_features(
            current_time, outdoor_temp_c, physics_kwh
        )

        # Primary model prediction
        primary_pred = float(model.primary_model.predict(features)[0])  # type: ignore[union-attr]

        # Blend weight ramp: 0 at _MIN_SAMPLES_HISTGBR, 1 at +_BLEND_RAMP_SAMPLES
        n = model.n_training_samples
        if n < _MIN_SAMPLES_HISTGBR:
            blend_w = 0.0
        elif n < _MIN_SAMPLES_HISTGBR + _BLEND_RAMP_SAMPLES:
            blend_w = (n - _MIN_SAMPLES_HISTGBR) / _BLEND_RAMP_SAMPLES
        else:
            blend_w = 1.0

        blended = blend_w * primary_pred + (1.0 - blend_w) * physics_kwh

        # Residual correction model
        if model.residual_model is not None:
            try:
                residual = float(model.residual_model.predict(features)[0])  # type: ignore[union-attr]
                blended += residual
            except Exception:
                pass

        return max(0.0, blended)

    def _build_inference_features(
        self,
        current_time: datetime,
        outdoor_temp_c: float,
        physics_kwh: float,
    ) -> pd.DataFrame:
        """Build single-row feature DataFrame matching model.feature_columns."""
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)

        hour = current_time.hour
        minute = current_time.minute
        slot_index = hour * 2 + (1 if minute >= 30 else 0)
        doy = current_time.timetuple().tm_yday
        dow = current_time.weekday()

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
            "temp_delta_1slot": float("nan"),
            "temp_delta_24h": float("nan"),
            "rolling_mean_6h": float("nan"),
            "temp_delta_1slot_sq": float("nan"),
            "physics_kwh_sq": physics_kwh**2,
        }

        assert self._model is not None
        feature_cols = self._model.feature_columns
        if "octopus_import_kwh" in feature_cols:
            row["octopus_import_kwh"] = float("nan")

        return pd.DataFrame([row], columns=feature_cols)

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    async def trigger_retrain(self) -> None:
        """Trigger a background training run (no-op if already training)."""
        if self._is_training:
            _LOGGER.info("BccMlEstimator: training already in progress, skipping")
            return
        asyncio.create_task(self._run_training_pipeline())

    async def _run_training_pipeline(self) -> None:
        """Full async training pipeline, executed in the background."""
        if self._state == "not_configured":
            _LOGGER.warning("BccMlEstimator: not configured, cannot train")
            return

        async with self._training_lock:
            self._is_training = True
            self._state = "training"
            _LOGGER.info("BccMlEstimator: training pipeline started")
            try:
                await self._do_training()
            except Exception as exc:
                _LOGGER.error(
                    "BccMlEstimator: training pipeline failed: %s", exc, exc_info=True
                )
                self._state = "training_failed"
            finally:
                self._is_training = False

    async def _do_training(self) -> None:
        config = self._config
        lookback_days: int = int(config.get("training_lookback_days", 90))

        end_dt = datetime.now(UTC)
        start_dt = end_dt - timedelta(days=lookback_days)

        # -- Fetch raw series from each source --------------------------------
        givenergy_api_key: str = config.get("givenergy_api_key", "")
        givenergy_inverter_serial: str = config.get("givenergy_inverter_serial", "")
        octopus_api_key: str = config.get("octopus_api_key", "")
        octopus_account_id: str = config.get("octopus_account_id", "")
        octopus_mpan: str = config.get("octopus_mpan", "")
        octopus_meter_serial: str = config.get("octopus_meter_serial", "")
        consumption_source: str = config.get("consumption_source", "givenergy")

        raw_series: dict[str, Any] = {}

        async with aiohttp.ClientSession() as session:
            # Temperature (always Open-Meteo)
            temp_src = OpenMeteoHistorySource(
                latitude=config.get("latitude", 51.5),
                longitude=config.get("longitude", -0.1),
            )
            temp_data = await temp_src.fetch(session, start_dt, end_dt)
            if temp_data is not None and not temp_data.empty:
                raw_series["outdoor_temp_c"] = temp_data

            # Consumption
            if (
                consumption_source in ("givenergy", "both")
                and givenergy_api_key
                and givenergy_inverter_serial
            ):
                cons_src = GivEnergyHistorySource(
                    api_token=givenergy_api_key,
                    serial_number=givenergy_inverter_serial,
                )
                cons_data = await cons_src.fetch(session, start_dt, end_dt)
                if cons_data is not None and not cons_data.empty:
                    raw_series["consumption_kwh"] = cons_data
            if (
                consumption_source in ("octopus", "both")
                and octopus_mpan
                and octopus_meter_serial
            ):
                cons_src = OctopusHistorySource(
                    api_key=octopus_api_key,
                    mpan=octopus_mpan,
                    meter_serial=octopus_meter_serial,
                )
                cons_data = await cons_src.fetch(session, start_dt, end_dt)
                if cons_data is not None and not cons_data.empty:
                    raw_series["octopus_import_kwh"] = cons_data

        consumption_kwh = raw_series.get("consumption_kwh")
        octopus_import_kwh = raw_series.get("octopus_import_kwh")
        if (consumption_kwh is None or consumption_kwh.empty) and (
            octopus_import_kwh is None or octopus_import_kwh.empty
        ):
            _LOGGER.warning(
                "BccMlEstimator: no consumption data fetched "
                "(source=%s, givenergy_key=%s, givenergy_serial=%s, "
                "octopus_mpan=%s, octopus_serial=%s)",
                consumption_source,
                bool(givenergy_api_key),
                bool(givenergy_inverter_serial),
                bool(octopus_mpan),
                bool(octopus_meter_serial),
            )
            self._state = "no_data"
            return

        # Build physics series in executor ------------------------------------
        loop = asyncio.get_event_loop()
        if self._physics_calculator is not None:
            temp_series: pd.Series | None = raw_series.get("outdoor_temp_c")
            if temp_series is not None and not temp_series.empty:
                physics_series = await loop.run_in_executor(
                    None,
                    self._build_physics_series_sync,
                    temp_series,
                )
                raw_series["physics_kwh"] = physics_series

        # Build training DataFrame + train model (in executor) ----------------
        _LOGGER.info("BccMlEstimator: starting model training in executor")
        trained_model: TrainedModel = await loop.run_in_executor(
            None,
            _train_from_raw_series,
            raw_series,
        )
        _LOGGER.info("BccMlEstimator: model training complete, saving to disk")

        # Compute power surface (sync, in executor) ---------------------------
        try:
            trained_model.power_surface = await loop.run_in_executor(
                None,
                compute_power_surface,
                trained_model,
                self._physics_calculator,  # None is OK — uses physics_kwh=0.0
            )
            _LOGGER.info("BccMlEstimator: power surface computed")
        except Exception as exc:
            _LOGGER.warning("BccMlEstimator: power surface computation failed: %s", exc)

        # Persist -------------------------------------------------------------
        await loop.run_in_executor(None, save_model, trained_model)
        self._model = trained_model
        self._state = "ready"
        _LOGGER.info(
            "BccMlEstimator: training complete (n_training_samples=%d, training_rmse=%.4f)",
            trained_model.n_training_samples,
            trained_model.training_rmse or 0.0,
        )

    def _build_physics_series_sync(self, temp_series: pd.Series) -> pd.Series:
        """Build physics kWh series aligned to temp_series (executor-safe)."""
        if self._physics_calculator is None:
            return pd.Series(dtype="float64", name="physics_kwh")

        values: list[float] = []
        for ts in temp_series.index:
            dt: datetime = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            temp_c = temp_series.loc[ts]
            if pd.isna(temp_c):
                val = self._physics_calculator.from_temp_and_time(dt, None)
            else:
                val = self._physics_calculator.from_temp_and_time(dt, float(temp_c))
            values.append(float(val))

        return pd.Series(
            values, index=temp_series.index, dtype="float64", name="physics_kwh"
        )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return a full status dict for GET /status."""
        model = self._model
        n = model.n_training_samples if model else 0
        # Blend weight: ramp from 0 at _MIN_SAMPLES_HISTGBR to 1 over _BLEND_RAMP_SAMPLES
        if n < _MIN_SAMPLES_HISTGBR:
            blend_w = 0.0
        elif n < _MIN_SAMPLES_HISTGBR + _BLEND_RAMP_SAMPLES:
            blend_w = (n - _MIN_SAMPLES_HISTGBR) / _BLEND_RAMP_SAMPLES
        else:
            blend_w = 1.0
        return {
            "state": self._state,
            "is_ready": self.is_ready,
            "is_training": self._is_training,
            "model_trained_at": model.trained_at.isoformat() if model else None,
            "model_n_training_samples": model.n_training_samples if model else None,
            "model_val_rmse": model.training_rmse if model else None,
            "model_type": model.model_type if model else None,
            "blend_weight": round(blend_w, 3),
            "doy_daily_kwh": model.doy_daily_kwh if model else None,
            "power_surface": _serialise_power_surface(model) if model else None,
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

estimator = BccMlEstimator()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _train_from_raw_series(raw_series: dict) -> TrainedModel:
    """Build training DataFrame and train the model (executor-safe, sync)."""
    consumption = raw_series.get("consumption_kwh")
    temp = raw_series.get("outdoor_temp_c")
    physics = raw_series.get("physics_kwh")
    octopus = raw_series.get("octopus_import_kwh")

    # When consumption came from Octopus, use it as the primary power series
    if consumption is None and octopus is not None:
        consumption = octopus
        octopus = None

    df = build_training_dataframe(
        power_series=consumption,
        temp_series=temp,
        physics_series=physics,
        octopus_series=octopus,
        include_octopus_feature=(octopus is not None),
    )
    return train_power_model(df)


def _parse_dt(value: str) -> datetime:
    """Parse ISO-8601 string into UTC-aware datetime."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _serialise_power_surface(model: TrainedModel) -> dict[str, Any] | None:
    """Return the power_surface dict, already JSON-serialisable."""
    ps = getattr(model, "power_surface", None)
    if not ps:
        return None
    # compute_power_surface already returns a plain dict with lists
    if isinstance(ps, dict) and ps.get("z"):
        return ps
    # Legacy dataclass path (shouldn't occur after migration)
    try:
        return {
            "temps": list(ps.temps),
            "weeks": list(ps.weeks),
            "z": [list(row) for row in ps.z],
            "z_physics": [list(row) for row in ps.z_physics]
            if ps.z_physics is not None
            else None,
        }
    except Exception:
        return None
