"""Estimated power demand sensor (power-vs-temperature curve)."""

from __future__ import annotations
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .. import const


class EstimatedPowerDemandSensor(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor exposing the predicted power-vs-temperature curve.

    State is the heating type in use.  The ``power_curve`` attribute contains
    a list of {temp, kwh_heating, kwh_total} dicts from -20 °C to +20 °C in
    1 °C steps, suitable for a Plotly Graph Card::

        type: custom:plotly-graph
        title: Power curve vs temperature
        entities:
          - entity: sensor.est_power_demand
            show_value: false
        raw_plotly_config: true
        layout:
          xaxis:
            title: Temperature (°C)
            range: [-20, 20]
            zeroline: true
          yaxis:
            title: kWh per 30-min slot
            rangemode: tozero
          showlegend: true
        config:
          displayModeBar: false
        data:
          - type: scatter
            mode: lines
            name: Heating only
            x: $ex entities[0].attributes.power_curve.map(p => p.temp)
            y: $ex entities[0].attributes.power_curve.map(p => p.kwh_heating)
            line:
              color: orange
          - type: scatter
            mode: lines
            name: Total (incl. base load)
            x: $ex entities[0].attributes.power_curve.map(p => p.temp)
            y: $ex entities[0].attributes.power_curve.map(p => p.kwh_total)
            line:
              color: steelblue
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_name = const.EST_POWER_DEMAND_SENSOR_NAME
    _attr_unique_id = const.EST_POWER_DEMAND_SENSOR

    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        """Initialize the estimated power demand sensor."""
        super().__init__(coordinator)
        self.hass = hass
        self._update_attributes()

    def _update_attributes(self) -> None:
        pc = getattr(self.coordinator, "power_calculator", None)
        if pc is not None:
            self._attr_native_value = pc.heating_type
            self._attr_extra_state_attributes = {
                "power_curve": pc.power_curve(temp_min=-20.0, temp_max=20.0, step=1.0),
                "heating_type": pc.heating_type,
                "cop": pc.cop,
                "heat_loss": pc.heat_loss,
                "indoor_temp": pc.indoor_temp,
            }
        else:
            self._attr_native_value = "unavailable"
            self._attr_extra_state_attributes = {}

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh curve whenever the coordinator updates."""
        self._update_attributes()
        self.async_write_ha_state()
