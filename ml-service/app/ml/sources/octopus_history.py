"""Octopus Energy consumption API client.

Fetches half-hourly grid import electricity data from the Octopus Energy
API for use as an ML training feature (``octopus_import_kwh``, D-7 feature
#16) or as the primary consumption signal when
``ML_CONSUMPTION_SOURCE == "octopus"``.

Grid import data represents only electricity drawn from the grid; it does
**not** include solar self-consumption.  When used as feature #16 in
``"both"`` mode, this lets HistGBR implicitly learn the solar generation
signal (low import relative to GivEnergy consumption → high solar output).

Auth:      ``aiohttp.BasicAuth(api_key, "")``  (same pattern as rate-fetch)
Pagination: follows the ``next`` cursor in the JSON response.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone as _tz

import aiohttp
import pandas as pd

_LOGGER = logging.getLogger(__name__)

OCTOPUS_API_BASE = "https://api.octopus.energy/v1"


class OctopusHistorySource:
    """Octopus Energy half-hourly grid import consumption API client.

    Fetches electricity import data for a single meter point identified by
    MPAN and meter serial number.  The ``meter_serial`` field is required;
    if it is empty the source returns ``None`` immediately with a warning.

    Pagination is handled automatically by following the ``next`` URL
    returned in each response until it is ``null``.
    """

    def __init__(self, api_key: str, mpan: str, meter_serial: str) -> None:
        """Initialise the Octopus history source.

        Args:
            api_key:       Octopus Energy API key.
            mpan:          Meter Point Administration Number (MPAN / supply
                           point identifier).
            meter_serial:  Electricity meter serial number.  Required; an
                           empty string causes ``fetch`` to return ``None``.
        """
        self._api_key = api_key
        self._mpan = mpan
        self._meter_serial = meter_serial

    @property
    def source_name(self) -> str:
        """Return the source identifier."""
        return "octopus"

    async def fetch(
        self,
        session: aiohttp.ClientSession,
        start: datetime,
        end: datetime,
    ) -> pd.Series | None:
        """Fetch historical grid import consumption from the Octopus API.

        Follows pagination automatically.  All Octopus timestamps are UTC
        so no timezone conversion is required.

        Args:
            session: Shared ``aiohttp.ClientSession`` owned by the caller.
            start:   UTC-aware start datetime (inclusive).
            end:     UTC-aware end datetime (exclusive).

        Returns:
            ``pd.Series`` (float64, UTC DatetimeIndex, 30-min freq, named
            ``"octopus_import_kwh"``) on success; ``None`` if the meter
            serial is unconfigured or a hard API failure occurs.
        """
        if not self._mpan or not self._mpan.strip():
            _LOGGER.warning(
                "Octopus MPAN (meter point) not configured — Octopus history unavailable"
            )
            return None
        if not self._meter_serial.strip():
            _LOGGER.warning(
                "OCTOPUS_METER_SERIAL not configured — Octopus history unavailable"
            )
            return None

        auth = aiohttp.BasicAuth(self._api_key, "")
        base_url = (
            f"{OCTOPUS_API_BASE}/electricity-meter-points/{self._mpan}"
            f"/meters/{self._meter_serial}/consumption/"
        )
        params: dict[str, str | int] = {
            "period_from": start.isoformat(),
            "period_to": end.isoformat(),
            "page_size": 1500,
            "order_by": "period",
        }
        timeout = aiohttp.ClientTimeout(total=30)

        all_items: list[dict] = []
        next_url: str | None = None
        first_page = True

        while first_page or next_url is not None:
            first_page = False
            # On subsequent pages the cursor URL already encodes all params
            fetch_url: str = next_url if next_url else base_url
            fetch_params = None if next_url else params

            data = await self._get_page(session, fetch_url, fetch_params, auth, timeout)
            if data is None:
                return None

            all_items.extend(data.get("results", []))
            next_url = data.get("next")  # None terminates pagination

        if not all_items:
            return pd.Series(dtype="float64", name="octopus_import_kwh")

        # Octopus returns ISO-8601 strings that may carry mixed UTC offsets
        # (e.g. +00:00 in winter, +01:00 in summer for BST slots).
        # pd.Timestamp().tz_convert() triggers a lazy import of tzdata inside
        # the event loop, which HA flags as a blocking call.  Use stdlib
        # datetime.fromisoformat() instead — it handles offset-aware strings
        # natively (Python 3.11+) with no external imports, then convert to
        # UTC using standard timedelta arithmetic.
        def _to_utc(ts_str: str) -> datetime:
            dt = datetime.fromisoformat(ts_str)
            if dt.tzinfo is not None:
                dt = dt.astimezone(_tz.utc)
            return dt

        utc_datetimes = [_to_utc(item["interval_start"]) for item in all_items]
        index = pd.DatetimeIndex(utc_datetimes, dtype="datetime64[ns, UTC]")
        values = [float(item["consumption"]) for item in all_items]

        series = pd.Series(
            values, index=index, dtype="float64", name="octopus_import_kwh"
        )
        series = series[~series.index.duplicated(keep="first")]
        series = series.sort_index()
        series = series.resample("30min").mean()

        return series

    async def _get_page(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: dict | None,
        auth: aiohttp.BasicAuth,
        timeout: aiohttp.ClientTimeout,
    ) -> dict | None:
        """Fetch a single page from the Octopus consumption endpoint.

        Handles HTTP 401/403 (auth failure), 429 (rate limit, single retry),
        other 4xx/5xx errors, and network/timeout exceptions.

        Args:
            session: Shared ``aiohttp.ClientSession``.
            url:     Page URL (base or ``next`` cursor URL).
            params:  Query parameters (``None`` when using a cursor URL).
            auth:    ``BasicAuth`` credentials.
            timeout: Per-request timeout.

        Returns:
            Parsed JSON ``dict`` on success; ``None`` on hard failure.
        """
        try:
            async with session.get(
                url, params=params, auth=auth, timeout=timeout
            ) as resp:
                if resp.status in (401, 403):
                    _LOGGER.error(
                        "Octopus API auth failed — check API key (HTTP %d)",
                        resp.status,
                    )
                    return None

                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    _LOGGER.warning(
                        "Octopus API rate-limited; retrying after %d s",
                        retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    async with session.get(
                        url, params=params, auth=auth, timeout=timeout
                    ) as retry_resp:
                        if not retry_resp.ok:
                            _LOGGER.error(
                                "Octopus API error after retry: HTTP %d",
                                retry_resp.status,
                            )
                            return None
                        return await retry_resp.json()

                if not resp.ok:
                    _LOGGER.error("Octopus API error: HTTP %d for %s", resp.status, url)
                    return None

                return await resp.json()

        except aiohttp.ClientError as exc:
            _LOGGER.error("Octopus request failed (network): %s", exc)
            return None
        except asyncio.TimeoutError:
            _LOGGER.error("Octopus request timed out for %s", url)
            return None
