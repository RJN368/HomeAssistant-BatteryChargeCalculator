"""Integration test for the full config flow using Home Assistant's test utilities."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.battery_charge_calculator import const


@pytest.mark.asyncio
async def test_full_config_flow(hass: HomeAssistant):
    # Register the integration (simulate manifest)
    assert await async_setup_component(hass, "persistent_notification", {})
    assert await async_setup_component(hass, "battery_charge_calculator", {})

    # Start the config flow
    result = await hass.config_entries.flow.async_init(
        const.DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"

    # Step 1: main settings
    user_input = {
        const.GIVENERGY_SERIAL_NUMBER: "SN123",
        const.GIVENERGY_API_TOKEN: "TOKEN",
        const.OCTOPUS_ACCOUNT_NUMBER: "A-ACCT",
        const.OCTOPUS_APIKEY: "OCTKEY",
        const.SIMULATE_ONLY: False,
        const.INVERTER_SIZE_KW: 5.0,
        const.INVERTER_EFFICIENCY: 0.9,
    }
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] == "form"
    assert result["step_id"] == "heating"

    # Step 2: heating type
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {const.HEATING_TYPE: const.HEATING_TYPE_NONE}
    )
    # Should complete the flow and create the entry
    assert result["type"] == "create_entry"
    assert result["title"]
    assert result["data"]
