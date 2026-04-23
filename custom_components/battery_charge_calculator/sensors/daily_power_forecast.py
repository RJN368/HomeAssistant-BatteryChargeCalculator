"""Daily power forecast sensor.

Exposes the per-slot ML/physics power forecast for the planning horizon as a
JSON-serialisable attribute list, suitable for graphing with Plotly Graph Card.

Each slot carries:

  ``time``        — ISO-8601 UTC datetime of the 30-min slot start
  ``temp_c``      — outdoor temperature used for the estimate (°C), or ``null``
  ``kwh``         — final estimated consumption (ML-corrected when ML is active,
                    otherwise same as ``physics_kwh``)
  ``physics_kwh`` — physics-model-only prediction (heating model + base load)

The sensor *state* is the total estimated kWh for the planning window.

Example Plotly Graph Card config (ML vs physics comparison)::

    type: custom:plotly-graph
    title: Forecast power use today
    entities:
      - entity: sensor.daily_power_forecast
        show_value: false
    raw_plotly_config: true
    layout:
      xaxis:
        title: Time
        type: date
      yaxis:
        title: kWh per 30-min slot
        rangemode: tozero
      yaxis2:
        title: Temperature (°C)
        overlaying: y
        side: right
        showgrid: false
      showlegend: true
    config:
      displayModeBar: false
    data:
      - type: bar
        name: ML estimate (kWh)
        x: $ex entities[0].attributes.slots.map(s => s.time)
        y: $ex entities[0].attributes.slots.map(s => s.kwh)
        marker:
          color: steelblue
          opacity: 0.8
      - type: bar
        name: Physics only (kWh)
        x: $ex entities[0].attributes.slots.map(s => s.time)
        y: $ex entities[0].attributes.slots.map(s => s.physics_kwh)
        marker:
          color: orange
          opacity: 0.5
      - type: scatter
        mode: lines
        name: Temperature (°C)
        x: $ex entities[0].attributes.slots.map(s => s.time)
        y: $ex entities[0].attributes.slots.map(s => s.temp_c)
        yaxis: y2
        line:
          color: tomato
          dash: dot
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .. import const


class DailyPowerForecastSensor(CoordinatorEntity, SensorEntity):
    """Sensor exposing forecast power use across today's planning horizon.

    State: total estimated house consumption in kWh for the planning window.
    Attribute ``slots``: list of per-30-min-slot dicts with ``time``,
    ``temp_c``, and ``kwh``.
    """

    _attr_should_poll = False
    _attr_translation_key = "daily_power_forecast"
    _attr_unique_id = const.DAILY_POWER_FORECAST_SENSOR
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-bell-curve-cumulative"

    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        super().__init__(coordinator)
        self.hass = hass
        self._update_attributes()

    def _update_attributes(self) -> None:
        slots: list[dict] = getattr(self.coordinator, "daily_power_forecast", [])
        total = round(sum(s["kwh"] for s in slots), 3)
        total_physics = round(sum(s.get("physics_kwh", s["kwh"]) for s in slots), 3)
        self._attr_native_value = total if slots else None
        self._attr_extra_state_attributes = {
            "slots": slots,
            "slot_count": len(slots),
            "total_kwh": total,
            "total_physics_kwh": total_physics,
            "ml_adjustment_kwh": round(total - total_physics, 3) if slots else None,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_attributes()
        self.async_write_ha_state()
