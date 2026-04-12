"""Regression tests for the 'Debouncer lock is not re-entrant' error.

Root cause
----------
1. DataUpdateCoordinator.async_refresh() acquires the debouncer lock.
2. Inside the refresh cycle it calls _async_update_data().
3. _async_update_data() called octopus_state_change_listener().
4. octopus_state_change_listener() called self.async_refresh() again.
5. The second async_refresh() attempt saw the lock was already held and
   raised RuntimeError("Debouncer lock is not re-entrant").

This happened on every coordinator update tick (every minute), producing 390+
warning log entries in Home Assistant.

Fix
---
- _async_update_data no longer calls octopus_state_change_listener.
  Planning is driven exclusively by _async_setup (initial) and
  _conditional_replan (hourly conditional check).
- octopus_state_change_listener calls self.async_set_updated_data() to
  notify HA sensors after a successful planning cycle, replacing the
  async_refresh() call that caused re-entrancy.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.battery_charge_calculator import const
from custom_components.battery_charge_calculator.coordinators import (
    BatteryChargeCoordinator,
)

_GENETIC_EVALUATOR_PATH = (
    "custom_components.battery_charge_calculator.genetic_evaluator.GeneticEvaluator"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator():
    """Build a coordinator with all __init__-time external dependencies mocked."""
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
            return_value=timezone.utc,
        ),
    ):
        coordinator = BatteryChargeCoordinator(hass, entry)

    coordinator.hass = hass
    coordinator.config_entry = entry
    return coordinator


def _add_listener_mocks(coord: BatteryChargeCoordinator) -> None:
    """Attach AsyncMock stubs so octopus_state_change_listener() can run to completion."""
    now = datetime.now(tz=timezone.utc)
    slot_end = now + timedelta(hours=2)

    rate_entry = {"start": now, "end": slot_end, "value_inc_vat": 0.25}
    coord.agile_rates_client.fetch_standing_charge = AsyncMock(return_value=0.35)
    coord.agile_rates_client.fetch_rates = AsyncMock(return_value=[rate_entry])

    weather_state = MagicMock()
    weather_state.attributes.get = MagicMock(return_value=15.0)
    coord.hass.states.get = MagicMock(return_value=weather_state)

    # Two sequential service calls: weather forecast then solcast
    coord.hass.services.async_call = AsyncMock(
        side_effect=[
            {"weather.forecast_home": {"forecast": []}},
            {"data": []},
        ]
    )

    coord.givenergy.get_inverter_soc_kwh = AsyncMock(return_value=5.0)
    coord.power_calculator.from_temp_and_time = MagicMock(return_value=0.3)
    coord.ml_client = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDebounceReentrancyFix:
    """Regression tests for the 'Debouncer lock is not re-entrant' error."""

    @pytest.mark.asyncio
    async def test_async_update_data_does_not_call_octopus_listener(self):
        """_async_update_data must NOT trigger octopus_state_change_listener.

        Before the fix _async_update_data called octopus_state_change_listener
        which eventually called async_refresh(), causing a re-entrancy error on
        every coordinator update tick (every minute).
        """
        coord = _make_coordinator()
        coord.timeslots = []
        coord.octopus_state_change_listener = AsyncMock()

        await coord._async_update_data()

        coord.octopus_state_change_listener.assert_not_called()

    @pytest.mark.asyncio
    async def test_octopus_listener_does_not_call_async_refresh(self):
        """octopus_state_change_listener must NOT call async_refresh().

        Before the fix the listener called async_refresh() at the end of a
        successful planning cycle.  When the coordinator's own refresh loop was
        already holding the debouncer lock this raised:
            RuntimeError: Debouncer lock is not re-entrant
        """
        coord = _make_coordinator()
        _add_listener_mocks(coord)

        refresh_calls: list = []

        async def _track_refresh():
            refresh_calls.append(True)

        coord.async_refresh = _track_refresh

        with patch(_GENETIC_EVALUATOR_PATH) as MockEvaluator:
            MockEvaluator.return_value.evaluate.return_value = ([], 0.0)
            await coord.octopus_state_change_listener(None)

        assert not refresh_calls, (
            "octopus_state_change_listener must not call async_refresh – "
            "use async_set_updated_data() to avoid debounce re-entrancy."
        )

    @pytest.mark.asyncio
    async def test_octopus_listener_notifies_via_async_set_updated_data(self):
        """After planning, octopus_state_change_listener must call async_set_updated_data.

        This is the replacement for the removed async_refresh() call and ensures
        HA sensors receive the updated timeslots after every planning run.
        """
        coord = _make_coordinator()
        _add_listener_mocks(coord)
        coord.async_set_updated_data = MagicMock()

        with patch(_GENETIC_EVALUATOR_PATH) as MockEvaluator:
            MockEvaluator.return_value.evaluate.return_value = ([], 0.0)
            await coord.octopus_state_change_listener(None)

        coord.async_set_updated_data.assert_called_once_with(coord.timeslots)
