"""Data pipeline for ML Power Estimator training data preparation.

Pipeline stages executed by :func:`build_training_dataframe`:

1. **Align & resample** — Normalise all input Series to UTC, resample to a
   30-minute grid, and inner-join on the timestamp index so that every
   downstream operation shares a common time axis.

2. **Hard exclusions (D-12 §1-2)** — Remove zero/NaN actual-consumption
   readings where the physics model predicts meaningful demand
   (> ``_SENSOR_ZERO_PHYSICS_THRESHOLD`` kWh), apply an absolute upper-bound
   clip (> ``_MAX_SLOT_KWH``), and exclude ±1 slot around temporal gaps longer
   than ``_GAP_THRESHOLD_MINUTES`` minutes (indicative of HA restart or sensor
   dropout).

3. **EV / large-load block exclusion (D-17 revised)** — :func:`detect_ev_blocks`
   runs the Hybrid-D algorithm with a temperature-correlation discriminator.
   Heat pump electrical consumption is anti-correlated with outdoor temperature
   (Pearson r < −0.4) and is preserved; EV charging is temperature-independent
   (|r| < 0.2) and is removed.  An ambiguous band (−0.4 ≤ r < −0.2) is
   resolved by a secondary temperature-span / proxy-physics check, and a
   coefficient-of-variation fallback handles the cold-start case where neither
   physics model nor temperature data are available.

4. **Residual z-score fencing (D-12 §3)** — Per ``slot_index`` (0–47) z-score
   of the residual ``actual − physics``.  Slots with |z| > ``_RESIDUAL_ZSCORE_THRESHOLD``
   are excluded.

5. **Per-slot IQR fence (D-12 §4)** — Per ``slot_index`` upper fence of
   Q3 + ``_IQR_MULTIPLIER`` × IQR acts as a belt-and-braces safety net.

6. **Flat-line freeze detection (D-12 §5)** — ≥ ``_FLATLINE_MIN_SLOTS``
   consecutive identical non-zero readings indicate a frozen sensor and the
   entire run is excluded.

7. **Quality gate (D-10)** — Raises :exc:`InsufficientDataError` if the
   number of clean slots falls below ``_MIN_CLEAN_SLOTS`` or the observed
   temperature range is below ``_MIN_TEMP_RANGE_C``.

8. **Feature engineering (D-7)** — Computes the full 15-feature vector plus
   the optional Octopus import feature (#16) when requested.

Note: this module is pure Python (Pandas / NumPy / SciPy only).  It contains
no Home Assistant imports and may safely be unit-tested outside HA.
"""

from __future__ import annotations

import logging
import math
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

_LOGGER = logging.getLogger(__name__)

_LONDON_TZ = ZoneInfo("Europe/London")

# ---------------------------------------------------------------------------
# D-17 EV / large-load detection thresholds
# ---------------------------------------------------------------------------

# kWh/slot — minimum actual consumption to flag (~3 kW sustained)
_LARGE_LOAD_FLOOR_KWH: float = 1.5

# Residual must exceed 4 × IQR of the residual distribution
_RESIDUAL_IQR_MULTIPLIER: float = 4.0

# kWh/slot — minimum absolute residual magnitude to flag
_RESIDUAL_ABS_MIN_KWH: float = 1.0

# Minimum consecutive candidate slots (≥ 90 min) to qualify as a block
_MIN_RUN_SLOTS: int = 3

# ±1 buffer slot applied around each excluded run to capture ramp-up/down
_BUFFER_SLOTS: int = 1

# Percentile threshold for cold-start absolute candidate detection (no physics or temp)
_COLD_START_PERCENTILE: int = 98

# kWh/slot absolute floor for cold-start detection (~5 kW)
_COLD_START_FLOOR_KWH: float = 2.5

# Pearson r threshold: stronger anti-correlation → heating → do NOT exclude
_TEMP_CORRELATION_UPPER: float = -0.4

# Pearson r threshold: weaker anti-correlation boundary → ambiguous band begins
_TEMP_CORRELATION_LOWER: float = -0.2

# ±6 slots (±3 h) context window for correlation calculation
_TEMP_CONTEXT_SLOTS: int = 6

# Minimum temperature span within block to count as heating evidence (ambiguous gate)
_TEMP_RANGE_MIN_C: float = 3.0

# Coefficient-of-variation threshold: CV < 0.20 = flat sustained load = EV-like
_CV_EV_THRESHOLD: float = 0.20

# Conservative proxy heat-loss coefficient when no physics model is available (W/°C)
_PROXY_HEAT_LOSS_W_PER_C: float = 100.0

# Assumed indoor setpoint for proxy physics estimate (°C)
_INDOOR_TEMP_PROXY_C: float = 20.0

# ---------------------------------------------------------------------------
# D-12 anomaly detection constants
# ---------------------------------------------------------------------------

# Absolute upper bound — above typical residential fuse capacity (kWh/slot)
_MAX_SLOT_KWH: float = 20.0

# Physics threshold below which zero readings are NOT considered anomalies
_SENSOR_ZERO_PHYSICS_THRESHOLD: float = 0.2

# Consecutive identical non-zero readings that indicate a frozen sensor
_FLATLINE_MIN_SLOTS: int = 6

# Per-slot-index z-score threshold for residual fencing
_RESIDUAL_ZSCORE_THRESHOLD: float = 3.5

# Per-slot-index IQR fence multiplier
_IQR_MULTIPLIER: float = 3.0

# Temporal gaps longer than this get ±1 slot excluded (minutes)
_GAP_THRESHOLD_MINUTES: int = 15

# ---------------------------------------------------------------------------
# D-10 quality gate constants
# ---------------------------------------------------------------------------

# Minimum number of clean slots required to attempt training
_MIN_CLEAN_SLOTS: int = 500

# Minimum observed temperature range required to attempt training (°C)
_MIN_TEMP_RANGE_C: float = 5.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InsufficientDataError(ValueError):
    """Raised when training data does not meet quality gate requirements (D-10).

    This may be raised when:
    - The number of clean slots is below ``_MIN_CLEAN_SLOTS`` (500), or
    - The observed outdoor temperature range is below ``_MIN_TEMP_RANGE_C`` (5 °C),
    - The input Series are empty or contain no overlapping timestamps.
    """


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _normalise_series_to_utc(series: pd.Series) -> pd.Series:
    """Normalise a Series with a DatetimeIndex to UTC.

    Handles three cases:

    * **tz-naive** — assumed to be Europe/London local time; localised to
      Europe/London (ambiguous times set to NaT and dropped), then converted
      to UTC.
    * **tz-aware, non-UTC** — converted to UTC via ``tz_convert``.
    * **already UTC** — returned without modification (shallow copy).

    DST fall-back duplicates are removed by keeping the first occurrence,
    per the ``~index.duplicated(keep="first")`` guard.

    Args:
        series: Input Series.  Must have a DatetimeIndex or an index
            convertible to datetime.

    Returns:
        Series with a UTC DatetimeIndex, duplicate timestamps removed.
    """
    if series is None or len(series) == 0:
        return pd.Series(dtype=float)

    # Ensure the index is a DatetimeIndex
    if not isinstance(series.index, pd.DatetimeIndex):
        series = series.copy()
        series.index = pd.to_datetime(series.index)

    idx = series.index

    if idx.tz is None:
        # tz-naive: assume Europe/London
        try:
            new_idx = idx.tz_localize(
                _LONDON_TZ, ambiguous="NaT", nonexistent="NaT"
            ).tz_convert("UTC")
        except Exception:
            # Absolute fallback: treat as UTC
            _LOGGER.debug("tz_localize to Europe/London failed; assuming UTC instead")
            new_idx = idx.tz_localize("UTC")
        series = series.copy()
        series.index = new_idx
        # Drop any NaT timestamps produced by ambiguity handling
        series = series[series.index.notna()]
    elif str(idx.tz) not in ("UTC", "utc"):
        series = series.copy()
        series.index = idx.tz_convert("UTC")

    # Remove DST fall-back duplicates
    series = series[~series.index.duplicated(keep="first")]

    return series


def resample_to_30min(series: pd.Series, is_cumulative: bool = False) -> pd.Series:
    """Resample a raw sensor Series to 30-minute kWh-per-slot values.

    Args:
        series: Input Series with a DatetimeIndex (any sub-30-min frequency).
        is_cumulative: Set ``True`` when the sensor has
            ``device_class == "energy"`` (cumulative kWh register).  In this
            case the function takes the last reading in each window and
            ``diff()``s to obtain per-slot energy; negative diffs (meter reset
            / counter overflow) are clamped to ``NaN``.

            Set ``False`` (default) for instantaneous power sensors whose
            values are in **Watts**; the mean is taken over the window and
            scaled to kWh: ``mean_W × 0.5 h ÷ 1000``.

    Returns:
        Series resampled to 30-minute intervals with a UTC-normalised
        DatetimeIndex.  Values are in kWh per 30-minute slot.
    """
    if series is None or len(series) == 0:
        return pd.Series(dtype=float)

    if is_cumulative:
        result = series.resample("30min").last()
        result = result.diff()
        # Clamp negative diffs (meter reset / counter overflow) to NaN
        result = result.where(result >= 0, other=np.nan)
        return result
    else:
        # Instantaneous power in W → kWh per 30-min slot
        return series.resample("30min").mean() * 0.5 / 1000.0


def _detect_flatlines(series: pd.Series, min_slots: int) -> pd.Series:
    """Return a boolean mask of flat-line (frozen sensor) runs.

    A run is identified as any sequence of *min_slots* or more consecutive
    slots that carry identical **non-zero** values.  Zero readings are not
    flagged because legitimate zero consumption is possible.

    Args:
        series: 1-D numeric Series.
        min_slots: Minimum number of consecutive identical non-zero values
            to flag as a frozen sensor run.

    Returns:
        Boolean Series (same index as *series*) where ``True`` means the
        slot belongs to a frozen-sensor run and should be excluded.
    """
    mask = pd.Series(False, index=series.index, dtype=bool)
    if len(series) == 0:
        return mask

    values = series.values.astype(float)
    n = len(values)
    i = 0
    while i < n:
        v = values[i]
        if np.isnan(v) or v == 0.0:
            i += 1
            continue
        # Walk forward while value is the same
        j = i + 1
        while j < n and values[j] == v:
            j += 1
        if j - i >= min_slots:
            mask.iloc[i:j] = True
        i = j

    return mask


def _find_runs(mask: np.ndarray, min_length: int) -> list[tuple[int, int]]:
    """Find contiguous runs of ``True`` with length ≥ *min_length*.

    Args:
        mask: 1-D boolean NumPy array.
        min_length: Minimum run length to include.

    Returns:
        List of ``(start, end)`` index pairs (both inclusive).
    """
    runs: list[tuple[int, int]] = []
    n = len(mask)
    i = 0
    while i < n:
        if mask[i]:
            j = i + 1
            while j < n and mask[j]:
                j += 1
            if j - i >= min_length:
                runs.append((i, j - 1))
            i = j
        else:
            i += 1
    return runs


# ---------------------------------------------------------------------------
# EV / large-load block detection (D-17 revised)
# ---------------------------------------------------------------------------


def detect_ev_blocks(
    power_kwh: pd.Series,
    physics_kwh: pd.Series | None,
    outdoor_temp_c: pd.Series | None,
) -> tuple[pd.Series, list[dict]]:
    # Heavy import deferred to avoid blocking the HA event loop at module load
    from scipy.stats import pearsonr  # noqa: PLC0415

    """Detect EV / large-load charging blocks in power consumption data.

    Uses the Hybrid-D algorithm with a temperature-correlation discriminator
    (D-17 revised).

    The key physical insight: heat pump electrical draw is strongly
    anti-correlated with outdoor temperature (Pearson r ≈ −0.7 to −0.9).
    EV charger draw is temperature-independent (r ≈ 0 ± 0.1).  This
    distinguishes the two load types even when the physics model is
    uncalibrated or ``heating_type = "none"``.

    Algorithm stages:

    **2a — Candidate detection**

    * *Case A* (physics + temp available): ``residual = actual − physics``;
      candidate when ``residual > max(4 × IQR(residual), 1.0 kWh)`` **AND**
      ``actual > 1.5 kWh``.
    * *Case B* (temp available, no calibrated physics): proxy physics
      ``= 100 W/°C × max(0, 20 − temp) / 1000 / 2`` used in place of
      physics; same residual gate as Case A.
    * *Case C* (neither physics nor temp): absolute threshold
      ``> max(98th-percentile, 2.5 kWh)``.

    Apply ``_MIN_RUN_SLOTS`` (3-slot) persistence gate to filter out
    short spikes (kettles, ovens).

    **2b — Temperature-correlation discriminator** (Cases A and B):
    Compute Pearson r over a ±6-slot context window.

    * ``r < −0.4`` → heating → **keep** (do not exclude)
    * ``r ≥ −0.2`` → EV / appliance → **exclude**
    * ``−0.4 ≤ r < −0.2`` → ambiguous → proceed to secondary check

    **2c — Secondary discriminator** (ambiguous blocks + Case C):

    * Temperature span ≥ 3 °C within block → heating → keep
    * Mean consumption within 20 % of proxy-physics estimate → heating → keep
    * Otherwise → exclude
    * *Case C CV fallback*: CV < 0.20 (flat sustained load) → exclude

    **2d — Buffer slots**: ±1 slot applied around each excluded run.

    Args:
        power_kwh: UTC 30-min actual consumption Series (kWh/slot).
        physics_kwh: Physics-model output on the same index (kWh/slot),
            or ``None`` when uncalibrated / unavailable.
        outdoor_temp_c: Outdoor temperature on the same index (°C),
            or ``None`` when unavailable.

    Returns:
        Tuple of:
        - ``exclusion_mask``: Boolean Series (``True`` = exclude from training).
        - ``ev_blocks``: List of detection-metadata dicts with keys
          ``start``, ``end``, ``n_slots``, ``mean_kwh``, ``peak_kwh``,
          ``r_temp``, ``cv``, ``detection_mode``.
    """
    exclusion_mask = pd.Series(False, index=power_kwh.index, dtype=bool)
    ev_blocks: list[dict] = []

    if len(power_kwh) == 0:
        return exclusion_mask, ev_blocks

    power_values = power_kwh.fillna(0.0).values.astype(float)

    # Determine which detection case applies
    has_physics = (
        physics_kwh is not None
        and len(physics_kwh) > 0
        and not physics_kwh.isna().all()
    )
    has_temp = (
        outdoor_temp_c is not None
        and len(outdoor_temp_c) > 0
        and not outdoor_temp_c.isna().all()
    )
    temp_values: np.ndarray | None = (
        outdoor_temp_c.values.astype(float) if has_temp else None
    )

    # ------------------------------------------------------------------
    # 2a: Candidate detection
    # ------------------------------------------------------------------
    residual: np.ndarray | None
    mode_base: str

    if has_physics:
        # Case A — calibrated physics available
        physics_values = physics_kwh.fillna(0.0).values.astype(float)
        residual = power_values - physics_values
        mode_base = "residual_iqr"
    elif has_temp and temp_values is not None:
        # Case B — proxy physics from temperature
        proxy_physics = (
            _PROXY_HEAT_LOSS_W_PER_C
            * np.maximum(0.0, _INDOOR_TEMP_PROXY_C - temp_values)
            / 1000.0
            / 2.0
        )
        residual = power_values - proxy_physics
        mode_base = "proxy_physics_cold_start"
    else:
        # Case C — no physics, no temperature
        residual = None
        mode_base = "temporal_cv_fallback"

    if residual is not None:
        iqr_residual = float(
            np.nanpercentile(residual, 75) - np.nanpercentile(residual, 25)
        )
        threshold = max(_RESIDUAL_IQR_MULTIPLIER * iqr_residual, _RESIDUAL_ABS_MIN_KWH)
        candidate_mask = (residual > threshold) & (power_values > _LARGE_LOAD_FLOOR_KWH)
    else:
        # Case C: absolute percentile threshold
        p98 = float(np.nanpercentile(power_values, _COLD_START_PERCENTILE))
        absolute_threshold = max(p98, _COLD_START_FLOOR_KWH)
        candidate_mask = power_values > absolute_threshold

    # Persistence gate: only runs >= _MIN_RUN_SLOTS qualify
    candidate_runs = _find_runs(candidate_mask, _MIN_RUN_SLOTS)

    if not candidate_runs:
        return exclusion_mask, ev_blocks

    # ------------------------------------------------------------------
    # 2b / 2c: Discriminators
    # ------------------------------------------------------------------
    runs_to_exclude: list[tuple[int, int, str, float | None, float | None]] = []
    # Each entry: (start_i, end_i, detection_mode, r_temp, cv)

    for run_start, run_end in candidate_runs:
        run_power = power_values[run_start : run_end + 1]
        mean_kwh = float(np.nanmean(run_power))

        # ------ Case C: CV fallback (no residual) ------
        if residual is None:
            cv = (
                float(np.nanstd(run_power) / mean_kwh) if mean_kwh > 0 else float("inf")
            )
            if cv < _CV_EV_THRESHOLD:
                runs_to_exclude.append(
                    (run_start, run_end, "temporal_cv_fallback", None, cv)
                )
            continue

        # ------ No temperature data: CV discriminator ------
        if temp_values is None:
            cv = (
                float(np.nanstd(run_power) / mean_kwh) if mean_kwh > 0 else float("inf")
            )
            if cv < _CV_EV_THRESHOLD:
                runs_to_exclude.append((run_start, run_end, mode_base, None, cv))
            continue

        # ------ 2b: Temperature-correlation discriminator ------
        ctx_start = max(0, run_start - _TEMP_CONTEXT_SLOTS)
        ctx_end = min(len(power_values), run_end + _TEMP_CONTEXT_SLOTS + 1)

        power_ctx = power_values[ctx_start:ctx_end]
        temp_ctx = temp_values[ctx_start:ctx_end]

        valid = ~(np.isnan(power_ctx) | np.isnan(temp_ctx))
        if valid.sum() >= 4:
            r, _ = pearsonr(power_ctx[valid], temp_ctx[valid])
            r = float(r)
        else:
            r = 0.0  # insufficient context → treat as uncorrelated (EV-like)

        if r < _TEMP_CORRELATION_UPPER:
            # Strongly anti-correlated → heating load → preserve
            continue

        if r >= _TEMP_CORRELATION_LOWER:
            # Temperature-independent (or positive) → EV / appliance → exclude
            runs_to_exclude.append((run_start, run_end, mode_base, r, None))
            continue

        # ------ 2c: Secondary discriminator (ambiguous: −0.4 ≤ r < −0.2) ------
        run_temp = temp_values[run_start : run_end + 1]
        valid_temp = run_temp[~np.isnan(run_temp)]
        temp_span = (
            float(np.max(valid_temp) - np.min(valid_temp))
            if len(valid_temp) >= 2
            else 0.0
        )

        if temp_span >= _TEMP_RANGE_MIN_C:
            # Significant thermal variation in block → consistent with heating → keep
            continue

        # Proxy-physics proximity check
        mean_run_temp = float(np.nanmean(run_temp))
        proxy_estimate = (
            _PROXY_HEAT_LOSS_W_PER_C
            * max(0.0, _INDOOR_TEMP_PROXY_C - mean_run_temp)
            / 1000.0
            / 2.0
        )
        if (
            proxy_estimate > 0
            and abs(mean_kwh - proxy_estimate) / proxy_estimate <= 0.20
        ):
            # Consumption consistent with physics proxy → heating → keep
            continue

        # Failed all secondary checks → exclude as ambiguous large load
        runs_to_exclude.append((run_start, run_end, "ev_ambiguous_secondary", r, None))

    # ------------------------------------------------------------------
    # 2d: Apply buffer slots and build output
    # ------------------------------------------------------------------
    for run_start, run_end, det_mode, r_temp, cv in runs_to_exclude:
        buf_start = max(0, run_start - _BUFFER_SLOTS)
        buf_end = min(len(exclusion_mask), run_end + _BUFFER_SLOTS + 1)
        exclusion_mask.iloc[buf_start:buf_end] = True

        run_power = power_values[run_start : run_end + 1]

        ev_blocks.append(
            {
                "start": power_kwh.index[run_start].isoformat(),
                "end": power_kwh.index[run_end].isoformat(),
                "n_slots": run_end - run_start + 1,
                "mean_kwh": float(np.nanmean(run_power)),
                "peak_kwh": float(np.nanmax(run_power)),
                "r_temp": round(r_temp, 4) if r_temp is not None else None,
                "cv": round(cv, 4) if cv is not None else None,
                "detection_mode": det_mode,
            }
        )

    return exclusion_mask, ev_blocks


# ---------------------------------------------------------------------------
# Feature engineering (D-7)
# ---------------------------------------------------------------------------


def _add_features(df: pd.DataFrame, include_octopus: bool) -> pd.DataFrame:
    """Add all D-7 features to the cleaned DataFrame.

    The DataFrame must already contain ``actual_kwh``, ``physics_kwh``, and
    ``outdoor_temp_c`` columns with a UTC DatetimeIndex.

    Features added:

    * ``hour_sin``, ``hour_cos``: circular UTC-hour encoding (period 24).
    * ``dow_sin``, ``dow_cos``: circular UTC day-of-week encoding (period 7).
    * ``doy_sin``, ``doy_cos``: circular UTC day-of-year encoding (period 365).
    * ``is_weekend``: 1 when UTC weekday is Saturday (5) or Sunday (6).
    * ``slot_index``: 0–47 (30-min slot within the UTC day).
    * ``temp_delta_1slot``: ``outdoor_temp_c[t] − outdoor_temp_c[t−1]``.
    * ``temp_delta_24h``: ``outdoor_temp_c[t] − outdoor_temp_c[t−48]``.
    * ``rolling_mean_6h``: 12-slot trailing mean of ``outdoor_temp_c``
      (captures 6 h of thermal inertia; min_periods=1).
    * ``temp_delta_1slot_sq``: ``temp_delta_1slot²`` (D-7 feature #14).
    * ``physics_kwh_sq``: ``physics_kwh²`` (captures nonlinear heating tail,
      D-7 feature #15).

    Circular encoding formula:

    .. math::
        \\text{feature\\_sin} = \\sin\\!\\left(\\frac{2\\pi \\cdot \\text{value}}{\\text{period}}\\right)

    This eliminates the artificial discontinuity at period boundaries (e.g.
    23:30 → 00:00, Sunday → Monday).

    If ``include_octopus`` is ``False``, any ``octopus_import_kwh`` column
    already in *df* is removed.

    Args:
        df: Cleaned DataFrame (output of the filtering stages).
        include_octopus: Whether to retain the ``octopus_import_kwh`` feature.

    Returns:
        New DataFrame with all engineered features appended.
    """
    df = df.copy()
    idx = df.index

    # --- Circular time encodings ---
    hour = idx.hour + idx.minute / 60.0
    df["hour_sin"] = np.sin(2.0 * math.pi * hour / 24.0)
    df["hour_cos"] = np.cos(2.0 * math.pi * hour / 24.0)

    dow = idx.dayofweek.astype(float)
    df["dow_sin"] = np.sin(2.0 * math.pi * dow / 7.0)
    df["dow_cos"] = np.cos(2.0 * math.pi * dow / 7.0)

    doy = idx.dayofyear.astype(float)
    df["doy_sin"] = np.sin(2.0 * math.pi * doy / 365.0)
    df["doy_cos"] = np.cos(2.0 * math.pi * doy / 365.0)

    # --- Calendar features ---
    df["is_weekend"] = (idx.dayofweek >= 5).astype(int)
    df["slot_index"] = idx.hour * 2 + idx.minute // 30

    # --- Temperature lag features ---
    if "outdoor_temp_c" in df.columns:
        temp = df["outdoor_temp_c"]
        df["temp_delta_1slot"] = temp.diff(1)
        df["temp_delta_24h"] = temp.diff(48)
        df["rolling_mean_6h"] = temp.rolling(12, min_periods=1).mean()
        df["temp_delta_1slot_sq"] = df["temp_delta_1slot"] ** 2
    else:
        df["temp_delta_1slot"] = np.nan
        df["temp_delta_24h"] = np.nan
        df["rolling_mean_6h"] = np.nan
        df["temp_delta_1slot_sq"] = np.nan

    # --- Nonlinear physics feature ---
    if "physics_kwh" in df.columns:
        df["physics_kwh_sq"] = df["physics_kwh"] ** 2
    else:
        df["physics_kwh_sq"] = np.nan

    # --- Octopus feature gate ---
    if not include_octopus and "octopus_import_kwh" in df.columns:
        df = df.drop(columns=["octopus_import_kwh"])

    return df


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_training_dataframe(
    power_series: pd.Series,
    temp_series: pd.Series,
    physics_series: pd.Series | None,
    octopus_series: pd.Series | None = None,
    include_octopus_feature: bool = False,
) -> pd.DataFrame:
    """Build a clean, feature-engineered training DataFrame from raw input Series.

    Pipeline stages (in order):

    1. Align and resample all series to 30-min UTC grid (inner join on index).
    2. Hard exclusions (D-12 §1-2): zeros where physics > 0.2 kWh; absolute
       upper bound > 20 kWh; ±1 slot around gaps > 15 min.
    3. EV / large-load block exclusion (D-17 revised — temperature-correlation
       discriminator).
    4. Residual z-score fencing (D-12 §3): |z| > 3.5 per ``slot_index``.
    5. Per-slot IQR fence (D-12 §4): ``actual > Q3 + 3 × IQR``.
    6. Flat-line freeze detection (D-12 §5): ≥ 6 identical non-zero slots.
    7. Quality gate (D-10): raise :exc:`InsufficientDataError` if
       ``N_clean < 500`` or ``temp_range < 5 °C``.
    8. Feature engineering (D-7): compute all 15 features + optional
       ``octopus_import_kwh``.

    All input Series are expected to carry values in **kWh per 30-min slot**
    (or °C for temperature) as returned by the ``ml/sources/`` data-ingestion
    layer.  The function normalises timezones and resamples gracefully if the
    data arrives at a finer resolution.

    Args:
        power_series: Actual household consumption (kWh/slot).
        temp_series: Outdoor temperature (°C).
        physics_series: Physics-model prediction on the same timestamps
            (kWh/slot), or ``None`` when no calibrated physics is available.
        octopus_series: Octopus grid-import series (kWh/slot). Only used
            when *include_octopus_feature* is ``True``.
        include_octopus_feature: When ``True``, a ``octopus_import_kwh``
            column is added to the returned DataFrame (D-7 feature #16).

    Returns:
        Cleaned DataFrame with a UTC ``DatetimeIndex`` and columns::

            actual_kwh, physics_kwh, outdoor_temp_c,
            hour_sin, hour_cos, dow_sin, dow_cos, doy_sin, doy_cos,
            is_weekend, slot_index, temp_delta_1slot, temp_delta_24h,
            rolling_mean_6h, temp_delta_1slot_sq, physics_kwh_sq,
            [octopus_import_kwh]

    Raises:
        InsufficientDataError: If the input Series are empty, contain no
            overlapping timestamps, or fail the D-10 quality gate.
    """
    # ------------------------------------------------------------------
    # Guard against empty inputs
    # ------------------------------------------------------------------
    if power_series is None or len(power_series) == 0:
        raise InsufficientDataError("power_series is empty")
    if temp_series is None or len(temp_series) == 0:
        raise InsufficientDataError("temp_series is empty")

    # ------------------------------------------------------------------
    # Stage 1: Align and resample to 30-min UTC grid
    # ------------------------------------------------------------------
    power_30 = _normalise_series_to_utc(power_series).resample("30min").mean()
    power_30.name = "actual_kwh"

    temp_30 = _normalise_series_to_utc(temp_series).resample("30min").mean()
    temp_30.name = "outdoor_temp_c"

    frames: dict[str, pd.Series] = {
        "actual_kwh": power_30,
        "outdoor_temp_c": temp_30,
    }

    if physics_series is not None and len(physics_series) > 0:
        phys_30 = _normalise_series_to_utc(physics_series).resample("30min").mean()
        frames["physics_kwh"] = phys_30

    if (
        include_octopus_feature
        and octopus_series is not None
        and len(octopus_series) > 0
    ):
        oct_30 = _normalise_series_to_utc(octopus_series).resample("30min").mean()
        frames["octopus_import_kwh"] = oct_30

    # Inner join: drop timestamps missing data for any present series
    df = pd.concat(frames, axis=1)
    mandatory = ["actual_kwh", "outdoor_temp_c"]
    if "physics_kwh" in df.columns:
        mandatory.append("physics_kwh")
    df = df.dropna(subset=mandatory)

    if len(df) == 0:
        raise InsufficientDataError(
            "No data remained after UTC alignment and NaN removal — "
            "power_series and temp_series have no overlapping timestamps."
        )

    # Add a NaN physics_kwh column when physics_series was not supplied so
    # downstream code (z-score fencing, feature engineering) can reference it
    # uniformly.  HistGBR handles NaN features natively.
    if "physics_kwh" not in df.columns:
        df["physics_kwh"] = np.nan

    # slot_index needed early for z-score / IQR groupby stages
    df["slot_index"] = df.index.hour * 2 + df.index.minute // 30

    _LOGGER.debug("Stage 1 complete: %d rows after alignment", len(df))

    # ------------------------------------------------------------------
    # Stage 2: Hard exclusions (D-12 §1-2)
    # ------------------------------------------------------------------
    n_before = len(df)

    # 2a: Zero / NaN readings where physics predicts demand
    physics_known = df["physics_kwh"].notna()
    sensor_gap = (
        (df["actual_kwh"].isna() | (df["actual_kwh"] <= 0.0))
        & physics_known
        & (df["physics_kwh"] > _SENSOR_ZERO_PHYSICS_THRESHOLD)
    )
    df = df[~sensor_gap]

    # 2b: Absolute upper bound
    df = df[df["actual_kwh"] <= _MAX_SLOT_KWH]

    # 2c: Temporal gap exclusion — ±1 slot around gaps > _GAP_THRESHOLD_MINUTES.
    # After resampling to 30-min and dropping NaN rows, a "gap" appears as
    # consecutive index values that are more than 30 min apart (i.e. at least one
    # 30-min slot was entirely absent from the source data — which means the source
    # had a run of missing readings exceeding _GAP_THRESHOLD_MINUTES = 15 min).
    # The comparison uses 30 min (the slot size) so that normal 30-min steps are
    # NOT flagged; only truly missing slots cause exclusion.
    if len(df) > 1:
        idx_series = df.index.to_series()
        time_diffs = idx_series.diff()
        # True at the slot IMMEDIATELY AFTER the gap (time_diffs > one slot)
        large_gap_after = time_diffs > pd.Timedelta(minutes=30)
        # True at the slot IMMEDIATELY BEFORE the gap (shift forward by 1)
        large_gap_before = large_gap_after.shift(-1, fill_value=False)
        gap_mask = large_gap_after | large_gap_before
        df = df[~gap_mask.values]

    _LOGGER.debug(
        "Stage 2 complete: %d rows remain (%d hard exclusions removed)",
        len(df),
        n_before - len(df),
    )

    if len(df) == 0:
        raise InsufficientDataError("No data remained after hard exclusions (Stage 2)")

    # ------------------------------------------------------------------
    # Stage 3: EV / large-load block exclusion (D-17 revised)
    # ------------------------------------------------------------------
    n_before = len(df)

    ev_physics_col = df["physics_kwh"] if df["physics_kwh"].notna().any() else None
    ev_temp_col = df["outdoor_temp_c"] if "outdoor_temp_c" in df.columns else None

    ev_exclusion_mask, ev_blocks = detect_ev_blocks(
        power_kwh=df["actual_kwh"],
        physics_kwh=ev_physics_col,
        outdoor_temp_c=ev_temp_col,
    )
    df = df[~ev_exclusion_mask.values]

    _LOGGER.debug(
        "Stage 3 complete: %d EV/large-load blocks excluded (%d slots); %d rows remain",
        len(ev_blocks),
        int(ev_exclusion_mask.sum()),
        len(df),
    )

    # ------------------------------------------------------------------
    # Stage 4: Residual z-score fencing (D-12 §3)
    # ------------------------------------------------------------------
    n_before = len(df)

    if df["physics_kwh"].notna().any() and len(df) > 0:
        df["_residual"] = df["actual_kwh"] - df["physics_kwh"]

        def _per_slot_zscore(group: pd.Series) -> pd.Series:
            std = group.std()
            if std == 0 or pd.isna(std):
                return pd.Series(0.0, index=group.index)
            return (group - group.mean()) / std

        residual_z = df.groupby("slot_index")["_residual"].transform(_per_slot_zscore)
        zscore_exclude = residual_z.abs() > _RESIDUAL_ZSCORE_THRESHOLD
        df = df[~zscore_exclude.fillna(False)]
        df = df.drop(columns=["_residual"])

        _LOGGER.debug(
            "Stage 4 complete: %d rows remain (z-score removed %d)",
            len(df),
            n_before - len(df),
        )

    # ------------------------------------------------------------------
    # Stage 5: Per-slot IQR fence (D-12 §4)
    # ------------------------------------------------------------------
    n_before = len(df)

    if len(df) > 0:

        def _iqr_upper_flag(group: pd.Series) -> pd.Series:
            q1 = group.quantile(0.25)
            q3 = group.quantile(0.75)
            upper = q3 + _IQR_MULTIPLIER * (q3 - q1)
            return group > upper

        iqr_exclude = df.groupby("slot_index")["actual_kwh"].transform(_iqr_upper_flag)
        df = df[~iqr_exclude.fillna(False)]

        _LOGGER.debug(
            "Stage 5 complete: %d rows remain (IQR fence removed %d)",
            len(df),
            n_before - len(df),
        )

    # ------------------------------------------------------------------
    # Stage 6: Flat-line freeze detection (D-12 §5)
    # ------------------------------------------------------------------
    n_before = len(df)

    if len(df) > 0:
        flatline_mask = _detect_flatlines(df["actual_kwh"], _FLATLINE_MIN_SLOTS)
        df = df[~flatline_mask]

        _LOGGER.debug(
            "Stage 6 complete: %d rows remain (flatline removed %d)",
            len(df),
            n_before - len(df),
        )

    # ------------------------------------------------------------------
    # Stage 7: Quality gate (D-10)
    # ------------------------------------------------------------------
    n_clean = len(df)
    temp_range = 0.0

    if "outdoor_temp_c" in df.columns and n_clean > 0:
        temp_range = float(df["outdoor_temp_c"].max() - df["outdoor_temp_c"].min())

    if n_clean < _MIN_CLEAN_SLOTS:
        raise InsufficientDataError(
            f"Quality gate failed: n_clean={n_clean} < {_MIN_CLEAN_SLOTS} "
            f"required clean slots (D-10).  Collect more data before training."
        )

    if temp_range < _MIN_TEMP_RANGE_C:
        raise InsufficientDataError(
            f"Quality gate failed: temp_range={temp_range:.2f} °C < "
            f"{_MIN_TEMP_RANGE_C} °C required (D-10).  Insufficient seasonal "
            f"variation in the training window."
        )

    _LOGGER.info(
        "Training data quality gate passed: n_clean=%d, temp_range=%.1f °C",
        n_clean,
        temp_range,
    )

    # ------------------------------------------------------------------
    # Stage 8: Feature engineering (D-7)
    # ------------------------------------------------------------------
    df = _add_features(df, include_octopus=include_octopus_feature)

    return df
