"""DST-aware tests for BatteryChargeCoordinator: verifies charge slot scheduling across DST boundaries (Europe/London)."""

import pytest
from datetime import datetime, timedelta
import sys

if sys.version_info >= (3, 9):
    from zoneinfo import ZoneInfo
else:
    import pytz

    ZoneInfo = pytz.timezone

from custom_components.battery_charge_calculator import const
from custom_components.battery_charge_calculator.coordinators import (
    BatteryChargeCoordinator,
)
from custom_components.battery_charge_calculator.genetic_evaluator import Timeslot
from unittest.mock import MagicMock, patch

LONDON = ZoneInfo("Europe/London")


# Helper to create a coordinator with Europe/London timezone
@pytest.fixture
def coordinator_london():
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.options = {
        const.OCTOPUS_APIKEY: "test-key",
        const.OCTOPUS_ACCOUNT_NUMBER: "A-1111",
        const.GIVENERGY_SERIAL_NUMBER: "SN001",
        const.GIVENERGY_API_TOKEN: "token",
        const.SIMULATE_ONLY: True,
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
            return_value=LONDON,
        ),
    ):
        coord = BatteryChargeCoordinator(hass, entry)
    coord.hass = hass
    coord.config_entry = entry
    return coord


def _dt_london(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=LONDON)


def test_dst_spring_forward_slot_alignment(coordinator_london):
    """
    When clocks move forward (last Sunday in March), ensure charge slot aligns with local cheap rate start.
    2026-03-29 01:30 UTC = 02:30 BST (DST starts at 01:00 UTC).
    """
    # Slot at 01:30 UTC = 02:30 BST (after DST transition at 01:00 UTC)
    slot_start_utc = datetime(2026, 3, 29, 1, 30, tzinfo=ZoneInfo("UTC"))
    slot = Timeslot(slot_start_utc, 0.10, 0.05, 0.3, 0.0)
    coordinator_london.timeslots = [slot]
    coordinator_london.tz = LONDON
    # The slot's local time should be 02:30 BST (UTC+1)
    assert slot.start_datetime.astimezone(LONDON).hour == 2
    assert slot.start_datetime.astimezone(LONDON).minute == 30
    # The slot should be considered active at 02:30 BST
    test_time = _dt_london(2026, 3, 29, 2, 30)
    with patch(
        "custom_components.battery_charge_calculator.coordinators.datetime"
    ) as dt_patch:
        dt_patch.now.return_value = test_time
        dt_patch.side_effect = lambda *a, **kw: datetime(*a, **kw)
        active = coordinator_london.current_active_slot()
        assert active is slot, (
            "Slot should be active at 02:30 BST after DST spring forward"
        )


def test_dst_fall_back_slot_alignment(coordinator_london):
    """
    When clocks move back (last Sunday in October), ensure charge slot aligns with local cheap rate start.
    2026-10-25: clocks go back at 02:00 BST (01:00 UTC). Slot at 00:30 UTC = 01:30 BST.
    """
    # Slot at 00:30 UTC = 01:30 BST (before the fallback at 01:00 UTC)
    slot_start_utc = datetime(2026, 10, 25, 0, 30, tzinfo=ZoneInfo("UTC"))
    slot = Timeslot(slot_start_utc, 0.10, 0.05, 0.3, 0.0)
    coordinator_london.timeslots = [slot]
    coordinator_london.tz = LONDON
    # The slot's local time should be 01:30 BST (UTC+1, before fallback)
    assert slot.start_datetime.astimezone(LONDON).hour == 1
    # The slot should be considered active at 01:30 BST
    test_time = _dt_london(2026, 10, 25, 1, 30)
    with patch(
        "custom_components.battery_charge_calculator.coordinators.datetime"
    ) as dt_patch:
        dt_patch.now.return_value = test_time
        dt_patch.side_effect = lambda *a, **kw: datetime(*a, **kw)
        active = coordinator_london.current_active_slot()
        assert active is slot, "Slot should be active at 01:30 BST before DST fall back"


def test_dst_slot_misalignment_fails(coordinator_london):
    """
    If slot is misaligned by 1 hour due to DST, test must fail.
    """
    # Simulate a slot that is 1 hour off (should NOT be active at local cheap rate)
    slot_start_utc = datetime(
        2026, 3, 29, 1, 30, tzinfo=ZoneInfo("UTC")
    )  # 02:30 BST, not 01:30
    slot = Timeslot(slot_start_utc, 0.10, 0.05, 0.3, 0.0)
    coordinator_london.timeslots = [slot]
    coordinator_london.tz = LONDON
    # At 01:30 BST, this slot should NOT be active
    test_time = _dt_london(2026, 3, 29, 1, 30)
    with patch(
        "custom_components.battery_charge_calculator.coordinators.datetime"
    ) as dt_patch:
        dt_patch.now.return_value = test_time
        dt_patch.side_effect = lambda *a, **kw: datetime(*a, **kw)
        active = coordinator_london.current_active_slot()
        assert active is None, "Slot should NOT be active if misaligned by 1 hour"
