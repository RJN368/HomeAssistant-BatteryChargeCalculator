"""ML model training and prediction for the BatteryChargeCalculator integration.

This module implements the residual-correction ML model described in D-1 and D-6.

The model learns the per-slot residual δ = actual_kwh − physics_kwh and applies an
additive blend at inference time:

    ŷ(T, t) = ŷ_physics(T, t) + w_ML · δ̂_ML(x)          (D-1)

Model selection (D-6):
    • N_clean ≥ 500: HistGradientBoostingRegressor (primary)
      — native NaN handling is critical when Open-Meteo or GivEnergy data is missing.
    • N_clean < 500: Ridge(alpha=10) wrapped in a SimpleImputer pipeline (cold-start).

Blend weight (D-8):
    w_ML ramps linearly 0.0 → 1.0 as N_clean goes from 500 → 2500.  Below the lower
    bound the output is pure physics; above the upper bound the ML correction receives
    full weight.

Correction capping (D-1):
    The predicted residual is clamped to ±(2 × training_RMSE) to prevent wild
    extrapolation on unseen feature combinations.

This module has NO homeassistant imports. It is pure Python + scikit-learn + numpy.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# D-6 model hyperparameters
# ---------------------------------------------------------------------------
_HIST_GBR_MAX_ITER = 100  # caps model file size ≈ 2–4 MB
_HIST_GBR_MAX_DEPTH = 4  # prevents overfitting on small datasets
_HIST_GBR_LEARNING_RATE = 0.05  # conservative shrinkage
_HIST_GBR_MIN_SAMPLES_LEAF = 20  # critical when data is sparse
_HIST_GBR_L2_REG = 1.0  # regularises leaf weights
_HIST_GBR_VALIDATION_FRACTION = 0.15
_HIST_GBR_N_ITER_NO_CHANGE = 15
_RIDGE_FALLBACK_ALPHA = 10.0  # Ridge used when N_clean < _RIDGE_MIN_SAMPLES
_RIDGE_MIN_SAMPLES = 500  # switch from Ridge to HistGBR above this
_BLEND_WEIGHT_RAMP_MIN = 500  # N_clean at which w_ml = 0 (pure physics)
_BLEND_WEIGHT_RAMP_MAX = 2500  # N_clean at which w_ml = 1.0 (full ML)
_BLEND_CORRECTION_CAP = 2.0  # cap ML correction to ±2×RMSE_train (D-1)
_RETRAIN_RMSE_TRIGGER = 1.5  # retrain if 7-day RMSE > 1.5× training RMSE

# ---------------------------------------------------------------------------
# Feature columns — must match data_pipeline.py exactly
# ---------------------------------------------------------------------------
FEATURE_COLUMNS: list[str] = [
    "outdoor_temp_c",
    "physics_kwh",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "doy_sin",
    "doy_cos",
    "is_weekend",
    "slot_index",
    "temp_delta_1slot",
    "temp_delta_24h",
    "rolling_mean_6h",
    "physics_kwh_sq",
]

# octopus_import_kwh added dynamically when trained with "both" source mode (D-7, D-18)
FEATURE_COLUMNS_WITH_OCTOPUS: list[str] = FEATURE_COLUMNS + ["octopus_import_kwh"]


# ---------------------------------------------------------------------------
# TrainedModel dataclass
# ---------------------------------------------------------------------------


@dataclass
class TrainedModel:
    """Container for a trained ML model and its associated metadata.

    Attributes:
        estimator: the fitted sklearn estimator (HistGBR or Ridge Pipeline).
        model_type: ``"hist_gbr"`` or ``"ridge"``.
        feature_columns: ordered list of feature names used during training.
            Used for compatibility checks on load — if the schema changes (e.g.
            source switched between ``"givenergy"`` and ``"both"`` mode), the
            model is discarded and retrained.
        trained_at: UTC datetime when training completed.
        n_training_samples: number of clean slots used for training.
        training_rmse: root mean squared error on the held-out 15 % validation
            set.  Used as the correction cap (D-1) and as the RMSE baseline for
            the 7-day health-check trigger (D-9).
        blend_weight: w_ml in [0, 1] computed from n_training_samples via the
            linear ramp formula (D-8).  Stored for diagnostic use; the ramp
            function should be called again at inference time if the sample
            count has grown between retrains.
        trained_with_octopus_feature: whether ``"octopus_import_kwh"`` was
            present in the training data.  Convenience flag mirroring whether
            ``feature_columns == FEATURE_COLUMNS_WITH_OCTOPUS``.
        slot_residual_std: global standard deviation of training residuals
            (actual − predicted on the full training set).  Single scalar used
            as an alternative ±2×RMSE correction cap reference (D-1).
        doy_daily_kwh: 366-entry list (index 0 = day-of-year 1) holding the
            mean total daily consumption (kWh) observed on that calendar day
            across all training days.  Gaps are linearly interpolated.
            Used by :class:`AnnualForecastSensor` to render a year bar chart.
            Defaults to an empty list for backwards compatibility with models
            persisted before this field was added.
    """

    estimator: Any
    model_type: str
    feature_columns: list[str]
    trained_at: datetime
    n_training_samples: int
    training_rmse: float
    blend_weight: float
    trained_with_octopus_feature: bool
    slot_residual_std: float  # single global std used for ±2×RMSE clamp
    r2_score: float | None = None  # R² on held-out 15 % validation split
    doy_daily_kwh: list[float] = field(default_factory=list)  # 366-entry DOY averages
    power_surface: dict = field(
        default_factory=dict
    )  # 3-D surface for MLPowerSurfaceSensor


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_blend_weight(n_clean: int) -> float:
    """Compute ML blend weight using the linear ramp defined in D-8.

    The weight ramps from 0.0 (pure physics) to 1.0 (full ML correction) as
    the number of clean training samples grows from ``_BLEND_WEIGHT_RAMP_MIN``
    to ``_BLEND_WEIGHT_RAMP_MAX``.

    Args:
        n_clean: number of clean 30-minute slots available for training.

    Returns:
        Blend weight ``w_ml`` in the closed interval ``[0.0, 1.0]``.

    Examples:
        >>> compute_blend_weight(250)   # below gate — pure physics
        0.0
        >>> compute_blend_weight(1500)  # half-way through ramp
        0.5
        >>> compute_blend_weight(3000)  # above ceiling — full ML
        1.0
    """
    return float(
        np.clip(
            (n_clean - _BLEND_WEIGHT_RAMP_MIN)
            / (_BLEND_WEIGHT_RAMP_MAX - _BLEND_WEIGHT_RAMP_MIN),
            0.0,
            1.0,
        )
    )


def train_power_model(df: pd.DataFrame) -> TrainedModel:
    """Train a power consumption correction model on the cleaned training DataFrame.

    The model learns the additive residual δ = actual_kwh − physics_kwh, enabling
    the blend formula at inference time (D-1)::

        ŷ = physics_kwh + w_ml × predict_correction(model, features)

    Model selection (D-6):
        * N_clean ≥ 500 → ``HistGradientBoostingRegressor`` (native NaN).
        * N_clean < 500 → ``Ridge(alpha=10)`` with mean imputation (cold-start).

    Feature schema (D-7):
        ``FEATURE_COLUMNS`` always; ``FEATURE_COLUMNS_WITH_OCTOPUS`` when the
        ``octopus_import_kwh`` column is present in *df*.

    RMSE metric:
        Computed on a held-out 15 % validation split (stratified by
        ``random_state=42`` for reproducibility).

    Args:
        df: Cleaned DataFrame produced by ``build_training_dataframe()`` with:
            - ``actual_kwh`` column (ground-truth consumption per 30-min slot).
            - ``physics_kwh`` column (physics model output for that slot).
            - All columns in :data:`FEATURE_COLUMNS` present.
            ``octopus_import_kwh`` is optional; included automatically when
            present.

    Returns:
        :class:`TrainedModel` with fitted estimator and metadata.

    Raises:
        ValueError: if any required column is absent from *df*.
    """
    # Heavy imports deferred to avoid blocking the HA event loop at module load
    from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: PLC0415
    from sklearn.impute import SimpleImputer  # noqa: PLC0415
    from sklearn.linear_model import Ridge  # noqa: PLC0415
    from sklearn.model_selection import train_test_split  # noqa: PLC0415
    from sklearn.pipeline import Pipeline  # noqa: PLC0415

    # ------------------------------------------------------------------
    # 1. Validate required columns
    # ------------------------------------------------------------------
    required_base = {"actual_kwh", "physics_kwh"} | set(FEATURE_COLUMNS)
    missing = required_base - set(df.columns)
    if missing:
        raise ValueError(
            f"train_power_model: missing required columns: {sorted(missing)}"
        )

    # ------------------------------------------------------------------
    # 2. Select feature schema — include Octopus column when available
    # ------------------------------------------------------------------
    has_octopus = "octopus_import_kwh" in df.columns
    feature_cols = FEATURE_COLUMNS_WITH_OCTOPUS if has_octopus else FEATURE_COLUMNS

    X = df[feature_cols].to_numpy(dtype=float)
    y = (df["actual_kwh"] - df["physics_kwh"]).to_numpy(dtype=float)
    n_samples = len(df)

    # ------------------------------------------------------------------
    # 3. Train / validation split (85 / 15) for honest RMSE reporting
    # ------------------------------------------------------------------
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=_HIST_GBR_VALIDATION_FRACTION, random_state=42
    )

    # ------------------------------------------------------------------
    # 4. Choose and construct estimator (D-6)
    # ------------------------------------------------------------------
    if n_samples >= _RIDGE_MIN_SAMPLES:
        estimator: Any = HistGradientBoostingRegressor(
            max_iter=_HIST_GBR_MAX_ITER,
            max_depth=_HIST_GBR_MAX_DEPTH,
            learning_rate=_HIST_GBR_LEARNING_RATE,
            min_samples_leaf=_HIST_GBR_MIN_SAMPLES_LEAF,
            l2_regularization=_HIST_GBR_L2_REG,
            validation_fraction=_HIST_GBR_VALIDATION_FRACTION,
            n_iter_no_change=_HIST_GBR_N_ITER_NO_CHANGE,
            random_state=42,
        )
        model_type = "hist_gbr"
        _LOGGER.debug(
            "train_power_model: using HistGradientBoostingRegressor (n=%d)", n_samples
        )
    else:
        # Ridge does not handle NaN natively; wrap in a mean-imputation Pipeline.
        estimator = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="mean")),
                ("ridge", Ridge(alpha=_RIDGE_FALLBACK_ALPHA)),
            ]
        )
        model_type = "ridge"
        _LOGGER.debug(
            "train_power_model: using Ridge fallback (n=%d < %d)",
            n_samples,
            _RIDGE_MIN_SAMPLES,
        )

    # ------------------------------------------------------------------
    # 5. Fit on training split
    # ------------------------------------------------------------------
    estimator.fit(X_train, y_train)

    # ------------------------------------------------------------------
    # 6. Compute RMSE and R² on the held-out validation split
    # ------------------------------------------------------------------
    y_pred_val = estimator.predict(X_val)
    training_rmse = float(np.sqrt(np.mean((y_val - y_pred_val) ** 2)))
    _ss_res = float(np.sum((y_val - y_pred_val) ** 2))
    _ss_tot = float(np.sum((y_val - np.mean(y_val)) ** 2))
    r2_score_val: float | None = (
        round(1.0 - _ss_res / _ss_tot, 4) if _ss_tot > 0 else None
    )

    # ------------------------------------------------------------------
    # 7. Compute global residual std on full dataset (D-1 correction cap ref)
    # ------------------------------------------------------------------
    y_pred_all = estimator.predict(X)
    residuals = y - y_pred_all
    slot_residual_std = float(np.std(residuals))

    # ------------------------------------------------------------------
    # 8. Derive blend weight from sample count (D-8)
    # ------------------------------------------------------------------
    blend_weight = compute_blend_weight(n_samples)

    _LOGGER.info(
        "train_power_model: training complete — model=%s n=%d rmse=%.4f w_ml=%.3f",
        model_type,
        n_samples,
        training_rmse,
        blend_weight,
    )

    # ------------------------------------------------------------------
    # 9. Per-day-of-year average daily kWh (for AnnualForecastSensor)
    # ------------------------------------------------------------------
    # Sum each day's 48 slots, then average across all training days that
    # fall on the same calendar day-of-year (1–366).
    _daily_totals: dict[int, list[float]] = {}
    for _date, _group in df["actual_kwh"].groupby(df.index.normalize()):
        _doy = pd.Timestamp(_date).day_of_year
        _daily_totals.setdefault(_doy, []).append(float(_group.sum()))
    _doy_arr = np.full(366, np.nan, dtype=float)
    for _doy, _vals in _daily_totals.items():
        _doy_arr[_doy - 1] = float(np.mean(_vals))
    # Fill calendar days absent from the training window using a single-harmonic
    # seasonal fit (a + b·cos(2π·doy/365) + c·sin(2π·doy/365)).  This correctly
    # extrapolates summer/winter variation even when training data covers only
    # part of the year, unlike a linear edge-fill which just copies edge values.
    _known_mask = ~np.isnan(_doy_arr)
    if _known_mask.sum() >= 3:
        _doy_idx = np.arange(1, 367, dtype=float)
        _theta = 2.0 * np.pi * _doy_idx / 365.0
        _A_full = np.column_stack(
            [np.ones(366, dtype=float), np.cos(_theta), np.sin(_theta)]
        )
        _coeffs, _, _, _ = np.linalg.lstsq(
            _A_full[_known_mask], _doy_arr[_known_mask], rcond=None
        )
        _seasonal = _A_full @ _coeffs
        _doy_arr[~_known_mask] = np.maximum(0.0, _seasonal[~_known_mask])
    else:
        # Not enough data points for a harmonic fit — fall back to forward/back-fill
        _doy_series = pd.Series(_doy_arr)
        _doy_series = _doy_series.interpolate(method="linear", limit_direction="both")
        _doy_arr = _doy_series.to_numpy(dtype=float)
    doy_daily_kwh: list[float] = [round(float(v), 3) for v in _doy_arr]

    return TrainedModel(
        estimator=estimator,
        model_type=model_type,
        feature_columns=feature_cols,
        trained_at=datetime.now(tz=timezone.utc),
        n_training_samples=n_samples,
        training_rmse=training_rmse,
        blend_weight=blend_weight,
        trained_with_octopus_feature=has_octopus,
        slot_residual_std=slot_residual_std,
        r2_score=r2_score_val,
        doy_daily_kwh=doy_daily_kwh,
    )


def predict_correction(
    model: TrainedModel,
    features: pd.DataFrame,
) -> np.ndarray:
    """Predict the power consumption correction (residual δ) for each row.

    The raw prediction δ̂ from the estimator is clamped to
    ``±(_BLEND_CORRECTION_CAP × training_rmse)`` to prevent wild extrapolation
    on unseen feature combinations (D-1).

    At inference time the ``octopus_import_kwh`` column will be ``NaN`` for
    "both" source mode; ``HistGradientBoostingRegressor`` handles this natively
    with no special pre-processing required (D-18 revised inference spec).

    Args:
        model: A :class:`TrainedModel` returned by :func:`train_power_model`.
        features: DataFrame whose columns include at least
            ``model.feature_columns`` in any order.  Extra columns are ignored.

    Returns:
        NumPy array of shape ``(len(features),)`` containing the clamped
        residual correction δ̂ in kWh for each input slot.
    """
    X = features[model.feature_columns].to_numpy(dtype=float)
    raw: np.ndarray = model.estimator.predict(X)
    cap = _BLEND_CORRECTION_CAP * model.training_rmse
    return np.clip(raw, -cap, cap)


def compute_power_surface(
    model: "TrainedModel", physics_calc: Any | None = None
) -> dict:
    """Build the 3-D power surface for ``MLPowerSurfaceSensor``.

    Sweeps 52 representative weeks × 16 temperature points and computes the
    blended daily kWh prediction for each cell by simulating all 48 half-hour
    slots of a representative Wednesday at noon and summing.

    All 52 × 16 × 48 = 39_936 feature rows are stacked into a single DataFrame
    and passed to ``predict_correction`` in one batch call, making the
    computation fast even for large models.

    Args:
        model: A trained :class:`TrainedModel`.
        physics_calc: The ``PowerCalulator`` instance (must not be None).

    Returns:
        Dict with keys:
            ``temps``  — list of 16 temperature floats (°C)
            ``weeks``  — list of 52 ISO week ints (1–52)
            ``z``      — list of 52 lists, each with 16 blended daily-kWh floats
            ``z_physics`` — same shape, physics-only predictions for comparison
    """
    temps: list[float] = [
        float(t) for t in range(-10, 22, 2)
    ]  # -10..20 step 2 → 16 pts
    weeks: list[int] = list(range(1, 53))  # ISO weeks 1–52
    n_temps = len(temps)
    n_weeks = len(weeks)
    n_slots = 48

    blend_weight = compute_blend_weight(model.n_training_samples)
    ref_year = model.trained_at.year

    # Build one representative datetime per week (Wednesday noon UTC).
    # ISO week 53 may not exist for all years — clamp to the year boundary.
    week_dts: list[datetime] = []
    for w in weeks:
        try:
            ref_date = datetime(ref_year, 1, 1, tzinfo=timezone.utc)
            # isoweekday: 1=Mon … 7=Sun; Wednesday = 3
            iso_wed = datetime.fromisocalendar(ref_year, w, 3).replace(
                hour=12, tzinfo=timezone.utc
            )
        except ValueError:
            # Week 53 doesn't exist for this year — use Dec 31 noon
            iso_wed = datetime(ref_year, 12, 31, 12, 0, tzinfo=timezone.utc)
        week_dts.append(iso_wed)

    # Build half-hour slot offsets relative to each week's reference noon
    slot_offsets: list[int] = list(range(n_slots))  # 0..47 (30-min slots)

    # Pre-compute slot datetimes for all weeks: shape (n_weeks, n_slots)
    # We'll iterate temp × week × slot to fill rows, then batch-predict.
    total_rows = n_temps * n_weeks * n_slots
    row_data: list[dict] = []
    physics_matrix = np.zeros((n_weeks, n_temps, n_slots), dtype=float)

    for ti, temp in enumerate(temps):
        for wi, week_dt in enumerate(week_dts):
            # Midnight of the representative day
            day_start = week_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            for si in range(n_slots):
                slot_dt = day_start + timedelta(minutes=si * 30)
                physics_kwh = (
                    physics_calc.from_temp_and_time(slot_dt, temp)
                    if physics_calc is not None
                    else 0.0
                )
                physics_matrix[wi, ti, si] = physics_kwh

                hour = slot_dt.hour
                minute = slot_dt.minute
                slot_index = hour * 2 + (1 if minute >= 30 else 0)
                doy = slot_dt.timetuple().tm_yday
                dow = slot_dt.weekday()  # Wednesday = 2

                row: dict[str, float] = {
                    "outdoor_temp_c": temp,
                    "physics_kwh": physics_kwh,
                    "hour_sin": math.sin(2 * math.pi * hour / 24),
                    "hour_cos": math.cos(2 * math.pi * hour / 24),
                    "dow_sin": math.sin(2 * math.pi * dow / 7),
                    "dow_cos": math.cos(2 * math.pi * dow / 7),
                    "doy_sin": math.sin(2 * math.pi * doy / 365),
                    "doy_cos": math.cos(2 * math.pi * doy / 365),
                    "is_weekend": 0.0,
                    "slot_index": float(slot_index),
                    "temp_delta_1slot": float("nan"),
                    "temp_delta_24h": float("nan"),
                    "rolling_mean_6h": float("nan"),
                    "temp_delta_1slot_sq": float("nan"),
                    "physics_kwh_sq": physics_kwh**2,
                }
                if "octopus_import_kwh" in model.feature_columns:
                    row["octopus_import_kwh"] = float("nan")
                row_data.append(row)

    # Batch predict all rows in one call
    features_df = pd.DataFrame(row_data, columns=model.feature_columns)
    corrections = predict_correction(model, features_df)  # shape (total_rows,)

    # corrections array layout: iterate temps outer, weeks middle, slots inner
    corrections_3d = corrections.reshape(n_temps, n_weeks, n_slots)

    # Build z matrices (n_weeks rows × n_temps cols)
    z: list[list[float]] = []
    z_physics: list[list[float]] = []
    for wi in range(n_weeks):
        z_row: list[float] = []
        zp_row: list[float] = []
        for ti in range(n_temps):
            slot_physics = physics_matrix[wi, ti, :]  # shape (n_slots,)
            slot_corrections = corrections_3d[ti, wi, :]  # shape (n_slots,)
            daily_physics = float(np.sum(slot_physics))
            daily_blended = float(
                np.sum(slot_physics + blend_weight * slot_corrections)
            )
            z_row.append(round(max(0.0, daily_blended), 3))
            zp_row.append(round(max(0.0, daily_physics), 3))
        z.append(z_row)
        z_physics.append(zp_row)

    return {
        "temps": temps,
        "weeks": weeks,
        "z": z,
        "z_physics": z_physics if physics_calc is not None else None,
    }


def check_model_compatibility(
    model: TrainedModel,
    current_feature_columns: list[str],
) -> bool:
    """Return ``True`` if the model's feature schema matches the current config.

    A mismatch forces a retrain — this occurs when the user switches the
    consumption source between ``"givenergy"`` and ``"both"`` mode, which
    changes whether ``octopus_import_kwh`` is present in the feature vector.

    Args:
        model: The loaded :class:`TrainedModel` to check.
        current_feature_columns: The feature column list that would be used
            if training were triggered now (either :data:`FEATURE_COLUMNS` or
            :data:`FEATURE_COLUMNS_WITH_OCTOPUS`).

    Returns:
        ``True`` if schemas are identical; ``False`` if a retrain is required.
    """
    return model.feature_columns == current_feature_columns
