"""ML Power Model Status sensor.

Exposes the state and metadata of the ML power estimation model.
Only registered when ML_ENABLED = True in config options (D-16).
"""

from __future__ import annotations
from typing import Any
from datetime import datetime, timezone

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .. import const


class MLModelStatusSensor(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor reporting the ML power estimation model state.

    State values:
        disabled          — ML_ENABLED is False
        insufficient_data — not enough clean training data yet (D-10 gate)
        training          — model is currently being trained
        ready             — model trained and active
        error             — training or data fetch failed

    Key attributes (D-11 + D-15 + D-17 extensions):
        last_trained          — ISO8601 UTC datetime of last successful training
        training_samples      — number of clean slots used
        r2_score              — cross-validated R² (model accuracy, 0–1)
        blend_weight          — current w_ml (0=pure physics, 1=full ML)
        model_age_days        — days since last training
        model_type            — "hist_gbr" or "ridge"
        consumption_source    — "givenergy" | "octopus" | "both"
        consumption_source_fallback — bool: True if fallback source used
        consumption_signal_quality  — "full" | "partial"
        temp_source           — "openmeteo" | "ha_entity" | "imputed"
        temp_source_fallback  — bool
        last_fetch_error      — error message string or null
        ev_detection_mode     — "residual_iqr" | "cold_start_absolute" | "temporal_cv_fallback"
        ev_excluded_slots     — total slots excluded as EV/large-load
        ev_excluded_fraction  — fraction of training data excluded
        ev_blocks_detected    — number of blocks detected this training cycle
        ev_blocks             — list of up to 20 most recent block dicts
        ml_enabled            — bool (mirrors config option)
        error_message         — last error string (None when ready)
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_name = const.ML_MODEL_STATUS_SENSOR_NAME
    _attr_unique_id = const.ML_MODEL_STATUS_SENSOR

    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        """Initialise the ML model status sensor."""
        super().__init__(coordinator)
        self.hass = hass
        self._update_attributes()

    def _update_attributes(self) -> None:
        """Sync sensor state and attributes from coordinator's ml_client."""
        ml_client = getattr(self.coordinator, "ml_client", None)

        if ml_client is None:
            self._attr_native_value = "disabled"
            self._attr_extra_state_attributes = {"ml_enabled": False}
            return

        status = ml_client.get_status()
        self._attr_native_value = ml_client.state or status.get(
            "state", "service_unreachable"
        )
        self._attr_extra_state_attributes = status

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        self._update_attributes()
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Sensor is always available when registered."""
        return True
