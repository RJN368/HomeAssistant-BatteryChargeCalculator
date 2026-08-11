"""Solar planning provider contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class SolarPlanningProvider(ABC):
    """Contract for solar production forecast reads."""

    @abstractmethod
    async def fetch_forecast(
        self,
        hass,
        *,
        start_date_time: datetime,
        end_date_time: datetime,
    ) -> dict:
        """Return Solcast-style forecast payload."""
