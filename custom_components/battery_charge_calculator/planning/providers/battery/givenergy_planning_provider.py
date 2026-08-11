"""Battery planning provider contract."""

from __future__ import annotations

from abc import ABC, abstractmethod


class GivEnergyPlanningProvider(ABC):
    """Contract for battery state reads used by planning."""

    @abstractmethod
    async def get_battery_soc_kwh(self, hass) -> float | None:
        """Return battery state of charge in kWh."""
