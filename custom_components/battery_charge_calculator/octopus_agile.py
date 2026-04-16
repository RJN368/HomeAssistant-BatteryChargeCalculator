"""Octopus Energy tariff rates client.

Fetches current import and export tariff rates and standing charges
for the account, regardless of tariff type.
"""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import logging

_LOGGER = logging.getLogger(__name__)

import aiohttp

OCTOPUS_API_BASE = "https://api.octopus.energy/v1"


def _product_code_from_tariff_code(tariff_code: str) -> str:
    """Derive product code from a tariff code.

    Tariff codes have the format E-1R-{product_code}-{region}, e.g.
    E-1R-AGILE-FLEX-22-11-25-B → product code is AGILE-FLEX-22-11-25.
    """
    parts = tariff_code.split("-")
    # parts[0] = E, parts[1] = 1R (or 2R), parts[-1] = region letter
    return "-".join(parts[2:-1])


def _active_agreement(agreements: list[dict]) -> dict | None:
    """Return the currently active agreement from a list, or None."""
    now = datetime.now(UTC)
    for agreement in agreements:
        valid_from_str = agreement.get("valid_from")
        valid_to_str = agreement.get("valid_to")
        if valid_from_str is None:
            continue
        valid_from = datetime.fromisoformat(valid_from_str)
        if valid_from.tzinfo is None:
            _LOGGER.warning(
                "Naive datetime detected in agreement valid_from; assuming UTC."
            )
            valid_from = valid_from.replace(tzinfo=UTC)
        valid_to = datetime.fromisoformat(valid_to_str) if valid_to_str else None
        if valid_to and valid_to.tzinfo is None:
            _LOGGER.warning(
                "Naive datetime detected in agreement valid_to; assuming UTC."
            )
            valid_to = valid_to.replace(tzinfo=UTC)
        if valid_from <= now and (valid_to is None or valid_to > now):
            return agreement
    return None


def _expand_to_30min_slots(raw_rates: list[dict], days: int = 2) -> list[dict]:
    """Expand rate bands into a contiguous 30-minute slot grid.

    Works for both Agile (already 30-min slots) and TOU tariffs like
    Intelligent Go where each rate covers a multi-hour band that repeats
    daily (e.g. 10p 23:30-05:30, 32p 05:30-23:30).
    """
    if not raw_rates:
        return []

    now = datetime.now(UTC)
    # Round down to the current 30-min boundary
    slot_start = now.replace(
        minute=0 if now.minute < 30 else 30, second=0, microsecond=0
    )
    end_time = slot_start + timedelta(days=days)

    slots: list[dict] = []
    current = slot_start
    last_value = raw_rates[0]["value_inc_vat"]

    # Ensure all rate datetimes are timezone-aware and convert to Europe/London for slot matching
    for r in raw_rates:
        for k in ("start", "end"):
            dt = r[k]
            if dt.tzinfo is None:
                _LOGGER.warning(f"Naive datetime detected in rate {k}; assuming UTC.")
                r[k] = dt.replace(tzinfo=UTC)
            # Always convert to Europe/London for slot logic
            r[k] = r[k].astimezone(ZoneInfo("Europe/London"))
    current = current.astimezone(ZoneInfo("Europe/London"))
    end_time = end_time.astimezone(ZoneInfo("Europe/London"))

    while current < end_time:
        # Find a rate whose window directly covers this slot
        rate_value = next(
            (r["value_inc_vat"] for r in raw_rates if r["start"] <= current < r["end"]),
            None,
        )

        if rate_value is None:
            # For daily-repeating TOU tariffs (e.g. Intelligent Go), the API
            # only returns today's bands. Match by time-of-day instead.
            current_tod = current.hour * 60 + current.minute
            for r in raw_rates:
                r_start_tod = r["start"].hour * 60 + r["start"].minute
                r_end_tod = r["end"].hour * 60 + r["end"].minute
                if r_end_tod > r_start_tod:
                    # Normal window (e.g. 05:30 → 23:30)
                    if r_start_tod <= current_tod < r_end_tod:
                        rate_value = r["value_inc_vat"]
                        break
                else:
                    # Overnight window crossing midnight (e.g. 23:30 → 05:30)
                    if current_tod >= r_start_tod or current_tod < r_end_tod:
                        rate_value = r["value_inc_vat"]
                        break

        if rate_value is None:
            rate_value = last_value
        else:
            last_value = rate_value

        slots.append(
            {
                "start": current,
                "end": current + timedelta(minutes=30),
                "value_inc_vat": rate_value,
            }
        )
        current += timedelta(minutes=30)

    return slots


class OctopusAgileRatesClient:
    """Client for fetching Octopus Energy electricity tariff rates."""

    def __init__(self, api_key: str, account_number: str) -> None:
        """Initialise the client with API credentials."""
        self.api_key = api_key
        self.account_number = account_number
        self.import_tariff_code: str | None = None
        self.export_tariff_code: str | None = None
        self.import_product_code: str | None = None
        self.export_product_code: str | None = None

    def _auth(self) -> aiohttp.BasicAuth:
        """Return basic auth for API requests."""
        return aiohttp.BasicAuth(self.api_key, "")

    async def _get_electricity_meters(self, session: aiohttp.ClientSession) -> list:
        """Fetch all electricity meter points for the account."""
        url = f"{OCTOPUS_API_BASE}/accounts/{self.account_number}/"
        async with session.get(url, auth=self._auth()) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["properties"][0]["electricity_meter_points"]

    async def _find_current_tariffs(self, session: aiohttp.ClientSession) -> None:
        """Resolve current import and export tariff codes from active agreements.

        Uses the is_export flag on each meter point to distinguish import from
        export. Selects the active agreement based on valid_from/valid_to dates,
        with no tariff-name string matching.
        """
        if (
            self.import_product_code is not None
            and self.export_product_code is not None
        ):
            return

        meters = await self._get_electricity_meters(session)
        for meter in meters:
            is_export = meter.get("is_export", False)
            agreement = _active_agreement(meter.get("agreements", []))
            if agreement is None:
                continue
            tariff_code = agreement["tariff_code"]
            product_code = _product_code_from_tariff_code(tariff_code)
            if is_export:
                self.export_tariff_code = tariff_code
                self.export_product_code = product_code
            else:
                self.import_tariff_code = tariff_code
                self.import_product_code = product_code

    async def fetch_standing_charge(self, session: aiohttp.ClientSession) -> float:
        """Fetch the current standing charge (p/day) for the import tariff."""
        await self._find_current_tariffs(session)
        url = (
            f"{OCTOPUS_API_BASE}/products/{self.import_product_code}"
            f"/electricity-tariffs/{self.import_tariff_code}/standing-charges/"
        )
        async with session.get(url, auth=self._auth()) as resp:
            resp.raise_for_status()
            data = await resp.json()
            results = data.get("results", [])
            if results:
                return float(results[0]["value_inc_vat"]) / 100
            return 0.0

    async def fetch_rates(
        self, session: aiohttp.ClientSession, export: bool, days: int = 2
    ) -> list[dict]:
        """Fetch 30-minute unit rate slots for the current import or export tariff.

        Args:
            session: aiohttp client session.
            export: If True, fetch export tariff rates; otherwise import.
            days: Number of days ahead to request (unused currently, reserved).

        Returns:
            List of dicts with keys: start, end, value_inc_vat (£/kWh).
        """
        await self._find_current_tariffs(session)
        product_code = self.export_product_code if export else self.import_product_code
        tariff_code = self.export_tariff_code if export else self.import_tariff_code
        url = (
            f"{OCTOPUS_API_BASE}/products/{product_code}"
            f"/electricity-tariffs/{tariff_code}/standard-unit-rates/"
        )
        async with session.get(url, auth=self._auth()) as resp:
            resp.raise_for_status()
            data = await resp.json()
            _far_future = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)
            raw_rates = []
            for r in data["results"]:
                if not r.get("valid_from"):
                    continue
                start = datetime.fromisoformat(r["valid_from"])
                if start.tzinfo is None:
                    _LOGGER.warning(
                        "Naive datetime detected in rate start; assuming UTC."
                    )
                    start = start.replace(tzinfo=UTC)
                end = (
                    datetime.fromisoformat(r["valid_to"])
                    if r.get("valid_to")
                    else _far_future
                )
                if end.tzinfo is None:
                    _LOGGER.warning(
                        "Naive datetime detected in rate end; assuming UTC."
                    )
                    end = end.replace(tzinfo=UTC)
                raw_rates.append(
                    {
                        "start": start,
                        "end": end,
                        "value_inc_vat": float(r["value_inc_vat"]) / 100,
                    }
                )
            raw_rates = sorted(raw_rates, key=lambda r: r["start"])
            return _expand_to_30min_slots(raw_rates, days)
