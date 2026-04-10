"""Battery SOC sensor."""

from __future__ import annotations
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity


class BatterySocSensor(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor showing the live cached battery SOC in kWh.

    Updates every time the DataUpdateCoordinator refreshes (every minute)
    so the SOC value from the MQTT subscription is visible in the UI
    without waiting for a full planning cycle.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_name = "Battery SOC"
    _attr_unique_id = "battery_charge_calculator_soc_kwh"

    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        """Initialize the SOC sensor."""
        super().__init__(coordinator)
        self.hass = hass

    @property
    def native_value(self) -> float:
        """Return the cached SOC from the GivEnergy MQTT controller."""
        return self.coordinator.givenergy._soc_kwh
