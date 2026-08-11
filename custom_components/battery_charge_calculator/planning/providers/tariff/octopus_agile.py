"""Octopus Energy tariff rates client.

Fetches current import and export tariff rates and standing charges
for the account, regardless of tariff type.
"""

from datetime import UTC, datetime, timedelta
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
    return "-".join(parts[2:-1])


def _active_agreement(agreements: list[dict]) -> dict | None:
    """Return the currently active agreement, or None."""
    return _active_agreement_at(agreements, datetime.now(UTC))


def _active_agreement_at(agreements: list[dict], now: datetime) -> dict | None:
    """Return the active agreement at now, or None."""
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
    """Expand rate bands into a contiguous 30-minute slot grid."""
    if not raw_rates:
        return []

    now = datetime.now(UTC)
    slot_start = now.replace(
        minute=0 if now.minute < 30 else 30, second=0, microsecond=0
    )
    end_time = slot_start + timedelta(days=days)

    slots: list[dict] = []
    current = slot_start
    last_value = raw_rates[0]["value_inc_vat"]

    for r in raw_rates:
        for k in ("start", "end"):
            dt = r[k]
            if dt.tzinfo is None:
                _LOGGER.warning("Naive datetime detected in rate %s; assuming UTC.", k)
                r[k] = dt.replace(tzinfo=UTC)
            r[k] = r[k].astimezone(ZoneInfo("Europe/London"))
    current = current.astimezone(ZoneInfo("Europe/London"))
    end_time = end_time.astimezone(ZoneInfo("Europe/London"))

    while current < end_time:
        rate_value = next(
            (r["value_inc_vat"] for r in raw_rates if r["start"] <= current < r["end"]),
            None,
        )

        if rate_value is None:
            current_tod = current.hour * 60 + current.minute
            for r in raw_rates:
                r_start_tod = r["start"].hour * 60 + r["start"].minute
                r_end_tod = r["end"].hour * 60 + r["end"].minute
                if r_end_tod > r_start_tod:
                    if r_start_tod <= current_tod < r_end_tod:
                        rate_value = r["value_inc_vat"]
                        break
                else:
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

    def __init__(
        self,
        api_key: str,
        account_number: str,
        tariff_cache_ttl: timedelta = timedelta(minutes=30),
    ) -> None:
        self.api_key = api_key
        self.account_number = account_number
        self.import_tariff_code: str | None = None
        self.export_tariff_code: str | None = None
        self.import_product_code: str | None = None
        self.export_product_code: str | None = None
        self._tariff_cache_ttl = tariff_cache_ttl
        self._tariffs_last_resolved_at: datetime | None = None
        self._tariffs_refresh_after: datetime | None = None

    def _auth(self) -> aiohttp.BasicAuth:
        return aiohttp.BasicAuth(self.api_key, "")

    async def _get_electricity_meters(self, session: aiohttp.ClientSession) -> list:
        url = f"{OCTOPUS_API_BASE}/accounts/{self.account_number}/"
        async with session.get(url, auth=self._auth()) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["properties"][0]["electricity_meter_points"]

    async def refresh_current_tariffs(
        self,
        session: aiohttp.ClientSession,
        *,
        now: datetime | None = None,
        force_refresh: bool = False,
    ) -> None:
        await self._find_current_tariffs(session, now=now, force_refresh=force_refresh)

    async def _find_current_tariffs(
        self,
        session: aiohttp.ClientSession,
        *,
        now: datetime | None = None,
        force_refresh: bool = False,
    ) -> None:
        now_dt = now or datetime.now(UTC)
        has_cached_tariffs = (
            self.import_tariff_code is not None
            or self.export_tariff_code is not None
            or self.import_product_code is not None
            or self.export_product_code is not None
        )
        ttl_valid = self._tariffs_last_resolved_at is not None and now_dt < (
            self._tariffs_last_resolved_at + self._tariff_cache_ttl
        )
        boundary_valid = (
            self._tariffs_refresh_after is None or now_dt < self._tariffs_refresh_after
        )

        if not force_refresh and has_cached_tariffs and ttl_valid and boundary_valid:
            return

        meters = await self._get_electricity_meters(session)
        next_boundary_candidates: list[datetime] = []

        self.import_tariff_code = None
        self.export_tariff_code = None
        self.import_product_code = None
        self.export_product_code = None

        for meter in meters:
            is_export = meter.get("is_export", False)
            agreement = _active_agreement_at(meter.get("agreements", []), now_dt)
            if agreement is None:
                continue
            tariff_code = agreement["tariff_code"]
            product_code = _product_code_from_tariff_code(tariff_code)

            valid_to_str = agreement.get("valid_to")
            if valid_to_str:
                valid_to = datetime.fromisoformat(valid_to_str)
                if valid_to.tzinfo is None:
                    _LOGGER.warning(
                        "Naive datetime detected in agreement valid_to; assuming UTC."
                    )
                    valid_to = valid_to.replace(tzinfo=UTC)
                if valid_to > now_dt:
                    next_boundary_candidates.append(valid_to)

            if is_export:
                self.export_tariff_code = tariff_code
                self.export_product_code = product_code
            else:
                self.import_tariff_code = tariff_code
                self.import_product_code = product_code

        self._tariffs_last_resolved_at = now_dt
        ttl_boundary = now_dt + self._tariff_cache_ttl
        next_boundary = (
            min(next_boundary_candidates) if next_boundary_candidates else None
        )
        self._tariffs_refresh_after = (
            min(ttl_boundary, next_boundary)
            if next_boundary is not None
            else ttl_boundary
        )

    async def fetch_standing_charge(self, session: aiohttp.ClientSession) -> float:
        await self.refresh_current_tariffs(session)
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
        await self.refresh_current_tariffs(session)
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

    async def async_fetch_today_consumption(
        self,
        session: aiohttp.ClientSession,
        mpan: str,
        meter_serial: str,
    ) -> list[dict]:
        if not mpan or not meter_serial:
            _LOGGER.warning(
                "MPAN or meter serial not configured — skipping today's consumption fetch"
            )
            return []

        london = ZoneInfo("Europe/London")
        now_utc = datetime.now(UTC)
        today_london_midnight = now_utc.astimezone(london).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        period_from = today_london_midnight.astimezone(UTC)

        url = (
            f"{OCTOPUS_API_BASE}/electricity-meter-points/{mpan}"
            f"/meters/{meter_serial}/consumption/"
        )
        params = {
            "period_from": period_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "period_to": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "page_size": 48,
            "order_by": "period",
        }

        try:
            async with session.get(url, auth=self._auth(), params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as exc:
            _LOGGER.warning("Failed to fetch today's consumption from Octopus: %s", exc)
            return []

        results: list[dict] = []
        for entry in data.get("results", []):
            interval_start_str = entry.get("interval_start")
            interval_end_str = entry.get("interval_end")
            consumption = entry.get("consumption")
            if not interval_start_str or consumption is None:
                continue
            interval_start = datetime.fromisoformat(interval_start_str)
            if interval_start.tzinfo is None:
                _LOGGER.warning(
                    "Naive datetime in consumption interval_start; assuming UTC."
                )
                interval_start = interval_start.replace(tzinfo=UTC)
            interval_end = (
                datetime.fromisoformat(interval_end_str)
                if interval_end_str
                else interval_start + timedelta(minutes=30)
            )
            if interval_end.tzinfo is None:
                _LOGGER.warning(
                    "Naive datetime in consumption interval_end; assuming UTC."
                )
                interval_end = interval_end.replace(tzinfo=UTC)
            results.append(
                {
                    "interval_start": interval_start.astimezone(UTC),
                    "interval_end": interval_end.astimezone(UTC),
                    "consumption_kwh": float(consumption),
                }
            )
        return results
