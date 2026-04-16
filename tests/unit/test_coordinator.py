import sys
import warnings
from zoneinfo import ZoneInfo

"""Unit tests for the BatteryChargeCoordinator."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock, patch, call

import pytest

from custom_components.battery_charge_calculator import const
from custom_components.battery_charge_calculator.coordinators import (
    BatteryChargeCoordinator,
)
from custom_components.battery_charge_calculator.genetic_evaluator import Timeslot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SLOT_DT = datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc)


def _make_timeslot(dt=None, charge_option="discharge"):
    slot = Timeslot(
        dt or SLOT_DT,
        import_price=0.25,
        export_price=0.15,
        demand_in=0.3,
        solar_in=0.0,
    )
    slot.charge_option = charge_option
    return slot


def _make_coordinator(simulate=True):
    """Build a coordinator with all external dependencies mocked."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.options = {
        const.OCTOPUS_APIKEY: "test-key",
        const.OCTOPUS_ACCOUNT_NUMBER: "A-1111",
        const.GIVENERGY_SERIAL_NUMBER: "SN001",
        const.GIVENERGY_API_TOKEN: "token",
        const.SIMULATE_ONLY: simulate,
    }

    hass = MagicMock()
    hass.config.time_zone = "Europe/London"
    hass.loop = MagicMock()

    with (
        patch(
            "custom_components.battery_charge_calculator.coordinators.OctopusAgileRatesClient"
        ),
        patch("custom_components.battery_charge_calculator.coordinators.givenergy"),
        patch(
            "custom_components.battery_charge_calculator.coordinators.power_calculator"
        ),
        patch(
            "custom_components.battery_charge_calculator.coordinators.dt_util.get_time_zone",
            return_value=timezone.utc,
        ),
    ):
        coordinator = BatteryChargeCoordinator(hass, entry)

    coordinator.hass = hass
    coordinator.config_entry = entry
    return coordinator


# ---------------------------------------------------------------------------
# ceil_dt
# ---------------------------------------------------------------------------


class TestCeilDt:
    def test_rounds_up_to_next_30_min(self):
        coord = _make_coordinator()
        dt = datetime(2026, 4, 4, 12, 10, 0)
        result = coord.ceil_dt(dt, timedelta(minutes=30))
        assert result == datetime(2026, 4, 4, 12, 30, 0)

    def test_already_on_boundary_stays_same(self):
        coord = _make_coordinator()
        dt = datetime(2026, 4, 4, 12, 30, 0)
        result = coord.ceil_dt(dt, timedelta(minutes=30))
        assert result == datetime(2026, 4, 4, 12, 30, 0)

    def test_rounds_minute_1_up(self):
        coord = _make_coordinator()
        dt = datetime(2026, 4, 4, 6, 1, 0)
        result = coord.ceil_dt(dt, timedelta(minutes=30))
        assert result == datetime(2026, 4, 4, 6, 30, 0)


# ---------------------------------------------------------------------------
# current_active_slot
# ---------------------------------------------------------------------------


class TestCurrentActiveSlot:
    def test_naive_datetime_in_slot_warns_and_converts(self, caplog):
        import logging as stdlib_logging

        coord = _make_coordinator()
        # Naive datetime (should warn and treat as UTC)
        naive_dt = datetime(2026, 4, 4, 12, 0, 0)
        with caplog.at_level(stdlib_logging.WARNING):
            slot = _make_timeslot(dt=naive_dt)
        coord.timeslots = [slot]
        coord.tz = ZoneInfo("Europe/London")
        # Slot is UTC 12:00 → Europe/London (BST, UTC+1 in April) = 13:00
        now_london = datetime(2026, 4, 4, 13, 0, 0, tzinfo=ZoneInfo("Europe/London"))
        with patch(
            "custom_components.battery_charge_calculator.coordinators.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = now_london
            result = coord.current_active_slot()
        assert result is slot
        assert any("Naive datetime" in r.message for r in caplog.records)

    def test_dst_transition_slot_matching(self):
        coord = _make_coordinator()
        # Slot at 01:30 UTC, which is 02:30 BST (DST starts in UK on 2026-03-29)
        dt_utc = datetime(2026, 3, 29, 1, 30, 0, tzinfo=timezone.utc)
        slot = _make_timeslot(dt=dt_utc)
        coord.timeslots = [slot]
        coord.tz = ZoneInfo("Europe/London")
        # Simulate now as 02:30 BST — datetime is already tz-aware, no need to set tzinfo
        now = dt_utc.astimezone(ZoneInfo("Europe/London"))
        with patch(
            "custom_components.battery_charge_calculator.coordinators.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = now
            result = coord.current_active_slot()
        assert result is slot

    def test_returns_none_when_no_timeslots(self):
        coord = _make_coordinator()
        coord.timeslots = []
        assert coord.current_active_slot() is None

    def test_returns_none_when_timeslots_not_list(self):
        coord = _make_coordinator()
        coord.timeslots = None
        assert coord.current_active_slot() is None

    def test_returns_active_slot(self):
        coord = _make_coordinator()
        now = datetime.now(tz=timezone.utc)
        # Slot that started 5 minutes ago (still within the 30-min window)
        active_dt = now - timedelta(minutes=5)
        slot = _make_timeslot(dt=active_dt)
        coord.timeslots = [slot]
        coord.tz = timezone.utc
        result = coord.current_active_slot()
        assert result is slot

    def test_returns_none_for_past_slot(self):
        coord = _make_coordinator()
        past_dt = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        slot = _make_timeslot(dt=past_dt)
        coord.timeslots = [slot]
        coord.tz = timezone.utc
        assert coord.current_active_slot() is None

    def test_returns_none_for_future_slot(self):
        coord = _make_coordinator()
        future_dt = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        slot = _make_timeslot(dt=future_dt)
        coord.timeslots = [slot]
        coord.tz = timezone.utc
        assert coord.current_active_slot() is None

    def test_picks_first_of_multiple_active(self):
        """Edge case: only one slot per 30-min window, but test selection logic."""
        coord = _make_coordinator()
        now = datetime.now(tz=timezone.utc)
        slot1 = _make_timeslot(dt=now - timedelta(minutes=5), charge_option="charge")
        slot2 = _make_timeslot(dt=now + timedelta(hours=1), charge_option="export")
        coord.timeslots = [slot1, slot2]
        coord.tz = timezone.utc
        result = coord.current_active_slot()
        assert result is slot1


# ---------------------------------------------------------------------------
# _async_update_data — charge/export/discharge dispatch
# ---------------------------------------------------------------------------


class TestAsyncUpdateData:
    @pytest.mark.asyncio
    async def test_simulate_mode_skips_mqtt_commands(self):
        coord = _make_coordinator(simulate=True)
        now = datetime.now(tz=timezone.utc)
        slot = _make_timeslot(dt=now - timedelta(minutes=5), charge_option="charge")
        coord.timeslots = [slot]
        coord.tz = timezone.utc
        coord.givenergy.enableCharge = AsyncMock()

        await coord._async_update_data()

        coord.givenergy.enableCharge.assert_not_called()

    @pytest.mark.asyncio
    async def test_charge_slot_dispatches_enable_charge(self):
        coord = _make_coordinator(simulate=False)
        now = datetime.now(tz=timezone.utc)
        slot = _make_timeslot(dt=now - timedelta(minutes=5), charge_option="charge")
        coord.timeslots = [slot]
        coord.tz = timezone.utc
        coord.givenergy.enableCharge = AsyncMock()
        coord.givenergy.enableExport = AsyncMock()
        coord.givenergy.disableCharge = AsyncMock()
        coord.givenergy.disableExport = AsyncMock()

        await coord._async_update_data()

        coord.givenergy.enableCharge.assert_called_once_with(coord.hass)
        coord.givenergy.enableExport.assert_not_called()

    @pytest.mark.asyncio
    async def test_export_slot_dispatches_enable_export(self):
        coord = _make_coordinator(simulate=False)
        now = datetime.now(tz=timezone.utc)
        slot = _make_timeslot(dt=now - timedelta(minutes=5), charge_option="export")
        coord.timeslots = [slot]
        coord.tz = timezone.utc
        coord.givenergy.enableCharge = AsyncMock()
        coord.givenergy.enableExport = AsyncMock()
        coord.givenergy.disableCharge = AsyncMock()
        coord.givenergy.disableExport = AsyncMock()

        await coord._async_update_data()

        coord.givenergy.enableExport.assert_called_once_with(coord.hass)
        coord.givenergy.enableCharge.assert_not_called()

    @pytest.mark.asyncio
    async def test_discharge_slot_disables_both(self):
        coord = _make_coordinator(simulate=False)
        now = datetime.now(tz=timezone.utc)
        slot = _make_timeslot(dt=now - timedelta(minutes=5), charge_option="discharge")
        coord.timeslots = [slot]
        coord.tz = timezone.utc
        coord.givenergy.enableCharge = AsyncMock()
        coord.givenergy.enableExport = AsyncMock()
        coord.givenergy.disableCharge = AsyncMock()
        coord.givenergy.disableExport = AsyncMock()

        await coord._async_update_data()

        coord.givenergy.disableCharge.assert_called_once_with(coord.hass)
        coord.givenergy.disableExport.assert_called_once_with(coord.hass)

    @pytest.mark.asyncio
    async def test_no_active_slot_sends_no_commands(self):
        coord = _make_coordinator(simulate=False)
        coord.timeslots = []
        coord.tz = timezone.utc
        coord.givenergy.enableCharge = AsyncMock()
        coord.givenergy.enableExport = AsyncMock()
        coord.givenergy.disableCharge = AsyncMock()
        coord.givenergy.disableExport = AsyncMock()

        await coord._async_update_data()

        coord.givenergy.enableCharge.assert_not_called()
        coord.givenergy.enableExport.assert_not_called()
        coord.givenergy.disableCharge.assert_not_called()
        coord.givenergy.disableExport.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_timeslots(self):
        coord = _make_coordinator(simulate=True)
        slot = _make_timeslot()
        coord.timeslots = [slot]
        coord.tz = timezone.utc
        result = await coord._async_update_data()
        assert result == [slot]


# ---------------------------------------------------------------------------
# find_in_dataset
# ---------------------------------------------------------------------------


class TestFindInDataset:
    def setup_method(self):
        self.coord = _make_coordinator()

    def test_returns_matching_value(self):
        data = [{"key": "a", "val": 1}, {"key": "b", "val": 2}]
        result = self.coord.find_in_dataset(data, 99, "val", lambda x: x["key"] == "b")
        assert result == 2

    def test_returns_lastvalue_when_no_match(self):
        data = [{"key": "a", "val": 1}]
        result = self.coord.find_in_dataset(data, 42, "val", lambda x: x["key"] == "z")
        assert result == 42

    def test_returns_first_match_when_multiple(self):
        data = [{"key": "a", "val": 10}, {"key": "a", "val": 20}]
        result = self.coord.find_in_dataset(data, 0, "val", lambda x: x["key"] == "a")
        assert result == 10

    def test_empty_dataset_returns_lastvalue(self):
        result = self.coord.find_in_dataset([], "default", "val", lambda x: True)
        assert result == "default"


# ---------------------------------------------------------------------------
# _should_replan
# ---------------------------------------------------------------------------


class TestShouldReplan:
    """Tests for BatteryChargeCoordinator._should_replan."""

    def _make_future_slot(
        self, coord, hours_from_now: float, initial_power: float = 5.0
    ):
        """Return a timeslot that starts `hours_from_now` hours in the future."""
        dt = datetime.now(tz=timezone.utc) + timedelta(hours=hours_from_now)
        slot = _make_timeslot(dt=dt)
        slot.initial_power = initial_power
        return slot

    def _make_active_slot(self, coord, initial_power: float = 5.0):
        """Return a timeslot that is currently active (started 5 minutes ago)."""
        dt = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
        slot = _make_timeslot(dt=dt)
        slot.initial_power = initial_power
        return slot

    @pytest.mark.asyncio
    async def test_returns_true_when_no_timeslots(self):
        coord = _make_coordinator()
        coord.timeslots = []
        should, reason = await coord._should_replan()
        assert should is True
        assert reason == const.REPLAN_REASON_NO_PLAN

    @pytest.mark.asyncio
    async def test_returns_true_when_timeslots_is_none(self):
        coord = _make_coordinator()
        coord.timeslots = None
        should, reason = await coord._should_replan()
        assert should is True
        assert reason == const.REPLAN_REASON_NO_PLAN

    @pytest.mark.asyncio
    async def test_returns_true_when_fewer_than_2h_remaining(self):
        """Last slot ends in 1.5 hours — below the 2-hour threshold."""
        coord = _make_coordinator()
        coord.tz = timezone.utc
        # Last slot started 1 hour ago; it's 30 min long, so plan ends in -30 min
        # Use a slot ending in 1.5 hours: start = now + 1h, end = now + 1.5h
        last_slot = self._make_future_slot(coord, hours_from_now=1.0)
        active_slot = self._make_active_slot(coord, initial_power=5.0)
        coord.timeslots = [active_slot, last_slot]
        coord.givenergy.get_inverter_soc_kwh = AsyncMock(return_value=5.0)
        should, reason = await coord._should_replan()
        assert should is True
        assert reason == const.REPLAN_REASON_PLAN_EXPIRING

    @pytest.mark.asyncio
    async def test_returns_false_when_more_than_2h_remaining_and_battery_on_track(self):
        """Last slot ends in 4 hours, battery exactly on projection — no replan."""
        coord = _make_coordinator()
        coord.tz = timezone.utc
        last_slot = self._make_future_slot(coord, hours_from_now=3.5)  # ends in 4h
        active_slot = self._make_active_slot(coord, initial_power=5.0)
        coord.timeslots = [active_slot, last_slot]
        coord.givenergy.get_inverter_soc_kwh = AsyncMock(return_value=5.0)
        should, reason = await coord._should_replan()
        assert should is False

    @pytest.mark.asyncio
    async def test_returns_true_when_battery_deviation_exceeds_10_percent(self):
        """Actual = 3.6 kWh, projected = 5.0 kWh → deviation 16 % > 10 %."""
        coord = _make_coordinator()
        coord.tz = timezone.utc
        coord.battery_capacity_kwh = 9.0
        last_slot = self._make_future_slot(coord, hours_from_now=3.5)
        active_slot = self._make_active_slot(coord, initial_power=5.0)
        coord.timeslots = [active_slot, last_slot]
        # 5.0 - 3.6 = 1.4 kWh → 1.4 / 9.0 ≈ 15.6 %
        coord.givenergy.get_inverter_soc_kwh = AsyncMock(return_value=3.6)
        should, reason = await coord._should_replan()
        assert should is True
        assert reason == const.REPLAN_REASON_BATTERY_DEVIATION

    @pytest.mark.asyncio
    async def test_returns_false_when_battery_deviation_within_10_percent(self):
        """Actual = 5.8 kWh, projected = 5.0 kWh → deviation 8.9 % < 10 %."""
        coord = _make_coordinator()
        coord.tz = timezone.utc
        coord.battery_capacity_kwh = 9.0
        last_slot = self._make_future_slot(coord, hours_from_now=3.5)
        active_slot = self._make_active_slot(coord, initial_power=5.0)
        coord.timeslots = [active_slot, last_slot]
        # 5.8 - 5.0 = 0.8 kWh → 0.8 / 9.0 ≈ 8.9 %
        coord.givenergy.get_inverter_soc_kwh = AsyncMock(return_value=5.8)
        should, reason = await coord._should_replan()
        assert should is False

    @pytest.mark.asyncio
    async def test_returns_false_just_below_10_percent_boundary(self):
        """Deviation of just under 10 % does NOT trigger replan."""
        coord = _make_coordinator()
        coord.tz = timezone.utc
        coord.battery_capacity_kwh = 9.0
        last_slot = self._make_future_slot(coord, hours_from_now=3.5)
        active_slot = self._make_active_slot(coord, initial_power=5.0)
        coord.timeslots = [active_slot, last_slot]
        # 0.89 / 9.0 ≈ 9.9 %, safely below the > 10 % threshold
        coord.givenergy.get_inverter_soc_kwh = AsyncMock(return_value=5.89)
        should, reason = await coord._should_replan()
        assert should is False

    @pytest.mark.asyncio
    async def test_uses_configured_battery_capacity_for_deviation(self):
        """A custom battery capacity changes the deviation threshold.

        With a 12 kWh battery, the same 1.4 kWh discrepancy is only ~11.7 %
        (still above the 10 % threshold), whereas 0.8 kWh would be ~6.7 %.
        """
        coord = _make_coordinator()
        coord.tz = timezone.utc
        coord.battery_capacity_kwh = 12.0
        last_slot = self._make_future_slot(coord, hours_from_now=3.5)
        active_slot = self._make_active_slot(coord, initial_power=5.0)
        coord.timeslots = [active_slot, last_slot]
        # 1.4 kWh deviation on a 12 kWh battery ≈ 11.7 % → still triggers replan
        coord.givenergy.get_inverter_soc_kwh = AsyncMock(return_value=3.6)
        should, reason = await coord._should_replan()
        assert should is True

    @pytest.mark.asyncio
    async def test_larger_battery_capacity_means_more_tolerance(self):
        """A deviation that triggers replan on a 9 kWh battery does not on a 20 kWh battery."""
        # 1.4 kWh on 9 kWh = 15.6 % → triggers
        coord_small = _make_coordinator()
        coord_small.tz = timezone.utc
        coord_small.battery_capacity_kwh = 9.0
        active = self._make_active_slot(coord_small, initial_power=5.0)
        last = self._make_future_slot(coord_small, hours_from_now=3.5)
        coord_small.timeslots = [active, last]
        coord_small.givenergy.get_inverter_soc_kwh = AsyncMock(return_value=3.6)
        should_small, _ = await coord_small._should_replan()
        assert should_small is True

        # 1.4 kWh on 20 kWh = 7.0 % → does not trigger
        coord_large = _make_coordinator()
        coord_large.tz = timezone.utc
        coord_large.battery_capacity_kwh = 20.0
        active2 = self._make_active_slot(coord_large, initial_power=5.0)
        last2 = self._make_future_slot(coord_large, hours_from_now=3.5)
        coord_large.timeslots = [active2, last2]
        coord_large.givenergy.get_inverter_soc_kwh = AsyncMock(return_value=3.6)
        should_large, _ = await coord_large._should_replan()
        assert should_large is False

    @pytest.mark.asyncio
    async def test_returns_false_when_soc_unavailable(self):
        """When SOC is None the check is inconclusive — do not disturb the plan."""
        coord = _make_coordinator()
        coord.tz = timezone.utc
        last_slot = self._make_future_slot(coord, hours_from_now=3.5)
        active_slot = self._make_active_slot(coord, initial_power=5.0)
        coord.timeslots = [active_slot, last_slot]
        coord.givenergy.get_inverter_soc_kwh = AsyncMock(return_value=None)
        should, reason = await coord._should_replan()
        assert should is False

    @pytest.mark.asyncio
    async def test_returns_true_when_no_active_slot_despite_timeslots(self):
        """All slots are in the past — no active slot, so replan."""
        coord = _make_coordinator()
        coord.tz = timezone.utc
        past_slot = _make_timeslot(
            dt=datetime.now(tz=timezone.utc) - timedelta(hours=3)
        )
        past_slot.initial_power = 5.0
        # Last slot ends far in the future so the plan-end check doesn't fire first
        last_slot = self._make_future_slot(coord, hours_from_now=3.5)
        coord.timeslots = [past_slot, last_slot]
        coord.givenergy.get_inverter_soc_kwh = AsyncMock(return_value=5.0)
        should, reason = await coord._should_replan()
        assert should is True
        assert reason == const.REPLAN_REASON_NO_ACTIVE_SLOT


# ---------------------------------------------------------------------------
# _conditional_replan
# ---------------------------------------------------------------------------


class TestConditionalReplan:
    """Tests for BatteryChargeCoordinator._conditional_replan."""

    @pytest.mark.asyncio
    async def test_calls_octopus_listener_when_should_replan(self):
        coord = _make_coordinator()
        coord.timeslots = []  # no plan → _should_replan returns True
        coord.octopus_state_change_listener = AsyncMock()

        await coord._conditional_replan()

        coord.octopus_state_change_listener.assert_called_once_with(
            None, reason=const.REPLAN_REASON_NO_PLAN
        )

    @pytest.mark.asyncio
    async def test_skips_replan_when_plan_is_valid(self):
        coord = _make_coordinator()
        coord.tz = timezone.utc
        now = datetime.now(tz=timezone.utc)
        # Active slot with matching battery, plan ends in 4 hours
        active_slot = _make_timeslot(dt=now - timedelta(minutes=5))
        active_slot.initial_power = 5.0
        last_slot = _make_timeslot(dt=now + timedelta(hours=3, minutes=30))
        last_slot.initial_power = 5.0
        coord.timeslots = [active_slot, last_slot]
        coord.givenergy.get_inverter_soc_kwh = AsyncMock(return_value=5.0)
        coord.octopus_state_change_listener = AsyncMock()

        await coord._conditional_replan()

        coord.octopus_state_change_listener.assert_not_called()
