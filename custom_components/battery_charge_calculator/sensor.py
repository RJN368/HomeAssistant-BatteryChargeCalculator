"""Battery Charge Calculator sensor entry point.

All sensor classes live in the sensors/ sub-package; this module wires them
into Home Assistant via async_setup_entry.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from . import const
from .sensors import (
    AnnualForecastSensor,
    AxleRemoteControlSensor,
    BatteryProjectionSensor,
    BatterySocSensor,
    CostPredictionSensor,
    DailyPowerForecastSensor,
    EstimatedPowerDemandSensor,
    LastRecalculationSensor,
    MLModelStatusSensor,
    MLPowerSurfaceSensor,
    TariffComparisonSensor,
    TimeSlotSensor,
)


_LOGGER = logging.getLogger(__name__)


def _apply_shared_device_info(entities: list, config_entry) -> None:
    """Assign a stable shared device identifier to integration entities."""
    shared_device_info = {
        "identifiers": {(const.DOMAIN, config_entry.entry_id)},
        "name": const.TITLE,
        "manufacturer": "@rjn368",
        "model": const.DOMAIN,
    }
    for entity in entities:
        unique_id = getattr(entity, "unique_id", None) or getattr(
            entity, "_attr_unique_id", None
        )
        if unique_id is None:
            continue
        # Translation-key entities should use entity-level names so they do not
        # all collapse to the shared device name in the UI.
        if (
            getattr(entity, "_attr_translation_key", None)
            and getattr(entity, "_attr_has_entity_name", None) is None
        ):
            entity._attr_has_entity_name = True
        if getattr(entity, "device_info", None) is not None:
            continue
        entity._attr_device_info = shared_device_info


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

    if config_entry.options.get(const.AXLE_ENABLED, const.DEFAULT_AXLE_ENABLED):
        entities.append(AxleRemoteControlSensor(hass, coordinator))

    if config_entry.options.get(const.TARIFF_COMPARISON_ENABLED, False):
        tc_coordinator = hass.data[const.DOMAIN].get(config_entry.entry_id + "_tariff")
        if tc_coordinator:
            entities.append(TariffComparisonSensor(hass, tc_coordinator))

    _apply_shared_device_info(entities, config_entry)
    _LOGGER.debug(
        "Registered %s battery_charge_calculator sensor entities for device %s",
        len(entities),
        config_entry.entry_id,
    )
    async_add_entities(entities)
