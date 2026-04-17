"""TariffComparisonClient — Octopus API data fetcher for tariff comparison.

Follows the existing OctopusAgileRatesClient pattern (D-14): caller passes an
aiohttp.ClientSession; this client never creates sessions.

All Octopus endpoints used here are documented in §3 of tariff-comparison.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

_OCTOPUS_BASE = "https://api.octopus.energy/v1"
_PAGE_SIZE = 25000


def _product_code_from_tariff_code(tariff_code: str) -> str:
    """Derive the product code from a tariff code.

    e.g. 'E-1R-AGILE-FLEX-22-11-25-B' -> 'AGILE-FLEX-22-11-25'
    Strips the leading 'E-1R-' (or 'E-2R-') and the trailing region letter.
    """
    parts = tariff_code.split("-")
    # parts[0]='E', parts[1]='1R', parts[-1]='B' (region)
    return "-".join(parts[2:-1])


async def _paginate(
    session: aiohttp.ClientSession,
    url: str,
    params: dict[str, Any],
    auth: aiohttp.BasicAuth | None = None,
) -> list[dict]:
    """Fetch all pages from a paginated Octopus endpoint.

    Follows the 'next' cursor until it is null, accumulating all result rows.
    """
    results: list[dict] = []
    next_url: str | None = url
    page_params = dict(params)

    while next_url:
        async with session.get(next_url, params=page_params, auth=auth) as resp:
            resp.raise_for_status()
            data = await resp.json()
        results.extend(data.get("results", []))
        next_url = data.get("next")
        page_params = {}  # subsequent pages use the full URL from 'next'

    return results


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string to a timezone-aware UTC datetime, or None."""
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _build_historical_rate_map(
    raw_rates: list[dict],
    period_from: datetime,
    period_to: datetime,
) -> dict[datetime, float]:
    """Expand raw rate bands into a {slot_start: rate_p_per_kwh} dict.

    Works for all tariff types:
    - Agile: rates already at 30-min resolution
    - Standard/Fixed: single rate covering the full period
    - TOU (Intelligent Go etc.): daily-repeating bands

    Slot starts are UTC timezone-aware, rounded to 30-min boundaries.
    Missing slots fall back to the most recent known rate (forward-fill).

    NOTE (from Hockney): The caller must ensure that a rate in force AT
    period_from is included in raw_rates so that forward-fill has a seed.
    See TariffComparisonClient.fetch_unit_rates() for the pre-window fetch.
    """
    if not raw_rates:
        return {}

    # Sort by valid_from ascending
    sorted_rates = sorted(raw_rates, key=lambda r: r["valid_from"])

    # Build slot boundaries
    slot = period_from.replace(second=0, microsecond=0)
    # Normalise to 30-min boundary
    if slot.minute not in (0, 30):
        slot = slot.replace(minute=0 if slot.minute < 30 else 30)

    rate_map: dict[datetime, float] = {}
    current_rate: float | None = None
    rate_idx = 0
    n_rates = len(sorted_rates)

    while slot < period_to:
        # Advance rate pointer past rates whose valid window has ended
        while rate_idx < n_rates:
            r = sorted_rates[rate_idx]
            vf = r["valid_from"]
            vt = r.get("valid_to")
            if vf <= slot and (vt is None or vt > slot):
                current_rate = r["value_inc_vat"]
                break
            if vf > slot:
                break
            rate_idx += 1

        if current_rate is not None:
            rate_map[slot] = current_rate

        slot = slot + timedelta(minutes=30)

    # Forward-fill any gaps (e.g. API gaps mid-year)
    last_known: float | None = None
    gap_start: datetime | None = None
    gap_count = 0

    all_slots = sorted(rate_map.keys())
    expected = period_from
    filled: dict[datetime, float] = {}

    for s in all_slots:
        # Fill any expected slots before s
        while expected < s:
            if last_known is not None:
                filled[expected] = last_known
                if gap_start is None:
                    gap_start = expected
                gap_count += 1
            expected = expected + timedelta(minutes=30)
        last_known = rate_map[s]
        filled[s] = last_known
        if gap_start is not None and gap_count:
            _LOGGER.debug(
                "Rate map gap: forward-filled %d slots from %s with rate %.4f",
                gap_count,
                gap_start,
                last_known,
            )
            gap_start = None
            gap_count = 0
        expected = s + timedelta(minutes=30)

    return filled


class TariffComparisonClient:
    """Fetches historical consumption and tariff rate data from the Octopus Energy API.

    Authentication for consumption endpoints: HTTP Basic Auth — API key as
    username, empty password.  Rate/standing-charge endpoints are public.
    """

    def __init__(
        self,
        api_key: str,
        mpan: str,
        meter_serial: str,
        export_mpan: str | None = None,
        export_meter_serial: str | None = None,
    ) -> None:
        """Initialise the client with Octopus credentials."""
        self._api_key = api_key
        self._mpan = mpan
        self._meter_serial = meter_serial
        self._export_mpan = export_mpan
        self._export_meter_serial = export_meter_serial
        self._auth = aiohttp.BasicAuth(api_key, "")

    async def fetch_consumption(
        self,
        session: aiohttp.ClientSession,
        period_from: datetime,
        period_to: datetime,
        export: bool = False,
    ) -> list[dict]:
        """Fetch half-hourly consumption readings for the import or export meter.

        Returns a list of dicts with timezone-aware ``interval_start`` datetimes
        and float ``consumption`` kWh values, sorted ascending.

        Paginates automatically by following the 'next' cursor until None.
        Raises ValueError if export=True and no export MPAN/serial is configured.
        """
        if export:
            if not self._export_mpan or not self._export_meter_serial:
                raise ValueError(
                    "Export consumption requested but no export MPAN/serial configured"
                )
            mpan = self._export_mpan
            serial = self._export_meter_serial
        else:
            mpan = self._mpan
            serial = self._meter_serial

        url = (
            f"{_OCTOPUS_BASE}/electricity-meter-points/{mpan}"
            f"/meters/{serial}/consumption/"
        )
        params = {
            "period_from": period_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "period_to": period_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "page_size": _PAGE_SIZE,
        }
        raw = await _paginate(session, url, params, auth=self._auth)

        result: list[dict] = []
        for row in raw:
            result.append(
                {
                    "interval_start": _parse_iso(row["interval_start"]),
                    "consumption": float(row["consumption"]),
                }
            )
        result.sort(key=lambda r: r["interval_start"])
        return result

    async def fetch_unit_rates(
        self,
        session: aiohttp.ClientSession,
        tariff_code: str,
        period_from: datetime,
        period_to: datetime,
    ) -> list[dict]:
        """Fetch historical unit rates for any tariff.

        Returns a list of dicts: ``{valid_from, valid_to, value_inc_vat}``
        with timezone-aware datetimes.  Sorted ascending by valid_from.

        Includes a pre-window fetch to seed the rate in force at period_from
        (handles Standard/Fixed tariffs whose rate predates the window —
        see Hockney's note in _build_historical_rate_map).
        """
        product_code = _product_code_from_tariff_code(tariff_code)
        url = (
            f"{_OCTOPUS_BASE}/products/{product_code}"
            f"/electricity-tariffs/{tariff_code}/standard-unit-rates/"
        )

        # Primary fetch — rates within the window
        params = {
            "period_from": period_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "period_to": period_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "page_size": _PAGE_SIZE,
        }
        raw = await _paginate(session, url, params)

        # Pre-window seed — get the rate in force at period_from (fixes fixed/TOU tariffs)
        seed_params = {
            "period_to": period_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "page_size": 1,
        }
        try:
            seed_raw = await _paginate(session, url, seed_params)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Pre-window seed fetch failed for %s: %s", tariff_code, exc)
            seed_raw = []

        # Prepend seed with valid_from = period_from so forward-fill starts correctly
        combined: list[dict] = []
        for row in seed_raw:
            combined.append(
                {
                    "valid_from": period_from,
                    "valid_to": _parse_iso(row.get("valid_to")),
                    "value_inc_vat": float(row["value_inc_vat"]),
                }
            )
        for row in raw:
            combined.append(
                {
                    "valid_from": _parse_iso(row["valid_from"]),
                    "valid_to": _parse_iso(row.get("valid_to")),
                    "value_inc_vat": float(row["value_inc_vat"]),
                }
            )
        combined.sort(key=lambda r: r["valid_from"])
        return combined

    async def fetch_standing_charges(
        self,
        session: aiohttp.ClientSession,
        tariff_code: str,
        period_from: datetime,
        period_to: datetime,
    ) -> list[dict]:
        """Fetch historical standing charges for an import tariff.

        Returns a list of dicts: ``{valid_from, valid_to, value_inc_vat}``
        where value_inc_vat is in pence/day (inc VAT).
        """
        product_code = _product_code_from_tariff_code(tariff_code)
        url = (
            f"{_OCTOPUS_BASE}/products/{product_code}"
            f"/electricity-tariffs/{tariff_code}/standing-charges/"
        )
        params = {
            "period_from": period_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "period_to": period_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        raw = await _paginate(session, url, params)

        result: list[dict] = []
        for row in raw:
            result.append(
                {
                    "valid_from": _parse_iso(row["valid_from"]),
                    "valid_to": _parse_iso(row.get("valid_to")),
                    "value_inc_vat": float(row["value_inc_vat"]),
                }
            )
        result.sort(key=lambda r: r["valid_from"])
        return result

    def build_rate_map(
        self,
        raw_rates: list[dict],
        period_from: datetime,
        period_to: datetime,
    ) -> dict[datetime, float]:
        """Build a {slot_start: rate_p_per_kwh} lookup dict for the given period."""
        return _build_historical_rate_map(raw_rates, period_from, period_to)

    async def fetch_export_tariff_code(
        self,
        session: aiohttp.ClientSession,
    ) -> str | None:
        """Resolve the active export tariff code from the export MPAN endpoint.

        Uses ``GET /v1/electricity-meter-points/{export_mpan}/`` which is
        authenticated but does NOT require an account number.  This is the
        fallback when account-based tariff resolution fails or the account
        number is not configured.

        Returns the tariff_code string of the active agreement, or None if
        no export MPAN is configured or no active agreement is found.
        """
        if not self._export_mpan:
            return None
        url = f"{_OCTOPUS_BASE}/electricity-meter-points/{self._export_mpan}/"
        async with session.get(url, auth=self._auth) as resp:
            resp.raise_for_status()
            data = await resp.json()
        agreements: list[dict] = data.get("agreements", [])
        _LOGGER.debug(
            "MPAN endpoint response for %s — top-level keys: %s; agreements count: %d",
            self._export_mpan,
            list(data.keys()),
            len(agreements),
        )
        if not agreements:
            _LOGGER.debug(
                "MPAN endpoint returned no agreements for %s", self._export_mpan
            )
            return None
        now = datetime.now(timezone.utc)
        # Sort descending by valid_from so the most-recent active agreement wins
        for agreement in sorted(
            agreements, key=lambda a: a.get("valid_from", ""), reverse=True
        ):
            valid_from_str = agreement.get("valid_from")
            valid_to_str = agreement.get("valid_to")
            if not valid_from_str:
                continue
            valid_from = datetime.fromisoformat(valid_from_str)
            if valid_from.tzinfo is None:
                valid_from = valid_from.replace(tzinfo=timezone.utc)
            valid_to = None
            if valid_to_str:
                valid_to = datetime.fromisoformat(valid_to_str)
                if valid_to.tzinfo is None:
                    valid_to = valid_to.replace(tzinfo=timezone.utc)
            if valid_from <= now and (valid_to is None or valid_to > now):
                return agreement.get("tariff_code")
        return None
