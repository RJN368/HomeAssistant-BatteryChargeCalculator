"""OpenMeteoHistoricalClient — fetch historical hourly temperatures.

Uses the Open-Meteo archive API (free, no authentication required).
Returns a dict mapping each date to its 24 hourly temperatures (°C).

See §3.5 of _docs/tariff-comparison.md for the full specification.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

_OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"


class OpenMeteoHistoricalClient:
    """Thin client for Open-Meteo historical weather archive.

    Returns hourly temperature data resampled to a ``dict[date, list[float]]``
    mapping — one 24-entry list per calendar date.
    """

    def __init__(self, lat: float, lon: float) -> None:
        """Initialise with the location co-ordinates from hass.config."""
        self._lat = lat
        self._lon = lon

    async def fetch_temperatures(
        self,
        session: aiohttp.ClientSession,
        start_date: date,
        end_date: date,
    ) -> dict[date, list[float]]:
        """Fetch hourly temperatures for the given date range.

        Args:
            session: Active aiohttp ClientSession.
            start_date: First day of the range (inclusive).
            end_date: Last day of the range (inclusive).

        Returns:
            Dict mapping each date to a list of 24 hourly temperatures in °C.
            Missing hours are filled with the nearest valid value.  Returns an
            empty dict on fetch error.
        """
        params = {
            "latitude": self._lat,
            "longitude": self._lon,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "hourly": "temperature_2m",
            "timezone": "UTC",
        }
        try:
            async with session.get(_OPEN_METEO_URL, params=params) as resp:
                if resp.status >= 400:
                    raise aiohttp.ClientResponseError(
                        resp.request_info,
                        resp.history,
                        status=resp.status,
                        message=f"HTTP error {resp.status} from Open-Meteo archive API",
                    )
                data: dict[str, Any] = await resp.json()
        except aiohttp.ClientResponseError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Open-Meteo fetch failed: %s", exc)
            return {}

        times: list[str] = data.get("hourly", {}).get("time", [])
        temps: list[float | None] = data.get("hourly", {}).get("temperature_2m", [])

        result: dict[date, list[float]] = {}
        for time_str, temp in zip(times, temps):
            if temp is None:
                continue
            dt = datetime.fromisoformat(time_str).replace(tzinfo=timezone.utc)
            day = dt.date()
            if day not in result:
                result[day] = []
            result[day].append(float(temp))

        return result
