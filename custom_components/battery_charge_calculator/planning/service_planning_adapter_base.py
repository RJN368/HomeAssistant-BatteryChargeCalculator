"""Base class for Home Assistant service-backed planning adapters."""

from __future__ import annotations


class ServicePlanningAdapterBase:
    """Shared utilities for adapters that query HA services/states."""

    def __init__(self, *, entity_id: str | None = None) -> None:
        self._entity_id = entity_id

    async def _call_service(self, hass, domain: str, service: str, data: dict) -> dict:
        return await hass.services.async_call(
            domain,
            service,
            data,
            return_response=True,
            blocking=True,
        )

    def _require_state(self, hass):
        if not self._entity_id:
            raise ValueError("entity_id is required for this adapter")
        state = hass.states.get(self._entity_id)
        if state is None:
            raise ValueError(f"Entity {self._entity_id} not found")
        return state
