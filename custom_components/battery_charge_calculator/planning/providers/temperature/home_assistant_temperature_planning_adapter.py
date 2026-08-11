"""Home Assistant weather temperature planning adapter."""

from __future__ import annotations

from ...service_planning_adapter_base import ServicePlanningAdapterBase
from .temperature_planning_provider import TemperaturePlanningProvider


class HomeAssistantTemperaturePlanningAdapter(
    ServicePlanningAdapterBase,
    TemperaturePlanningProvider,
):
    """Temperature adapter over Home Assistant weather entity/service."""

    def __init__(self, *, entity_id: str = "weather.forecast_home") -> None:
        super().__init__(entity_id=entity_id)

    async def fetch_current_temperature(self, hass) -> float | None:
        weather_state = self._require_state(hass)
        return weather_state.attributes.get("temperature")

    async def fetch_hourly_forecast(self, hass) -> list[dict]:
        forecast_response = await self._call_service(
            hass,
            "weather",
            "get_forecasts",
            {"entity_id": self._entity_id, "type": "hourly"},
        )
        return forecast_response.get(self._entity_id, {}).get("forecast", [])
