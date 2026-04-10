"""ML Power Surface sensor.

Exposes a 3-D (week × temperature → daily kWh) surface computed from the
trained ML model plus the physics calculator.  The surface shows how the
model's prediction varies across the full temperature and seasonal range,
even when training data only covers part of the year.

Resolution:
    - X axis (temps): -10 °C to +20 °C in 2 °C steps  → 16 points
    - Y axis (weeks): ISO weeks 1–52                   → 52 points
    - Z (z / z_physics): matrices of shape 52 × 16     → 832 cells each

Only registered when ML_ENABLED = True.

Plotly 3-D surface card (paste into Lovelace)::

    type: custom:plotly-graph
    title: ML power surface — week × temperature
    raw_plotly_config: true
    layout:
      scene:
        xaxis:
          title: Temperature (°C)
        yaxis:
          title: Week of year
        zaxis:
          title: Daily kWh
      showlegend: true
    config:
      displayModeBar: false
    entities:
      - entity: sensor.ml_power_surface
        show_value: false
    data:
      - type: surface
        name: ML blended
        colorscale: Blues
        x: $ex hass.states['sensor.ml_power_surface'].attributes.temps
        y: $ex hass.states['sensor.ml_power_surface'].attributes.weeks
        z: $ex hass.states['sensor.ml_power_surface'].attributes.z
      - type: surface
        name: Physics only
        colorscale: Oranges
        opacity: 0.5
        x: $ex hass.states['sensor.ml_power_surface'].attributes.temps
        y: $ex hass.states['sensor.ml_power_surface'].attributes.weeks
        z: $ex hass.states['sensor.ml_power_surface'].attributes.z_physics
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .. import const


class MLPowerSurfaceSensor(CoordinatorEntity, SensorEntity):
    """Sensor exposing the week × temperature → daily-kWh power surface.

    State: total cell count (temps × weeks) — a stable non-zero integer that
    confirms the surface has been computed.

    Attributes:
        temps       -- list of 16 temperature floats (°C), -10 to 20 in 2 °C steps
        weeks       -- list of 52 ISO week ints (1–52)
        z           -- 52 × 16 matrix of blended (ML + physics) daily kWh
        z_physics   -- 52 × 16 matrix of physics-only daily kWh (comparison layer)
        blend_weight -- current w_ml blend weight (0 = physics, 1 = full ML)
        generated_at -- ISO-8601 UTC timestamp of the last successful training run
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_name = const.ML_POWER_SURFACE_SENSOR_NAME
    _attr_unique_id = const.ML_POWER_SURFACE_SENSOR
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:chart-surface"

    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        """Initialise the ML power surface sensor."""
        super().__init__(coordinator)
        self.hass = hass
        self._update_attributes()

    def _update_attributes(self) -> None:
        """Pull the latest surface data from the ML estimator."""
        estimator = getattr(self.coordinator, "ml_estimator", None)
        model = getattr(estimator, "_model", None) if estimator else None

        if not estimator or not getattr(estimator, "is_ready", False) or model is None:
            self._attr_native_value = None
            self._attr_extra_state_attributes = {
                "state": "unavailable",
                "reason": "ML model not yet trained",
            }
            return

        surface: dict = getattr(model, "power_surface", {})
        if not surface or not surface.get("z"):
            self._attr_native_value = None
            self._attr_extra_state_attributes = {
                "state": "unavailable",
                "reason": (
                    "Surface data is missing — retrain the model via "
                    "Developer Tools → Services → "
                    "battery_charge_calculator.trigger_ml_training"
                ),
            }
            return

        from ..ml.model_trainer import compute_blend_weight  # local import, no HA deps

        n_temps = len(surface.get("temps", []))
        n_weeks = len(surface.get("weeks", []))
        self._attr_native_value = n_temps * n_weeks  # stable non-null state
        self._attr_extra_state_attributes = {
            "temps": surface["temps"],
            "weeks": surface["weeks"],
            "z": surface["z"],
            "z_physics": surface.get("z_physics", []),
            "blend_weight": round(compute_blend_weight(model.n_training_samples), 3),
            "generated_at": model.trained_at.isoformat(),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh surface data whenever the coordinator updates."""
        self._update_attributes()
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Sensor is always available once registered."""
        return True
