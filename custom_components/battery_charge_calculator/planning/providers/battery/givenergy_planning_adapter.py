"""GivEnergy planning adapter."""

from __future__ import annotations

from ...client_planning_adapter_base import ClientPlanningAdapterBase
from .givenergy_planning_provider import GivEnergyPlanningProvider


class GivEnergyPlanningAdapter(ClientPlanningAdapterBase, GivEnergyPlanningProvider):
    """GivEnergy adapter over inverter MQTT controller."""

    def __init__(self, controller) -> None:
        super().__init__(controller)

    async def get_battery_soc_kwh(self, hass) -> float | None:
        return await self._client.get_inverter_soc_kwh(hass)
