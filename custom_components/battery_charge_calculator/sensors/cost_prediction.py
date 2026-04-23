"""Cost prediction sensor."""

from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
import logging
from typing import Any

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .. import const


class CostPredictionSensor(CoordinatorEntity, RestoreSensor):
    """Sensor showing predicted energy cost for the rest of today."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_should_poll = False
    _attr_translation_key = "cost_prediction"

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
        current_day = datetime.now(ZoneInfo("Europe/London")).day
        timeslots = getattr(self.coordinator, "timeslots", None)
        if timeslots:
            for timeslot in timeslots:
                if hasattr(timeslot, "start_datetime"):
                    dt = timeslot.start_datetime
                    if dt.tzinfo is None:
                        logging.warning("Naive datetime in timeslot; assuming UTC.")
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    dt = dt.astimezone(ZoneInfo("Europe/London"))
                    if dt.day == current_day:
                        end_of_day_cost += getattr(timeslot, "cost", 0)
        return end_of_day_cost
