"""Tariff/consumption planning provider contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ...models import (
    PlanningProviderContext,
    TariffConsumptionRequest,
)


class TariffConsumptionPlanningProvider(ABC):
    """Contract for tariff and consumption data access."""

    @abstractmethod
    def build_consumption_request(
        self,
        *,
        context: PlanningProviderContext,
    ) -> TariffConsumptionRequest:
        """Build provider-specific request shape from context/config."""

    @abstractmethod
    async def fetch_standing_charge(self, session) -> float:
        """Return standing charge in p/day or equivalent unit."""

    @abstractmethod
    async def fetch_import_rates(self, session) -> list[dict]:
        """Return upcoming import half-hour rates."""

    @abstractmethod
    async def fetch_export_rates(self, session) -> list[dict]:
        """Return upcoming export half-hour rates."""

    @abstractmethod
    async def fetch_today_consumption(
        self,
        session,
        *,
        request: TariffConsumptionRequest,
    ) -> list[dict]:
        """Return today's half-hourly consumption samples."""
