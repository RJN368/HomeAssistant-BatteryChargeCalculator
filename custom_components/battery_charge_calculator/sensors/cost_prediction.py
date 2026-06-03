"""Cost prediction sensor."""

from __future__ import annotations
from typing import Any

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .. import const


class CostPredictionSensor(CoordinatorEntity, RestoreSensor):
    """Sensor showing predicted energy cost for the rest of today.

    The value is computed once per replan cycle by
    ``BatteryChargeCoordinator._calculate_end_of_day_cost``, which blends
    real half-hourly consumption (from the Octopus API) for completed slots
    with the genetic evaluator's predicted costs for remaining slots.
    """

    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.hass = hass
        self._attr_unique_id = const.CHARGE_COST_ESTIMATE_SENSOR
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_should_poll = False
        self._attr_translation_key = "cost_prediction"

    @property
    def native_value(self):
        """Return the predicted end-of-day cost."""
        return self.coordinator.end_of_day_cost
