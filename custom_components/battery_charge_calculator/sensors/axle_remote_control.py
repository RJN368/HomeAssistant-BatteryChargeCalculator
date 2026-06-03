"""Axle remote-control diagnostic sensor."""

from __future__ import annotations

from datetime import timezone
from enum import StrEnum
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

try:
    from homeassistant.const import EntityCategory
except ImportError:  # pragma: no cover - compatibility for lightweight test stubs
    class EntityCategory(StrEnum):
        DIAGNOSTIC = "diagnostic"

from .. import const


class AxleRemoteControlSensor(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor exposing Axle source and suppression state."""

    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        """Initialise the Axle remote-control sensor."""
        super().__init__(coordinator)
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_should_poll = False
        self._attr_translation_key = "axle_remote_control"
        self._attr_unique_id = const.AXLE_REMOTE_CONTROL_SENSOR
        self.hass = hass
        self._update_attributes()

    def _update_attributes(self) -> None:
        """Sync state and attributes from coordinator Axle cache."""
        cache = getattr(self.coordinator, "_axle_cache", {})

        source_status = cache.get(
            "source_status",
            const.AXLE_SOURCE_STATUS_UNAVAILABLE,
        )
        is_active = bool(cache.get("is_active", False))

        if source_status == const.AXLE_SOURCE_STATUS_UNAVAILABLE:
            state = "unavailable"
        elif is_active:
            state = "active"
        else:
            state = "inactive"

        active_window = None
        now_utc = None
        overlapping_window_fn = getattr(self.coordinator, "_axle_overlapping_window", None)
        if callable(overlapping_window_fn):
            now_utc = cache.get("last_success_utc")
            if now_utc is None:
                from datetime import datetime

                now_utc = datetime.now(timezone.utc)
            active_window = overlapping_window_fn(now_utc)

        cache_age_seconds = None
        cache_age_fn = getattr(self.coordinator, "_axle_cache_age_seconds", None)
        if callable(cache_age_fn):
            cache_age_seconds = cache_age_fn(now_utc=now_utc)

        self._attr_native_value = state
        self._attr_extra_state_attributes = {
            "source_status": source_status,
            "suppression_reason": cache.get("suppression_reason"),
            "last_transition_reason": cache.get("last_transition_reason"),
            "last_error": cache.get("last_error"),
            "active_window_start": active_window.start.isoformat()
            if active_window is not None
            else None,
            "active_window_end": active_window.end.isoformat()
            if active_window is not None
            else None,
            "cache_age_seconds": cache_age_seconds,
            "fail_safe_mode": self.coordinator.config_entry.options.get(
                const.AXLE_FAIL_SAFE_MODE,
                const.DEFAULT_AXLE_FAIL_SAFE_MODE,
            ),
            "neutralize_on_active_entry": self.coordinator.config_entry.options.get(
                const.AXLE_NEUTRALIZE_ON_ACTIVE_ENTRY,
                const.DEFAULT_AXLE_NEUTRALIZE_ON_ACTIVE_ENTRY,
            ),
            "poll_interval_seconds": self.coordinator.config_entry.options.get(
                const.AXLE_POLL_INTERVAL_SECONDS,
                const.DEFAULT_AXLE_POLL_INTERVAL_SECONDS,
            ),
            "request_timeout_seconds": self.coordinator.config_entry.options.get(
                const.AXLE_REQUEST_TIMEOUT_SECONDS,
                const.DEFAULT_AXLE_REQUEST_TIMEOUT_SECONDS,
            ),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        self._update_attributes()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register coordinator listener and immediately write current state."""
        await super().async_added_to_hass()
        self._update_attributes()
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Sensor is always available when registered."""
        return True
