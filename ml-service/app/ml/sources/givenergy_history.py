"""GivEnergy Cloud REST API client for historical house consumption.

Fetches half-hourly (30-minute) house load data from the GivEnergy Cloud
data-points endpoint for use as ML training target data.

Uses ``GET /v1/inverter/{serial}/data-points/{YYYY-MM-DD}``, one request
per calendar day.  The API returns cumulative daily totals per slot;
per-30-min incremental kWh is derived by differencing consecutive readings.

All returned timestamps are normalised to UTC at ingestion (D-18, D-13/Q4).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Union
from zoneinfo import ZoneInfo

import aiohttp
import pandas as pd

_LOGGER = logging.getLogger(__name__)

GIVENERGY_API_BASE = "https://api.givenergy.cloud/v1"

_LONDON_TZ = ZoneInfo("Europe/London")
# Number of data-points per page; 512 is well above the 48 slots per day
_PAGE_SIZE = 512


def _normalise_to_utc(ts_input: Union[str, datetime]) -> datetime:
    """Parse a GivEnergy timestamp and return a UTC-aware ``datetime``.

    Handles all three forms returned in practice:

    - Naive ISO string (no offset):
        Assumed to be Europe/London local time; localised then converted to
        UTC.  DST ambiguity on fall-back hour is resolved by taking the
        first (summer-time) occurrence; non-existent (spring-forward) wall
        times are shifted forward.
    - ISO string with UTC offset (``+00:00``, ``Z``, or any other offset):
        Parsed and converted to UTC directly.
    - ``datetime`` object (already UTC-aware or naive):
        UTC-aware objects are returned after ``tz_convert``; naive objects
        are treated as Europe/London (same rule as naive strings).

    Args:
        ts_input: An ISO-8601 timestamp string or a ``datetime`` object.

    Returns:
        A UTC-aware ``datetime`` instance.
    """
    ts = pd.Timestamp(ts_input)
    if ts.tzinfo is None:
        # Naive — assume Europe/London (D-18, DST-safe localisation).
        # pandas 2+ disallows ambiguous='infer' for scalar Timestamps; use
        # ambiguous=False (first/summer-time occurrence) which matches the
        # documented DST-ambiguity resolution intent.
        try:
            ts = ts.tz_localize(
                _LONDON_TZ,
                ambiguous=False,
                nonexistent="shift_forward",
            )
        except Exception:
            # Last-resort fallback: convert via UTC assumption
            ts = ts.tz_localize(UTC)
    return ts.tz_convert(UTC).to_pydatetime()


class GivEnergyHistorySource:
    """GivEnergy Cloud REST API client for historical house consumption.

    Issues GET requests to ``/v1/inverter/{serial}/data-points/{date}``
    (one request per calendar day).  Pagination is followed via
    ``links.next`` in the response.

    The ``total.consumption`` field in each slot is a running daily total
    (resets to zero at midnight).  Per-30-min incremental kWh is obtained
    by differencing consecutive totals within each day.

    Auth:   ``Authorization: Bearer {api_token}`` header.
    Method: GET.
    """

    def __init__(self, api_token: str, serial_number: str) -> None:
        """Initialise the GivEnergy history source.

        Args:
            api_token:     GivEnergy Cloud API bearer token.
            serial_number: GivEnergy inverter serial number.
        """
        self._api_token = api_token
        self._serial_number = serial_number

    @property
    def source_name(self) -> str:
        """Return the source identifier."""
        return "givenergy"

    async def fetch(
        self,
        session: aiohttp.ClientSession,
        start: datetime,
        end: datetime,
    ) -> pd.Series | None:
        """Fetch historical house consumption from the GivEnergy Cloud API.

        One GET request is issued per calendar day in the range
        [start.date(), end.date()).  Timestamps are normalised to UTC via
        :func:`_normalise_to_utc`.

        The API returns cumulative daily totals per time slot.  These are
        differenced within each day to produce per-30-min incremental kWh
        values.  The output is resampled to a regular 30-min UTC grid;
        single-slot gaps are forward-filled.

        Args:
            session: Shared ``aiohttp.ClientSession`` owned by the caller.
            start:   UTC-aware start datetime (inclusive).
            end:     UTC-aware end datetime (exclusive).

        Returns:
            ``pd.Series`` (float64, UTC DatetimeIndex, 30-min freq, named
            ``"consumption_kwh"``) on success; ``None`` on hard failure.
        """
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Accept": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=30)

        # Collect (timestamp, cumulative_total) tuples, grouped by day
        records_by_date: dict[date, list[tuple[datetime, float]]] = {}

        current_date = start.date()
        end_date = end.date()
        while current_date < end_date:
            day_records = await self._fetch_day(session, headers, timeout, current_date)
            if day_records is None:
                return None  # hard failure — abort
            if day_records:
                records_by_date[current_date] = day_records
            current_date += timedelta(days=1)

        if not records_by_date:
            return pd.Series(dtype="float64", name="consumption_kwh")

        # Convert cumulative daily totals → per-slot incremental kWh.
        # Each day's running total resets at midnight, so diff within the day.
        incremental: list[tuple[datetime, float]] = []
        for d in sorted(records_by_date):
            prev_total = 0.0
            for ts, cumulative in records_by_date[d]:
                increment = max(0.0, cumulative - prev_total)
                incremental.append((ts, increment))
                prev_total = cumulative

        # Build Series
        index = pd.DatetimeIndex(
            [r[0] for r in incremental], dtype="datetime64[ns, UTC]"
        )
        values = [r[1] for r in incremental]
        series = pd.Series(values, index=index, dtype="float64", name="consumption_kwh")

        # Remove DST fall-back duplicates (keep first = summer-time reading)
        series = series[~series.index.duplicated(keep="first")]
        series = series.sort_index()

        # Resample to uniform 30-min grid; forward-fill single-slot gaps only
        series = series.resample("30min").sum()
        series = series.ffill(limit=1)

        return series

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_day(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        timeout: aiohttp.ClientTimeout,
        target_date: date,
    ) -> list[tuple[datetime, float]] | None:
        """Fetch all cumulative data-points for a single calendar day.

        Follows ``links.next`` pagination until all pages are consumed.

        Args:
            session:     Shared ``aiohttp.ClientSession``.
            headers:     Request headers including ``Authorization``.
            timeout:     Per-request timeout.
            target_date: The calendar date to fetch.

        Returns:
            Ordered list of ``(utc_datetime, cumulative_consumption_kwh)``
            tuples, or ``None`` on hard failure.
        """
        date_str = target_date.strftime("%Y-%m-%d")
        base_url = (
            f"{GIVENERGY_API_BASE}/inverter/{self._serial_number}"
            f"/data-points/{date_str}?pageSize={_PAGE_SIZE}"
        )

        records: list[tuple[datetime, float]] = []
        next_url: str | None = base_url

        while next_url:
            page_records, next_url = await self._get_page(
                session, headers, timeout, next_url, date_str
            )
            if page_records is None:
                return None  # hard failure
            records.extend(page_records)

        return records

    async def _get_page(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        timeout: aiohttp.ClientTimeout,
        url: str,
        date_str: str,
    ) -> tuple[list[tuple[datetime, float]] | None, str | None]:
        """GET a single page of data-points and return (records, next_url).

        Handles HTTP 401/403 (auth failure — hard stop), 429 (rate limit,
        single retry), other non-OK responses, and network/timeout errors.

        Args:
            session:  Shared ``aiohttp.ClientSession``.
            headers:  Request headers including ``Authorization``.
            timeout:  Per-request timeout.
            url:      Full URL of the page to fetch.
            date_str: Human-readable date for log messages.

        Returns:
            Tuple of (records, next_page_url).  ``records`` is ``None`` on
            hard failure.  ``next_page_url`` is ``None`` when no further
            pages exist.
        """
        data: dict | None = None

        try:
            async with session.get(url, headers=headers, timeout=timeout) as resp:
                if resp.status in (401, 403):
                    _LOGGER.error(
                        "GivEnergy auth failed — check API token (HTTP %d)",
                        resp.status,
                    )
                    return None, None

                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    _LOGGER.warning(
                        "GivEnergy rate-limited; retrying after %d s",
                        retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    # Single retry after back-off
                    async with session.get(
                        url, headers=headers, timeout=timeout
                    ) as retry_resp:
                        if not retry_resp.ok:
                            _LOGGER.error(
                                "GivEnergy API error after retry: HTTP %d for date %s",
                                retry_resp.status,
                                date_str,
                            )
                            return None, None
                        data = await retry_resp.json()
                elif not resp.ok:
                    _LOGGER.error(
                        "GivEnergy API error: HTTP %d for date %s",
                        resp.status,
                        date_str,
                    )
                    return None, None
                else:
                    data = await resp.json()

        except aiohttp.ClientError as exc:
            _LOGGER.error("GivEnergy request failed (network): %s", exc)
            return None, None
        except asyncio.TimeoutError:
            _LOGGER.error(
                "GivEnergy request timed out for date %s",
                date_str,
            )
            return None, None

        if data is None:
            return [], None

        # Parse the data array
        records: list[tuple[datetime, float]] = []
        for item in data.get("data", []):
            ts_raw = item.get("time")
            totals = item.get("total")
            if ts_raw is None or not isinstance(totals, dict):
                continue
            consumption = totals.get("consumption")
            if consumption is None:
                continue
            try:
                utc_dt = _normalise_to_utc(ts_raw)
                records.append((utc_dt, float(consumption)))
            except (ValueError, TypeError) as exc:
                _LOGGER.debug("Skipping malformed GivEnergy record %s: %s", item, exc)

        # Follow pagination
        next_url: str | None = data.get("links", {}).get("next")

        return records, next_url
