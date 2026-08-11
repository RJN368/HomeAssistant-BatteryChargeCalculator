"""Solar forecast planning adapter."""

from __future__ import annotations

from datetime import datetime

from ...service_planning_adapter_base import ServicePlanningAdapterBase
from .solar_planning_provider import SolarPlanningProvider


class SolarPlanningAdapter(ServicePlanningAdapterBase, SolarPlanningProvider):
    """Solar adapter over Home Assistant Solcast service call."""

    def __init__(self) -> None:
        super().__init__(entity_id=None)

    async def fetch_forecast(
        self,
        hass,
        *,
        start_date_time: datetime,
        end_date_time: datetime,
    ) -> dict:
        return await self._call_service(
            hass,
            "solcast_solar",
            "query_forecast_data",
            {
                "start_date_time": start_date_time,
                "end_date_time": end_date_time,
            },
        )
