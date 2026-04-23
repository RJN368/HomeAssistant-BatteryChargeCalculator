"""Unit tests for tariff_comparison.ha_solar_history.fetch_solar_history.

The HA recorder is fully stubbed — no real HA installation needed.
"""

from __future__ import annotations

import sys
import types
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

UTC = timezone.utc
PERIOD_FROM = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
PERIOD_TO = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
ENTITY_ID = "sensor.solar_energy_production"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stat_row(start: datetime, cumulative_sum: float):
    """Return a minimal StatisticsRow-like object."""
    row = MagicMock()
    row.start = start
    row.sum = cumulative_sum
    return row


def _make_hass(stats_return: dict | None = None):
    """Build a minimal hass stub with a recorder instance."""
    recorder_instance = MagicMock()

    # async_add_executor_job runs the callable immediately in tests
    async def _executor_job(func, *args):
        return func(*args)

    recorder_instance.async_add_executor_job = _executor_job

    hass = MagicMock()

    # get_instance(hass) returns our recorder_instance
    return hass, recorder_instance, stats_return or {}


def _patch_recorder(recorder_instance, stats_return: dict):
    """Context-manager patches for recorder imports inside ha_solar_history."""
    recorder_mod = types.ModuleType("homeassistant.components.recorder")
    recorder_mod.get_instance = lambda hass: recorder_instance

    stats_mod = types.ModuleType("homeassistant.components.recorder.statistics")
    stats_mod.statistics_during_period = lambda *a, **kw: stats_return

    return patch.dict(
        sys.modules,
        {
            "homeassistant.components.recorder": recorder_mod,
            "homeassistant.components.recorder.statistics": stats_mod,
        },
    )


# ---------------------------------------------------------------------------
# Fetch: empty / no entity
# ---------------------------------------------------------------------------


class TestFetchEdgeCases:
    """Boundary conditions that produce empty results."""

    @pytest.mark.asyncio
    async def test_empty_entity_id_returns_empty(self):
        """fetch_solar_history(entity_id='') must return {} without touching HA."""
        from custom_components.battery_charge_calculator.tariff_comparison.ha_solar_history import (
            fetch_solar_history,
        )

        hass = MagicMock()
        result = await fetch_solar_history(hass, "", PERIOD_FROM, PERIOD_TO)
        assert result == {}

    @pytest.mark.asyncio
    async def test_no_stats_rows_returns_empty(self):
        """When HA returns no rows for the entity, return {}."""
        from custom_components.battery_charge_calculator.tariff_comparison.ha_solar_history import (
            fetch_solar_history,
        )

        hass, recorder_instance, _ = _make_hass()
        with _patch_recorder(recorder_instance, {ENTITY_ID: []}):
            result = await fetch_solar_history(hass, ENTITY_ID, PERIOD_FROM, PERIOD_TO)
        assert result == {}

    @pytest.mark.asyncio
    async def test_entity_absent_from_stats_returns_empty(self):
        """When entity_id not present in stats dict at all, return {}."""
        from custom_components.battery_charge_calculator.tariff_comparison.ha_solar_history import (
            fetch_solar_history,
        )

        hass, recorder_instance, _ = _make_hass()
        with _patch_recorder(recorder_instance, {}):
            result = await fetch_solar_history(hass, ENTITY_ID, PERIOD_FROM, PERIOD_TO)
        assert result == {}

    @pytest.mark.asyncio
    async def test_single_row_no_diff_returns_empty(self):
        """Only one row → can't compute a delta → return {}."""
        from custom_components.battery_charge_calculator.tariff_comparison.ha_solar_history import (
            fetch_solar_history,
        )

        row = _make_stat_row(datetime(2026, 2, 28, 23, 0, tzinfo=UTC), 100.0)
        hass, recorder_instance, _ = _make_hass()
        with _patch_recorder(recorder_instance, {ENTITY_ID: [row]}):
            result = await fetch_solar_history(hass, ENTITY_ID, PERIOD_FROM, PERIOD_TO)
        assert result == {}

    @pytest.mark.asyncio
    async def test_all_null_sums_returns_empty(self):
        """Rows where sum is None cannot be differenced → return {}."""
        from custom_components.battery_charge_calculator.tariff_comparison.ha_solar_history import (
            fetch_solar_history,
        )

        rows = [
            _make_stat_row(datetime(2026, 3, 1, h, 0, tzinfo=UTC), None)
            for h in range(5)
        ]
        hass, recorder_instance, _ = _make_hass()
        with _patch_recorder(recorder_instance, {ENTITY_ID: rows}):
            result = await fetch_solar_history(hass, ENTITY_ID, PERIOD_FROM, PERIOD_TO)
        assert result == {}


# ---------------------------------------------------------------------------
# Fetch: normal data
# ---------------------------------------------------------------------------


class TestFetchNormalData:
    """Happy-path scenarios with realistic stats data."""

    def _make_march_rows(
        self, hourly_kwh: dict[int, float], base_sum: float = 0.0
    ) -> list:
        """Build a sequence of cumulative StatisticsRow objects for March 2026.

        *hourly_kwh* maps hour-of-day (0–23) of March 1 to kWh generated.
        The seed row is at 2026-02-28 23:00 UTC (before the window) with
        cumulative sum = base_sum.
        """
        rows = []
        cumulative = base_sum
        # seed: hour before window starts
        rows.append(
            _make_stat_row(datetime(2026, 2, 28, 23, 0, tzinfo=UTC), cumulative)
        )
        for h in range(24):
            cumulative += hourly_kwh.get(h, 0.0)
            rows.append(
                _make_stat_row(datetime(2026, 3, 1, h, 0, tzinfo=UTC), cumulative)
            )
        return rows

    @pytest.mark.asyncio
    async def test_48_slots_returned_per_day(self):
        """Result must contain exactly 48 slots for each day in the window."""
        from custom_components.battery_charge_calculator.tariff_comparison.ha_solar_history import (
            fetch_solar_history,
        )

        rows = self._make_march_rows({})  # all zeros
        hass, recorder_instance, _ = _make_hass()
        # Return data only for Mar 1; remaining days get zeros from fallback
        with _patch_recorder(recorder_instance, {ENTITY_ID: rows}):
            result = await fetch_solar_history(
                hass,
                ENTITY_ID,
                datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
                datetime(2026, 3, 3, 0, 0, tzinfo=UTC),  # 2 days
            )
        assert len(result) == 2
        for day_slots in result.values():
            assert len(day_slots) == 48

    @pytest.mark.asyncio
    async def test_dict_style_statistics_rows_are_supported(self):
        """HA returns dict-style StatisticsRow; fetch_solar_history must parse it."""
        from custom_components.battery_charge_calculator.tariff_comparison.ha_solar_history import (
            fetch_solar_history,
        )

        rows = [
            {
                "start": datetime(2026, 2, 28, 23, 0, tzinfo=UTC),
                "sum": 100.0,
            },
            {
                "start": datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
                "sum": 101.0,
            },
        ]
        hass, recorder_instance, _ = _make_hass()
        with _patch_recorder(recorder_instance, {ENTITY_ID: rows}):
            result = await fetch_solar_history(
                hass,
                ENTITY_ID,
                datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
                datetime(2026, 3, 2, 0, 0, tzinfo=UTC),
            )

        assert date(2026, 3, 1) in result
        # 1.0 kWh for the first hour -> split into 0.5 + 0.5
        assert result[date(2026, 3, 1)][0] == pytest.approx(0.5)
        assert result[date(2026, 3, 1)][1] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_hourly_split_into_two_30min_halves(self):
        """Each hourly kWh must appear in two equal 30-min half-slots."""
        from custom_components.battery_charge_calculator.tariff_comparison.ha_solar_history import (
            fetch_solar_history,
        )

        # 1 kWh at noon (hour 12)
        rows = self._make_march_rows({12: 1.0})
        hass, recorder_instance, _ = _make_hass()
        with _patch_recorder(recorder_instance, {ENTITY_ID: rows}):
            result = await fetch_solar_history(
                hass,
                ENTITY_ID,
                datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
                datetime(2026, 3, 2, 0, 0, tzinfo=UTC),
            )
        assert date(2026, 3, 1) in result
        slots = result[date(2026, 3, 1)]
        # Slot 24 = hour 12 HH:00, slot 25 = hour 12 HH:30
        assert slots[24] == pytest.approx(0.5)
        assert slots[25] == pytest.approx(0.5)
        # All other slots should be 0
        assert all(
            s == pytest.approx(0.0) for i, s in enumerate(slots) if i not in (24, 25)
        )

    @pytest.mark.asyncio
    async def test_negative_delta_clamped_to_zero(self):
        """Decreasing cumulative sum (e.g. meter reset) must produce 0.0, not negative."""
        from custom_components.battery_charge_calculator.tariff_comparison.ha_solar_history import (
            fetch_solar_history,
        )

        # Simulate a reset: sum goes from 100 → 5 between hours 5 and 6
        rows = [
            _make_stat_row(datetime(2026, 2, 28, 23, 0, tzinfo=UTC), 100.0),
            _make_stat_row(datetime(2026, 3, 1, 0, 0, tzinfo=UTC), 101.0),
            _make_stat_row(datetime(2026, 3, 1, 1, 0, tzinfo=UTC), 103.0),
            _make_stat_row(datetime(2026, 3, 1, 2, 0, tzinfo=UTC), 104.0),
            _make_stat_row(datetime(2026, 3, 1, 3, 0, tzinfo=UTC), 104.0),  # no gen
            _make_stat_row(datetime(2026, 3, 1, 4, 0, tzinfo=UTC), 104.0),  # no gen
            _make_stat_row(datetime(2026, 3, 1, 5, 0, tzinfo=UTC), 5.0),  # reset!
            _make_stat_row(datetime(2026, 3, 1, 6, 0, tzinfo=UTC), 6.5),
        ]
        hass, recorder_instance, _ = _make_hass()
        with _patch_recorder(recorder_instance, {ENTITY_ID: rows}):
            result = await fetch_solar_history(
                hass,
                ENTITY_ID,
                datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
                datetime(2026, 3, 2, 0, 0, tzinfo=UTC),
            )
        slots = result[date(2026, 3, 1)]
        # Hour 5 had a reset (delta = -99) → clamped to 0.0
        assert slots[10] == pytest.approx(0.0)
        assert slots[11] == pytest.approx(0.0)
        # Hour 6 = 1.5 kWh → 0.75 per slot
        assert slots[12] == pytest.approx(0.75)
        assert slots[13] == pytest.approx(0.75)

    @pytest.mark.asyncio
    async def test_missing_hours_fill_with_zero(self):
        """Hours without a stats row default to 0.0 kWh for both slots."""
        from custom_components.battery_charge_calculator.tariff_comparison.ha_solar_history import (
            fetch_solar_history,
        )

        # Only provide one non-zero hour; all others absent from stats
        rows = [
            _make_stat_row(datetime(2026, 2, 28, 23, 0, tzinfo=UTC), 0.0),
            _make_stat_row(datetime(2026, 3, 1, 10, 0, tzinfo=UTC), 2.0),  # +2 kWh
            _make_stat_row(datetime(2026, 3, 1, 11, 0, tzinfo=UTC), 2.0),  # no gen
        ]
        hass, recorder_instance, _ = _make_hass()
        with _patch_recorder(recorder_instance, {ENTITY_ID: rows}):
            result = await fetch_solar_history(
                hass,
                ENTITY_ID,
                datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
                datetime(2026, 3, 2, 0, 0, tzinfo=UTC),
            )
        slots = result[date(2026, 3, 1)]
        # Slots 0–19 (hours 0–9) → no rows; default 0.0
        for i in range(20):
            assert slots[i] == pytest.approx(0.0), f"slot {i} should be 0.0"
        # Hour 10: delta = 2.0 → 1.0 per half
        assert slots[20] == pytest.approx(1.0)
        assert slots[21] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_total_daily_kwh_preserved(self):
        """Sum of all 48 slots for a day must equal total kWh generated that day."""
        from custom_components.battery_charge_calculator.tariff_comparison.ha_solar_history import (
            fetch_solar_history,
        )

        # Simulate a realistic solar day: generation from hours 7–18
        hourly = {h: 0.5 for h in range(7, 19)}  # 6 kWh total
        rows = self._make_march_rows(hourly)
        hass, recorder_instance, _ = _make_hass()
        with _patch_recorder(recorder_instance, {ENTITY_ID: rows}):
            result = await fetch_solar_history(
                hass,
                ENTITY_ID,
                datetime(2026, 3, 1, 0, 0, tzinfo=UTC),
                datetime(2026, 3, 2, 0, 0, tzinfo=UTC),
            )
        day_slots = result[date(2026, 3, 1)]
        total = sum(day_slots)
        assert total == pytest.approx(6.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_executor_error_returns_empty(self):
        """If the recorder executor raises, return {} (graceful degradation)."""
        from custom_components.battery_charge_calculator.tariff_comparison.ha_solar_history import (
            fetch_solar_history,
        )

        recorder_instance = MagicMock()

        async def _failing_executor(func, *args):
            raise RuntimeError("recorder unavailable")

        recorder_instance.async_add_executor_job = _failing_executor
        hass = MagicMock()

        recorder_mod = types.ModuleType("homeassistant.components.recorder")
        recorder_mod.get_instance = lambda h: recorder_instance
        stats_mod = types.ModuleType("homeassistant.components.recorder.statistics")
        stats_mod.statistics_during_period = MagicMock()

        with patch.dict(
            sys.modules,
            {
                "homeassistant.components.recorder": recorder_mod,
                "homeassistant.components.recorder.statistics": stats_mod,
            },
        ):
            result = await fetch_solar_history(hass, ENTITY_ID, PERIOD_FROM, PERIOD_TO)
        assert result == {}


# ---------------------------------------------------------------------------
# simulate_day: solar_data_30min parameter
# ---------------------------------------------------------------------------


class TestSimulateDaySolarParam:
    """Verify that simulator.py uses solar_data_30min correctly."""

    def _make_rate_map(self, day: date, rate: float = 20.0) -> dict:
        """48-slot rate map for *day* at a flat *rate* p/kWh."""
        from datetime import datetime, timezone

        slots = {}
        for slot in range(48):
            h, m = divmod(slot, 2)
            minute = 0 if m == 0 else 30
            slots[
                datetime(day.year, day.month, day.day, h, minute, tzinfo=timezone.utc)
            ] = rate
        return slots

    def test_solar_none_does_not_raise(self):
        """simulate_day(solar_data_30min=None) must complete without error."""
        from custom_components.battery_charge_calculator.tariff_comparison.simulator import (
            TariffSimulator,
        )
        from custom_components.battery_charge_calculator.power_calculator import (
            PowerCalulator,
        )

        sim = TariffSimulator()
        day = date(2026, 3, 1)
        rate_map = self._make_rate_map(day)
        pc = PowerCalulator()
        # Should not raise
        result = sim.simulate_day(
            date_obj=day,
            hourly_temps=[10.0] * 24,
            rate_map_import=rate_map,
            rate_map_export=None,
            power_calculator=pc,
            inverter_size_kw=3.6,
            inverter_efficiency=0.97,
            battery_capacity_kwh=9.5,
            battery_start_kwh=4.75,
            solar_data_30min=None,
        )
        assert "import_cost_pence" in result
        assert "export_earnings_pence" in result

    def test_solar_48_zeros_same_as_none(self):
        """Passing 48 zeros should produce the same result as passing None.

        The optimizer is stochastic, so we seed the RNG before each run.
        """
        import random

        from custom_components.battery_charge_calculator.tariff_comparison.simulator import (
            TariffSimulator,
        )
        from custom_components.battery_charge_calculator.power_calculator import (
            PowerCalulator,
        )

        sim = TariffSimulator()
        day = date(2026, 3, 1)
        rate_map = self._make_rate_map(day)
        pc = PowerCalulator()
        kwargs = dict(
            date_obj=day,
            hourly_temps=[10.0] * 24,
            rate_map_import=rate_map,
            rate_map_export=None,
            power_calculator=pc,
            inverter_size_kw=3.6,
            inverter_efficiency=0.97,
            battery_capacity_kwh=9.5,
            battery_start_kwh=4.75,
        )
        random.seed(42)
        result_none = sim.simulate_day(**kwargs, solar_data_30min=None)
        random.seed(42)
        result_zeros = sim.simulate_day(**kwargs, solar_data_30min=[0.0] * 48)
        assert result_none["import_cost_pence"] == pytest.approx(
            result_zeros["import_cost_pence"], abs=1e-3
        )

    def test_short_solar_list_padded_with_zeros(self):
        """If solar list is shorter than 48, missing slots use 0.0 (no IndexError)."""
        from custom_components.battery_charge_calculator.tariff_comparison.simulator import (
            TariffSimulator,
        )
        from custom_components.battery_charge_calculator.power_calculator import (
            PowerCalulator,
        )

        sim = TariffSimulator()
        day = date(2026, 3, 1)
        rate_map = self._make_rate_map(day)
        pc = PowerCalulator()
        # Only 10 solar values — should not raise
        result = sim.simulate_day(
            date_obj=day,
            hourly_temps=[10.0] * 24,
            rate_map_import=rate_map,
            rate_map_export=None,
            power_calculator=pc,
            inverter_size_kw=3.6,
            inverter_efficiency=0.97,
            battery_capacity_kwh=9.5,
            battery_start_kwh=4.75,
            solar_data_30min=[0.5] * 10,
        )
        assert "import_cost_pence" in result
