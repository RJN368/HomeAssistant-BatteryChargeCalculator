"""Octopus tariff/consumption planning adapter."""

from __future__ import annotations

from custom_components.battery_charge_calculator import const
from ...client_planning_adapter_base import ClientPlanningAdapterBase
from ...models import PlanningProviderContext, TariffConsumptionRequest
from .tariff_consumption_planning_provider import TariffConsumptionPlanningProvider


class OctopusTariffConsumptionPlanningAdapter(
    ClientPlanningAdapterBase,
    TariffConsumptionPlanningProvider,
):
    """Octopus tariff/consumption adapter over OctopusAgileRatesClient."""

    def __init__(self, client) -> None:
        super().__init__(client)

    def build_consumption_request(
        self,
        *,
        context: PlanningProviderContext,
    ) -> TariffConsumptionRequest:
        options = context.config_entry.options
        return TariffConsumptionRequest(
            identifiers={
                "mpan": options.get(const.OCTOPUS_MPN, ""),
                "meter_serial": options.get(const.OCTOPUS_METER_SERIAL, ""),
            }
        )

    async def fetch_standing_charge(self, session) -> float:
        return await self._client.fetch_standing_charge(session)

    async def fetch_import_rates(self, session) -> list[dict]:
        return await self._client.fetch_rates(session, export=False)

    async def fetch_export_rates(self, session) -> list[dict]:
        return await self._client.fetch_rates(session, export=True)

    async def fetch_today_consumption(
        self,
        session,
        *,
        request: TariffConsumptionRequest,
    ) -> list[dict]:
        return await self._client.async_fetch_today_consumption(
            session,
            request.identifiers.get("mpan", ""),
            request.identifiers.get("meter_serial", ""),
        )
