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

# Human-readable labels for each reason value
_REASON_LABELS: dict[str, str] = {
    const.REPLAN_REASON_INITIAL_SETUP: "Initial setup",
    const.REPLAN_REASON_NO_PLAN: "No existing plan",
    const.REPLAN_REASON_PLAN_EXPIRING: "Plan expiring soon",
    const.REPLAN_REASON_BATTERY_DEVIATION: "Battery level deviation",
    const.REPLAN_REASON_NO_ACTIVE_SLOT: "No active slot found",
    const.REPLAN_REASON_MANUAL: "Manual trigger",
}


class LastRecalculationSensor(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor reporting when and why charge slots were last recalculated.

    State: ISO8601 timestamp of the last recalculation (or None if not yet run).
    Attributes:
        reason        — machine-readable reason key (e.g. "battery_deviation")
        reason_label  — human-readable description of the reason
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_name = const.LAST_RECALCULATION_SENSOR_NAME
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
            "reason_label": _REASON_LABELS.get(reason, reason) if reason else None,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_attributes()
        self.async_write_ha_state()
