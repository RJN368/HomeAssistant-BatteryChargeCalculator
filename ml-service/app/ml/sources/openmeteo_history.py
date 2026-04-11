"""Open-Meteo historical weather API client.

Fetches hourly outdoor temperature from the Open-Meteo Archive API and
interpolates it to a 30-minute UTC grid for use as the ``outdoor_temp``
ML training feature (D-7 feature #2).

No API key or authentication is required.  The archive endpoint supports
arbitrary historical date ranges in a single request, so no chunking is
needed.

The request always specifies ``timezone=UTC`` to avoid DST ambiguity in
the returned timestamps (D-18 guidance).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import aiohttp
import pandas as pd

_LOGGER = logging.getLogger(__name__)

OPENMETEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


class OpenMeteoHistorySource:
    """Open-Meteo historical weather API client.

    Returns hourly outdoor temperature (°C) from the Open-Meteo Archive
    API, resampled to 30-minute intervals via linear time interpolation.
    No authentication is required.

    The ``session`` parameter is accepted to match the
    :class:`~ml.sources.base.HistoricalDataSource` protocol signature.
    The caller's shared session is used directly; this source does not
    create or close sessions.
    """

    def __init__(self, latitude: float, longitude: float) -> None:
        """Initialise the Open-Meteo history source.

        Args:
            latitude:  Decimal latitude of the site (e.g. ``51.5074``).
            longitude: Decimal longitude of the site (e.g. ``-0.1278``).
        """
        self._latitude = latitude
        self._longitude = longitude

    @property
    def source_name(self) -> str:
        """Return the source identifier."""
        return "openmeteo"

    async def fetch(
        self,
        session: aiohttp.ClientSession,
        start: datetime,
        end: datetime,
    ) -> pd.Series | None:
        """Fetch historical outdoor temperature from the Open-Meteo archive.

        A single GET request retrieves hourly ``temperature_2m`` data for
        the full date range.  The hourly series is then resampled to a
        30-minute grid using ``interpolate(method="time")``.

        ``timezone=UTC`` is always passed in the request so that the
        returned timestamps carry no DST ambiguity.

        Args:
            session: Shared ``aiohttp.ClientSession`` owned by the caller.
            start:   UTC-aware start datetime (inclusive).
            end:     UTC-aware end datetime (exclusive).

        Returns:
            ``pd.Series`` (float64, UTC DatetimeIndex, 30-min freq, named
            ``"outdoor_temp_c"``) on success; ``None`` on hard failure.
            Partial responses (some ``null`` values) are returned as-is
            with ``NaN``; the training pipeline filters on
            ``valid_fraction_per_day ≥ 0.6`` (D-10).
        """
        params: dict[str, str | float] = {
            "latitude": self._latitude,
            "longitude": self._longitude,
            "start_date": start.date().strftime("%Y-%m-%d"),
            "end_date": end.date().strftime("%Y-%m-%d"),
            "hourly": "temperature_2m",
            "timezone": "UTC",
        }
        timeout = aiohttp.ClientTimeout(total=60)

        try:
            async with session.get(
                OPENMETEO_ARCHIVE_URL, params=params, timeout=timeout
            ) as resp:
                if not resp.ok:
                    _LOGGER.error(
                        "Open-Meteo API error: HTTP %d (lat=%.4f, lon=%.4f)",
                        resp.status,
                        self._latitude,
                        self._longitude,
                    )
                    return None
                data = await resp.json()

        except aiohttp.ClientError as exc:
            _LOGGER.error("Open-Meteo request failed (network): %s", exc)
            return None
        except asyncio.TimeoutError:
            _LOGGER.error(
                "Open-Meteo request timed out (lat=%.4f, lon=%.4f)",
                self._latitude,
                self._longitude,
            )
            return None

        hourly = data.get("hourly", {})
        times: list[str] = hourly.get("time", [])
        temps: list[float | None] = hourly.get("temperature_2m", [])

        if not times or not temps:
            _LOGGER.warning(
                "Open-Meteo returned empty hourly data for lat=%.4f, lon=%.4f",
                self._latitude,
                self._longitude,
            )
            return pd.Series(dtype="float64", name="outdoor_temp_c")

        # The API returns naive strings like "2024-01-01T00:00"; utc=True
        # treats them as UTC (consistent with timezone=UTC in the request)
        index = pd.to_datetime(times, utc=True)
        values = [float(t) if t is not None else float("nan") for t in temps]
        series = pd.Series(values, index=index, dtype="float64", name="outdoor_temp_c")

        # Upsample from hourly to 30-min via linear time interpolation
        series = series.resample("30min").interpolate(method="time")

        return series
