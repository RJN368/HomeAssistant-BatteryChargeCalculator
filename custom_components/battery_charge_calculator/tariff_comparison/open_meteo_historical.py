"""OpenMeteoHistoricalClient — fetch historical hourly temperatures.

Uses the Open-Meteo archive API (free, no authentication required).
Returns a dict mapping each date to its 24 hourly temperatures (°C).

See §3.5 of _docs/tariff-comparison.md for the full specification.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
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

    def resample_to_30min(
        self, daily_temps: dict[date, list[float]]
    ) -> dict[datetime, float]:
        """Duplicate each hourly temperature into two 30-min slots.

        Returns a ``dict[slot_start_utc, temperature_c]`` for all slots.
        E.g. temperature at HH:00 → slots at HH:00 and HH:30.
        """
        result: dict[datetime, float] = {}
        for day, hourly in daily_temps.items():
            for hour_idx, temp in enumerate(hourly):
                slot_00 = datetime(
                    day.year, day.month, day.day, hour_idx, 0, tzinfo=timezone.utc
                )
                slot_30 = datetime(
                    day.year, day.month, day.day, hour_idx, 30, tzinfo=timezone.utc
                )
                result[slot_00] = temp
                result[slot_30] = temp
        return result

    async def fetch(
        self,
        session: aiohttp.ClientSession,
        period_from: date,
        period_to: date,
        lat: float | None = None,
        lon: float | None = None,
    ) -> dict[date, list[float]]:
        """Fetch historical temperatures — canonical API (alias of fetch_temperatures).

        Args:
            session: Active aiohttp ClientSession.
            period_from: First day of the range (inclusive).
            period_to: Last day of the range (inclusive).
            lat: Optional latitude override (uses instance lat if omitted).
            lon: Optional longitude override (uses instance lon if omitted).

        Returns:
            Dict mapping each date to a list of 24 hourly temperatures in °C.
        """
        if lat is not None:
            self._lat = lat
        if lon is not None:
            self._lon = lon
        return await self.fetch_temperatures(session, period_from, period_to)
