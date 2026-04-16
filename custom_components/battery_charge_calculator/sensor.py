"""Battery Charge Calculator sensor entry point.

All sensor classes live in the sensors/ sub-package; this module wires them
into Home Assistant via async_setup_entry.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from . import const
from .sensors import (
    AnnualForecastSensor,
    BatteryProjectionSensor,
    BatterySocSensor,
    CostPredictionSensor,
    DailyPowerForecastSensor,
    EstimatedPowerDemandSensor,
    LastRecalculationSensor,
    MLModelStatusSensor,
    MLPowerSurfaceSensor,
    TimeSlotSensor,
)


async def async_setup_entry(
    hass: HomeAssistant, config_entry, async_add_entities
) -> None:
    """Set up the Battery Charge Calculator sensor devices."""
    coordinator = hass.data[const.DOMAIN][config_entry.entry_id]
    entities = [
        TimeSlotSensor(hass, coordinator),
        BatteryProjectionSensor(hass, coordinator),
        CostPredictionSensor(hass, coordinator),
        BatterySocSensor(hass, coordinator),
        EstimatedPowerDemandSensor(hass, coordinator),
        DailyPowerForecastSensor(hass, coordinator),
        LastRecalculationSensor(hass, coordinator),
    ]
    if config_entry.options.get(const.ML_ENABLED, False):
        entities.append(MLModelStatusSensor(hass, coordinator))
        entities.append(AnnualForecastSensor(hass, coordinator))
        entities.append(MLPowerSurfaceSensor(hass, coordinator))
    async_add_entities(entities)
