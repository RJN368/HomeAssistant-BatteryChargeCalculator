"""Unit tests for detect_ev_blocks() — the D-17 Hybrid-D algorithm.

Critical correctness test: heat pump load must NOT be excluded.
EV/flat-load MUST be excluded.
"""

import numpy as np
import pandas as pd
import pytest

from custom_components.battery_charge_calculator.ml.data_pipeline import (
    detect_ev_blocks,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def make_idx(n: int = 96) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-15", periods=n, freq="30min", tz="UTC")


def make_physics_from_temp(temps: np.ndarray) -> pd.Series:
    """Simple physics estimate: max(0, 150 * (20 - T) / 2000) kWh/slot."""
    idx = pd.date_range("2024-01-15", periods=len(temps), freq="30min", tz="UTC")
    vals = np.maximum(0.0, 150.0 * (20.0 - temps) / 2000.0)
    return pd.Series(vals, index=idx, dtype=float)


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------


class TestFlatEvBlockDetected:
    def test_flat_ev_block_detected(self):
        """A 10-slot flat temperature-independent load is flagged as EV."""
        idx = make_idx(96)
        rng = np.random.default_rng(0)

        # Background: low variable consumption; slightly varying temp (avoids
        # pearsonr NaN from constant input, keeps r ≈ 0 for the EV window).
        actual = rng.normal(0.3, 0.05, 96)
        physics = np.full(96, 0.3)
        temps = np.linspace(9.5, 10.5, 96)  # tiny range, no heating evidence

        # EV block: slots 20-29 — flat, high, temperature-independent
        actual[20:30] = 2.5
        physics[20:30] = 0.3  # residual = 2.2 kWh >> threshold

        power_kwh = pd.Series(actual, index=idx)
        physics_kwh = pd.Series(physics, index=idx)
        outdoor_temp_c = pd.Series(temps, index=idx)

        mask, blocks = detect_ev_blocks(power_kwh, physics_kwh, outdoor_temp_c)

        # The 10 EV slots must be in the exclusion mask
        assert mask.iloc[20:30].all(), (
            "Slots 20-29 (EV block) should all be flagged for exclusion"
        )
        assert len(blocks) >= 1


class TestHeatPumpBlockPreserved:
    def test_heat_pump_block_preserved(self):
        """Consumption anti-correlated with temperature is NOT excluded.

        Slots 20-35 show elevated actual consumption (1.8 kWh/slot) with a
        physics-model residual > 1.0 kWh, so they qualify as candidates.
        However, the temperature drops from 5°C to -5°C across those slots —
        strong negative correlation (r << -0.4) — so the algorithm must keep them.
        """
        idx = make_idx(96)

        actual = np.full(96, 0.3)
        physics = np.full(96, 0.3)
        temps = np.full(96, 10.0)  # warm background

        # Heat-pump block: slots 20-35 — high load coinciding with cold snap
        actual[20:36] = 1.8
        # Physics underestimates (e.g., uncalibrated model), leaving residual=1.5
        physics[20:36] = 0.3
        # Temperature drops linearly from 5°C to -5°C across the block
        temps[20:36] = np.linspace(5.0, -5.0, 16)

        power_kwh = pd.Series(actual, index=idx)
        physics_kwh = pd.Series(physics, index=idx)
        outdoor_temp_c = pd.Series(temps, index=idx)

        mask, _ = detect_ev_blocks(power_kwh, physics_kwh, outdoor_temp_c)

        assert not mask.iloc[20:36].any(), (
            "Heat-pump slots (strongly anti-correlated with temperature) "
            "must NOT be excluded"
        )


class TestShortRunNotExcluded:
    def test_short_run_not_excluded(self):
        """A 2-slot high-residual run is below MIN_RUN_SLOTS=3 and must not be flagged."""
        idx = make_idx(96)

        actual = np.full(96, 0.3)
        actual[40:42] = 3.0  # only 2 consecutive high slots

        physics = np.full(96, 0.3)
        temps = np.linspace(9.5, 10.5, 96)

        power_kwh = pd.Series(actual, index=idx)
        physics_kwh = pd.Series(physics, index=idx)
        outdoor_temp_c = pd.Series(temps, index=idx)

        mask, _ = detect_ev_blocks(power_kwh, physics_kwh, outdoor_temp_c)

        assert not mask.iloc[40].item(), (
            "Slot 40 should not be excluded (run too short)"
        )
        assert not mask.iloc[41].item(), (
            "Slot 41 should not be excluded (run too short)"
        )


class TestBufferSlotsApplied:
    def test_buffer_slots_applied(self):
        """±1 buffer around each excluded run: slot before and after are also masked."""
        idx = make_idx(96)

        actual = np.full(96, 0.3)
        actual[10:15] = 3.0  # 5 consecutive slots at positions 10-14

        physics = np.full(96, 0.3)
        temps = np.linspace(9.5, 10.5, 96)  # near-constant → r ≈ 0 → EV path

        power_kwh = pd.Series(actual, index=idx)
        physics_kwh = pd.Series(physics, index=idx)
        outdoor_temp_c = pd.Series(temps, index=idx)

        mask, _ = detect_ev_blocks(power_kwh, physics_kwh, outdoor_temp_c)

        # Core block
        assert mask.iloc[10:15].all(), "Core EV block slots should be excluded"
        # Buffer slots
        assert mask.iloc[9].item(), "Buffer slot before block (9) should be excluded"
        assert mask.iloc[15].item(), "Buffer slot after block (15) should be excluded"


class TestColdStartFlatLoad:
    def test_cold_start_flat_load_excluded(self):
        """Cold start (no physics/temp): a flat sustained load block is excluded via CV.

        Needs ≥ 400 background slots so that the flat-block value exceeds the
        p98-derived threshold (the 6 flat slots represent < 2% of the total,
        so p98 equals the background level, and absolute_threshold falls back
        to _COLD_START_FLOOR_KWH = 2.5 kWh; flat value = 3.0 kWh > 2.5).
        """
        n = 400
        idx = pd.date_range("2024-01-15", periods=n, freq="30min", tz="UTC")
        actual = np.full(n, 0.3)
        actual[200:206] = 3.0  # 6 consecutive flat slots at 3.0 kWh, CV = 0

        power_kwh = pd.Series(actual, index=idx)

        mask, blocks = detect_ev_blocks(power_kwh, None, None)

        assert mask.iloc[200:206].all(), (
            "Cold-start flat-load block should be excluded via temporal CV fallback"
        )

    def test_cold_start_variable_load_kept(self):
        """Cold start (no physics/temp): a variable high-load block is NOT excluded.

        CV = std/mean > 0.20 (the _CV_EV_THRESHOLD) signals genuine variability
        consistent with appliances/cooking rather than constant EV charging.
        """
        n = 400
        idx = pd.date_range("2024-01-15", periods=n, freq="30min", tz="UTC")
        actual = np.full(n, 0.3)
        # Alternating 3.0 / 5.0 kWh → mean=4.0, std=1.0, CV=0.25 > 0.20
        actual[200:206] = [3.0, 5.0, 3.0, 5.0, 3.0, 5.0]

        power_kwh = pd.Series(actual, index=idx)

        mask, _ = detect_ev_blocks(power_kwh, None, None)

        assert not mask.iloc[200:206].any(), (
            "Variable cold-start load block should NOT be excluded (CV > threshold)"
        )


class TestEvBlocksListPopulated:
    def test_ev_blocks_list_populated(self):
        """ev_blocks list is non-empty and each entry has the required keys."""
        idx = make_idx(96)
        rng = np.random.default_rng(1)

        actual = rng.normal(0.3, 0.05, 96)
        physics = np.full(96, 0.3)
        temps = np.linspace(9.5, 10.5, 96)
        actual[30:40] = 2.5  # 10-slot EV block

        power_kwh = pd.Series(actual, index=idx)
        physics_kwh = pd.Series(physics, index=idx)
        outdoor_temp_c = pd.Series(temps, index=idx)

        mask, blocks = detect_ev_blocks(power_kwh, physics_kwh, outdoor_temp_c)

        assert len(blocks) >= 1, "Expected at least one detected EV block"
        required_keys = {"start", "end", "n_slots", "mean_kwh", "detection_mode"}
        for block in blocks:
            missing = required_keys - block.keys()
            assert not missing, f"Block dict is missing keys: {missing}"


class TestNormalLoadLowExclusionRate:
    def test_normal_load_low_exclusion_rate(self):
        """90-day physics-consistent load produces < 2% EV exclusion rate."""
        n = 4320  # 90 days × 48 slots
        rng = np.random.default_rng(42)
        idx = pd.date_range("2024-01-01", periods=n, freq="30min", tz="UTC")

        temps = np.linspace(-5, 15, n)
        physics = np.clip(0.4 - temps * 0.02, 0.05, 2.0)
        actual = np.clip(physics + rng.normal(0, 0.05, n), 0.0, None)

        power_kwh = pd.Series(actual, index=idx)
        physics_kwh = pd.Series(physics, index=idx)
        outdoor_temp_c = pd.Series(temps, index=idx)

        mask, _ = detect_ev_blocks(power_kwh, physics_kwh, outdoor_temp_c)

        ev_excluded_fraction = mask.sum() / len(mask)
        assert ev_excluded_fraction < 0.02, (
            f"False-positive EV exclusion rate too high: "
            f"{ev_excluded_fraction:.2%} (expected < 2%)"
        )


class TestReturnsBoolSeries:
    def test_returns_bool_series(self):
        """First return value is a pd.Series of dtype bool with same index as input."""
        idx = make_idx(96)
        actual = np.full(96, 0.3)
        power_kwh = pd.Series(actual, index=idx)

        result, _ = detect_ev_blocks(power_kwh, None, None)

        assert isinstance(result, pd.Series), "exclusion_mask must be a pd.Series"
        assert result.dtype == bool, (
            f"exclusion_mask dtype should be bool, got {result.dtype}"
        )
        assert result.index.equals(power_kwh.index), (
            "exclusion_mask index must match the input power_kwh index"
        )
