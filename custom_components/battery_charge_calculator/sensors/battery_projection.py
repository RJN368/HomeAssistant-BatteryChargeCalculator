"""Battery projection sensor."""

from __future__ import annotations
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .. import const


class BatteryProjectionSensor(CoordinatorEntity, SensorEntity):
    """Representation of future price predictions."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = "total"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        """Initialize the future price sensor."""
        super().__init__(coordinator)
        self.hass = hass
        self._attr_name = const.BATTERY_PROJECTION_SENSOR_NAME
        self._attr_unique_id = const.BATTERY_PROJECTION_SENSOR

    @property
    def native_value(self):
        """Return the current battery power as the sensor value."""
        data = getattr(self.coordinator, "data", None)
        if data and len(data) > 0 and hasattr(data[0], "initial_power"):
            return data[0].initial_power
        return None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update attributes when coordinator data changes."""
        data = getattr(self.coordinator, "data", None)
        if data and len(data) > 0 and hasattr(data[0], "start_datetime"):
            slots = {"data": []}
            slotdata = slots["data"]
            cost_sum = 0
            current_day = 0
            for val in data:
                if not hasattr(val, "start_datetime"):
                    continue
                if current_day < val.start_datetime.day:
                    cost_sum = 0
                cost_sum += getattr(val, "cost", 0)
                current_day = val.start_datetime.day
                slotdata.append(
                    {
                        "date": val.start_datetime.isoformat()
                        if hasattr(val.start_datetime, "isoformat")
                        else str(val.start_datetime),
                        "cost": getattr(val, "cost", 0),
                        "charge_option": getattr(val, "charge_option", None),
                        "cost_total": cost_sum,
                        "initial_power": getattr(val, "initial_power", None),
                    }
                )
            self._attr_extra_state_attributes = slots
        else:
            self._attr_extra_state_attributes = {}
        self.async_write_ha_state()
