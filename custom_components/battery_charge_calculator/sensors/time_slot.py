"""Time slot sensor."""

from __future__ import annotations
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import RestoreSensor
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .. import const


class TimeSlotSensor(CoordinatorEntity, SelectEntity, RestoreSensor):
    """Sensor showing the current active charge/export/discharge slot."""

    _attr_options = ["charge", "export", "discharge"]
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.hass = hass
        self._attr_unique_id = const.BATTERY_CHARGE_SENSOR
        self.entity_id = const.BATTERY_CHARGE_SENSOR

    @property
    def current_option(self) -> str | None:
        """Return the current active slot's charge option."""
        slot_func = getattr(self.coordinator, "current_active_slot", None)
        if callable(slot_func):
            slot = slot_func()
            if slot and hasattr(slot, "charge_option"):
                return getattr(slot, "charge_option", None)
        return None
