"""Unit tests for planning strategy factory wiring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.battery_charge_calculator import const
from custom_components.battery_charge_calculator.planning.factory import (
    build_default_planning_strategy,
)
from custom_components.battery_charge_calculator.planning.models import (
    PlanningProviderContext,
)


def test_factory_builds_default_strategy_with_axle_adapter_delegate():
    coordinator = MagicMock()
    coordinator.agile_rates_client = MagicMock()
    coordinator.givenergy = MagicMock()
    coordinator.axle_slot_export_adjustment_kwh = MagicMock(return_value=1.25)
    coordinator.axle_slot_forced_action = MagicMock(return_value="export")

    strategy = build_default_planning_strategy(coordinator)

    now = datetime.now(timezone.utc)
    adjustment, forced = strategy.axle_constraints(
        slot_start=now,
        slot_end=now + timedelta(minutes=30),
        inverter_size_kw=3.6,
    )

    assert strategy.__class__.__name__ == "DefaultPlanningStrategy"
    assert adjustment == 1.25
    assert forced == "export"
    coordinator.axle_slot_export_adjustment_kwh.assert_called_once()
    coordinator.axle_slot_forced_action.assert_called_once()


def test_factory_falls_back_to_defaults_for_unknown_provider_ids():
    coordinator = MagicMock()
    coordinator.agile_rates_client = MagicMock()
    coordinator.givenergy = MagicMock()
    coordinator.axle_slot_export_adjustment_kwh = MagicMock(return_value=0.0)
    coordinator.axle_slot_forced_action = MagicMock(return_value=None)
    coordinator.config_entry.options = {
        const.PLANNING_TARIFF_PROVIDER: "eon",
        const.PLANNING_BATTERY_PROVIDER: "other_battery",
        const.PLANNING_SOLAR_PROVIDER: "other_solar",
        const.PLANNING_AXLE_PROVIDER: "other_axle",
        const.PLANNING_TEMPERATURE_PROVIDER: "other_temperature",
    }

    strategy = build_default_planning_strategy(coordinator)

    now = datetime.now(timezone.utc)
    adjustment, forced = strategy.axle_constraints(
        slot_start=now,
        slot_end=now + timedelta(minutes=30),
        inverter_size_kw=3.6,
    )

    assert strategy.__class__.__name__ == "DefaultPlanningStrategy"
    assert adjustment == 0.0
    assert forced is None


@pytest.mark.asyncio
async def test_factory_strategy_collect_inputs_uses_provider_context_request_mapping():
    now = datetime.now(timezone.utc)
    import_rates = [{"start": now, "end": now + timedelta(hours=6), "value_inc_vat": 0.21}]
    export_rates = [{"start": now, "end": now + timedelta(hours=6), "value_inc_vat": 0.12}]
    consumption = [{"consumption": 0.5}]

    client = MagicMock()
    client.fetch_standing_charge = AsyncMock(return_value=0.45)
    client.fetch_rates = AsyncMock(side_effect=[import_rates, export_rates])
    client.async_fetch_today_consumption = AsyncMock(return_value=consumption)

    givenergy = MagicMock()
    givenergy.get_inverter_soc_kwh = AsyncMock(return_value=4.2)

    hass = MagicMock()
    hass.states.get = MagicMock(
        return_value=MagicMock(attributes={"temperature": 13.0})
    )
    hass.services.async_call = AsyncMock(
        side_effect=[
            {"weather.forecast_home": {"forecast": [{"datetime": now.isoformat()}]}},
            {"data": [{"period_start": now, "pv_estimate10": 0.0}]},
        ]
    )

    entry = MagicMock()
    entry.options = {
        const.OCTOPUS_MPN: "mpan-123",
        const.OCTOPUS_METER_SERIAL: "meter-789",
    }

    coordinator = MagicMock()
    coordinator.agile_rates_client = client
    coordinator.givenergy = givenergy
    coordinator.axle_slot_export_adjustment_kwh = MagicMock(return_value=0.0)
    coordinator.axle_slot_forced_action = MagicMock(return_value=None)

    strategy = build_default_planning_strategy(coordinator)
    inputs = await strategy.collect_inputs(
        context=PlanningProviderContext(
            hass=hass,
            config_entry=entry,
            session=MagicMock(),
            time_now=now,
        )
    )

    assert inputs.standing_charge_rate == 0.45
    assert inputs.import_rates == import_rates
    assert inputs.export_rates == export_rates
    assert inputs.today_consumption == consumption
    assert inputs.battery_kwh == 4.2
    assert inputs.current_temperature == 13.0
    assert inputs.hourly_temperature_forecast == [{"datetime": now.isoformat()}]
    assert inputs.solar_forecast == {"data": [{"period_start": now, "pv_estimate10": 0.0}]}

    client.async_fetch_today_consumption.assert_awaited_once()
    _, mpan, meter_serial = client.async_fetch_today_consumption.await_args.args
    assert mpan == "mpan-123"
    assert meter_serial == "meter-789"
