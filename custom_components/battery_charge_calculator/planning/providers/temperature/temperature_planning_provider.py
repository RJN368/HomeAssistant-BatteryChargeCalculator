"""Temperature planning provider contract."""

from __future__ import annotations

from abc import ABC, abstractmethod


class TemperaturePlanningProvider(ABC):
    """Contract for current and forecasted temperature reads."""

    @abstractmethod
    async def fetch_current_temperature(self, hass) -> float | None:
        """Return current ambient temperature in Celsius when available."""

    @abstractmethod
    async def fetch_hourly_forecast(self, hass) -> list[dict]:
        """Return hourly weather forecast list."""
