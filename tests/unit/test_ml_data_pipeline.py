"""Unit tests for ml/data_pipeline.py.

Tests cover feature engineering, anomaly detection, quality gate,
and the InsufficientDataError exception.
"""

import math

import numpy as np
import pandas as pd
import pytest

from custom_components.battery_charge_calculator.ml.data_pipeline import (
    InsufficientDataError,
    build_training_dataframe,
    detect_ev_blocks,
    resample_to_30min,
)

# ---------------------------------------------------------------------------
# Feature columns — mirrors model_trainer.FEATURE_COLUMNS (hardcoded to avoid
# pulling in model_trainer which may have transitive HA imports at test time)
# ---------------------------------------------------------------------------
FEATURE_COLUMNS = [
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

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def make_utc_index(n: int, start: str = "2024-01-15") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="30min", tz="UTC")


def make_clean_df(n: int = 600) -> pd.DataFrame:
    """Generate a minimal clean DataFrame that passes the quality gate."""
    rng = np.random.default_rng(42)
    idx = make_utc_index(n)
    temps = np.linspace(-5, 15, n)  # 20°C range — passes temp gate
    physics = np.clip(0.4 - temps * 0.02, 0.05, 2.0)
    return pd.DataFrame(
        {
            "actual_kwh": physics + rng.normal(0, 0.05, n),
            "physics_kwh": physics,
            "outdoor_temp_c": temps,
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# build_training_dataframe tests
# ---------------------------------------------------------------------------


class TestBuildReturnsDataFrame:
    def test_build_returns_dataframe(self):
        """build_training_dataframe returns a non-empty pd.DataFrame."""
        df_in = make_clean_df(600)
        result = build_training_dataframe(
            power_series=df_in["actual_kwh"],
            temp_series=df_in["outdoor_temp_c"],
            physics_series=df_in["physics_kwh"],
        )
        assert isinstance(result, pd.DataFrame)
        assert len(result) > 0


class TestFeatureColumns:
    def test_feature_columns_all_present(self):
        """All 14 FEATURE_COLUMNS are present in the result DataFrame."""
        df_in = make_clean_df(600)
        result = build_training_dataframe(
            power_series=df_in["actual_kwh"],
            temp_series=df_in["outdoor_temp_c"],
            physics_series=df_in["physics_kwh"],
        )
        for col in FEATURE_COLUMNS:
            assert col in result.columns, f"Missing feature column: {col}"


class TestCircularTimeEncoding:
    def test_circular_time_no_discontinuity(self):
        """hour_sin / hour_cos have no large jumps at midnight boundaries.

        In circular encoding the step from 23:30 → 00:00 must not exceed 0.3
        in combined sqrt(Δsin² + Δcos²) — the 30-min step produces ≈ 0.13.
        """
        n = 1440  # 30 days × 48 slots
        df_in = make_clean_df(n)
        result = build_training_dataframe(
            power_series=df_in["actual_kwh"],
            temp_series=df_in["outdoor_temp_c"],
            physics_series=df_in["physics_kwh"],
        )
        d_sin = result["hour_sin"].diff().dropna()
        d_cos = result["hour_cos"].diff().dropna()
        jump = np.sqrt(d_sin**2 + d_cos**2)
        assert jump.max() <= 0.3, f"Discontinuity detected: max jump = {jump.max():.4f}"


class TestQualityGate:
    def test_quality_gate_insufficient_slots(self):
        """Fewer than 500 clean rows raises InsufficientDataError."""
        df_in = make_clean_df(400)  # well under the 500-slot minimum
        with pytest.raises(InsufficientDataError):
            build_training_dataframe(
                power_series=df_in["actual_kwh"],
                temp_series=df_in["outdoor_temp_c"],
                physics_series=df_in["physics_kwh"],
            )

    def test_quality_gate_narrow_temp_range(self):
        """Temperature range < 5°C raises InsufficientDataError."""
        n = 600
        rng = np.random.default_rng(7)
        idx = make_utc_index(n)
        physics = np.full(n, 0.3)
        df_in = pd.DataFrame(
            {
                "actual_kwh": physics + rng.normal(0, 0.05, n),
                "physics_kwh": physics,
                "outdoor_temp_c": np.full(n, 10.0),  # zero range → fails gate
            },
            index=idx,
        )
        with pytest.raises(InsufficientDataError):
            build_training_dataframe(
                power_series=df_in["actual_kwh"],
                temp_series=df_in["outdoor_temp_c"],
                physics_series=df_in["physics_kwh"],
            )


class TestAnomalyExclusion:
    def test_flatline_excluded(self):
        """8 consecutive identical non-zero actual_kwh values are not in output."""
        df_in = make_clean_df(700).copy()

        # Use the same value for both actual and physics at positions 100-107 so
        # residual = 0 (avoids z-score / EV exclusion).  Choose flat_val ≈ physics
        # at those temps so the IQR fence also leaves it alone.
        flat_val = float(df_in["physics_kwh"].iloc[104])
        df_in.iloc[100:108, df_in.columns.get_loc("actual_kwh")] = flat_val
        df_in.iloc[100:108, df_in.columns.get_loc("physics_kwh")] = flat_val

        flatline_timestamps = df_in.index[100:108]

        result = build_training_dataframe(
            power_series=df_in["actual_kwh"],
            temp_series=df_in["outdoor_temp_c"],
            physics_series=df_in["physics_kwh"],
        )

        overlap = flatline_timestamps.isin(result.index)
        assert not overlap.any(), (
            f"Flatline timestamps still present in result: "
            f"{flatline_timestamps[overlap].tolist()}"
        )

    def test_large_values_excluded(self):
        """Slots with actual_kwh = 25 (> _MAX_SLOT_KWH=20) are removed."""
        df_in = make_clean_df(700).copy()
        target_timestamps = df_in.index[50:53]
        df_in.loc[target_timestamps, "actual_kwh"] = 25.0

        result = build_training_dataframe(
            power_series=df_in["actual_kwh"],
            temp_series=df_in["outdoor_temp_c"],
            physics_series=df_in["physics_kwh"],
        )

        overlap = target_timestamps.isin(result.index)
        assert not overlap.any(), (
            "Large-value rows should have been excluded by the hard upper-bound filter"
        )


class TestOctopusFeature:
    def test_octopus_feature_included_when_requested(self):
        """octopus_import_kwh column present when include_octopus_feature=True."""
        df_in = make_clean_df(600)
        octopus = pd.Series(0.3, index=df_in.index)
        result = build_training_dataframe(
            power_series=df_in["actual_kwh"],
            temp_series=df_in["outdoor_temp_c"],
            physics_series=df_in["physics_kwh"],
            octopus_series=octopus,
            include_octopus_feature=True,
        )
        assert "octopus_import_kwh" in result.columns

    def test_octopus_feature_absent_when_not_requested(self):
        """octopus_import_kwh column absent when include_octopus_feature=False."""
        df_in = make_clean_df(600)
        octopus = pd.Series(0.3, index=df_in.index)
        result = build_training_dataframe(
            power_series=df_in["actual_kwh"],
            temp_series=df_in["outdoor_temp_c"],
            physics_series=df_in["physics_kwh"],
            octopus_series=octopus,
            include_octopus_feature=False,
        )
        assert "octopus_import_kwh" not in result.columns


# ---------------------------------------------------------------------------
# resample_to_30min tests
# ---------------------------------------------------------------------------


class TestResampleTo30Min:
    def test_resample_to_30min_instantaneous(self):
        """Instantaneous Watts resampled to 30-min kWh are in a plausible range.

        A 3 kW heater = 3000 W → 3000 × 0.5 / 1000 = 1.5 kWh/slot.
        Input at hourly resolution; each filled 30-min window must contain
        a positive value below 5.0 kWh.
        """
        idx = pd.date_range("2024-01-01", periods=24, freq="1h", tz="UTC")
        series = pd.Series(3000.0, index=idx)  # 3 kW constant

        result = resample_to_30min(series, is_cumulative=False)

        # The 30-min grid is created by resample; verify spacing.
        assert (result.index[1] - result.index[0]) == pd.Timedelta("30min")

        non_nan = result.dropna()
        assert len(non_nan) > 0
        assert (non_nan > 0.0).all()
        assert (non_nan < 5.0).all()

    def test_resample_to_30min_cumulative(self):
        """Cumulative energy resampled to 30-min slots yields positive kWh values."""
        # 6 hours of 5-min readings: monotonically increasing energy register
        # 0 → 6 kWh across 72 readings → ~0.5 kWh per 30-min slot
        idx = pd.date_range("2024-01-01", periods=72, freq="5min", tz="UTC")
        vals = np.linspace(0, 6, 72)
        series = pd.Series(vals, index=idx)

        result = resample_to_30min(series, is_cumulative=True)

        assert (result.index[1] - result.index[0]) == pd.Timedelta("30min")

        non_nan = result.dropna()
        assert len(non_nan) > 0
        assert (non_nan >= 0.0).all(), "Cumulative diff should never be negative"
        assert (non_nan < 5.0).all(), "Expected < 5 kWh per slot for a 1 kWh/hr meter"
