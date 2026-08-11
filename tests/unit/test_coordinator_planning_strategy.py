"""Focused tests for coordinator planning via strategy abstraction."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.battery_charge_calculator import const
from custom_components.battery_charge_calculator.coordinators import (
    BatteryChargeCoordinator,
)
from custom_components.battery_charge_calculator.planning.models import PlanningInputs


def _make_coordinator() -> BatteryChargeCoordinator:
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


@pytest.mark.asyncio
async def test_octopus_listener_uses_strategy_collect_inputs_and_axle_constraints():
    coord = _make_coordinator()

    weather_state = MagicMock()
    weather_state.attributes.get = MagicMock(return_value=14.0)
    coord.hass.states.get = MagicMock(return_value=weather_state)
    coord.hass.services.async_call = AsyncMock(
        return_value={"weather.forecast_home": {"forecast": []}}
    )

    now = datetime.now(timezone.utc)
    import_rates = [
        {
            "start": now - timedelta(hours=1),
            "end": now + timedelta(hours=6),
            "value_inc_vat": 0.25,
        }
    ]

    strategy = MagicMock()
    strategy.collect_inputs = AsyncMock(
        return_value=PlanningInputs(
            standing_charge_rate=0.4,
            import_rates=import_rates,
            export_rates=import_rates,
            today_consumption=[],
            battery_kwh=5.0,
            time_end=now + timedelta(hours=6),
            current_temperature=14.0,
            hourly_temperature_forecast=[],
            solar_forecast={"data": []},
        )
    )
    strategy.axle_constraints = MagicMock(return_value=(0.0, None))
    coord.planning_strategy = strategy

    coord.power_calculator.from_temp_and_time = MagicMock(return_value=0.3)
    coord.async_set_updated_data = MagicMock()

    with (
        patch(
            "custom_components.battery_charge_calculator.coordinators.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.battery_charge_calculator.coordinators.genetic_evaluator.GeneticEvaluator"
        ) as evaluator_cls,
    ):
        evaluator_instance = evaluator_cls.return_value
        evaluator_instance.evaluate.return_value = ([], 0.0)

        await coord.octopus_state_change_listener(None)

    strategy.collect_inputs.assert_awaited_once()
    assert strategy.axle_constraints.call_count > 0
    coord.async_set_updated_data.assert_called_once_with(coord.timeslots)
