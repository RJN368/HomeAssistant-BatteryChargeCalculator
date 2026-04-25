"""Last recalculation sensor.

Reports when the charge slots were last recalculated and why.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .. import const


class LastRecalculationSensor(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor reporting when and why charge slots were last recalculated.

    State: ISO8601 timestamp of the last recalculation (or None if not yet run).
    Attributes:
        reason        — machine-readable reason key (e.g. "battery_deviation")
        reason_label  — translation-aware reason value for frontend display
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_translation_key = "last_recalculation"
    _attr_unique_id = const.LAST_RECALCULATION_SENSOR

    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.hass = hass
        self._update_attributes()

    def _update_attributes(self) -> None:
        """Sync state and attributes from the coordinator."""
        self._attr_native_value = getattr(self.coordinator, "recalculation_time", None)
        reason = getattr(self.coordinator, "recalculation_reason", None)
        self._attr_extra_state_attributes = {
            "reason": reason,
            "reason_label": reason,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_attributes()
        self.async_write_ha_state()
