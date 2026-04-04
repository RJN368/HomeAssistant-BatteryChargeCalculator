"""Battery Charge Calculator sensor entities for Home Assistant.

Defines sensors for battery projection, cost prediction, and current charge slot.
"""

from __future__ import annotations
from datetime import datetime
from typing import Any


from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import const


async def async_setup_entry(
    hass: HomeAssistant, config_entry, async_add_entities
) -> None:
    """Set up the Battery Charge Calculator sensor devices."""
    coordinator = hass.data[const.DOMAIN][config_entry.entry_id]
    async_add_entities(
        [
            TimeSlotSensor(hass, coordinator),
            BatteryProjectionSensor(hass, coordinator),
            CostPredictionSensor(hass, coordinator),
        ]
    )


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


class CostPredictionSensor(CoordinatorEntity, RestoreSensor):
    """Sensor showing predicted energy cost for the rest of today."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.hass = hass
        self._attr_unique_id = const.CHARGE_COST_ESTIMATE_SENSOR
        self.entity_id = const.CHARGE_COST_ESTIMATE_SENSOR

    @property
    def native_value(self):
        """Return the predicted end-of-day cost."""
        end_of_day_cost = 0
        current_day = datetime.now().day
        timeslots = getattr(self.coordinator, "timeslots", None)
        if timeslots:
            for timeslot in timeslots:
                if (
                    hasattr(timeslot, "start_datetime")
                    and getattr(timeslot.start_datetime, "day", None) == current_day
                ):
                    end_of_day_cost += getattr(timeslot, "cost", 0)
        return end_of_day_cost


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
