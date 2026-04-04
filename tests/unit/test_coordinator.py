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
        patch(
            "custom_components.battery_charge_calculator.coordinators.givenergy"
        ),
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
