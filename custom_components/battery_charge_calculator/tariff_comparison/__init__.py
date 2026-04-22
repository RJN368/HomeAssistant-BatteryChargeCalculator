"""TariffComparisonCoordinator — package init.

Exports TariffComparisonCoordinator, a lightweight DataUpdateCoordinator that
orchestrates the fetch-calculate-cache cycle for the Tariff Comparison feature.

Architecture:
- _async_update_data() returns immediately with cached data (or empty dict).
- When the cache is stale a background task (_background_fetch_and_calculate)
  does all the network I/O and CPU work, then calls async_set_updated_data().
- The comparison window is the previous complete calendar month.
- Update interval: configurable, default 7 days.
"""

from __future__ import annotations

import asyncio
import calendar
import copy
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .. import const
from ..power_calculator import PowerCalulator
from .cache import (
    build_cache_payload,
    cache_data_year,
    is_cache_fresh,
    read_cache,
    write_cache,
)
from .calculator import calculate_tariff_cost
from .client import TariffComparisonClient, _build_historical_rate_map
from .ha_solar_history import fetch_solar_history
from .open_meteo_historical import OpenMeteoHistoricalClient

_LOGGER = logging.getLogger(__name__)


def _select_current_tariff_config(tariff_configs: list[dict]) -> dict | None:
    """Return current tariff config when exactly one entry is flagged current.

    Ambiguous config (multiple ``is_current`` entries) returns ``None`` so we
    do not silently pick the wrong tariff from list order.
    """
    current_tariffs = [tc for tc in tariff_configs if tc.get("is_current", False)]
    if not current_tariffs:
        return None
    if len(current_tariffs) > 1:
        _LOGGER.warning(
            "Multiple tariffs are marked is_current; skipping current-tariff selection for this run.",
        )
        return None
    return current_tariffs[0]


def _target_data_year(now: datetime) -> str:
    """Return the data_year key for the rolling 1-month window.

    The window covers the previous complete calendar month.
    Returns 'YYYY-MM' of that month.
    e.g. if today is 2026-04-16, window = 2026-03-01 → 2026-04-01 → '2026-03'
    """
    period_from, _ = _period_bounds(now)
    return period_from.strftime("%Y-%m")


def _period_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Return (period_from, period_to) UTC datetimes for the rolling 1-month window.

    Covers the previous complete calendar month so only settled data is used.
    """
    period_to = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Go back exactly 1 calendar month
    to_month = period_to.month
    to_year = period_to.year
    from_month = to_month - 1
    if from_month <= 0:
        from_month = 12
        from_year = to_year - 1
    else:
        from_year = to_year
    period_from = period_to.replace(year=from_year, month=from_month, day=1)
    return period_from, period_to


class TariffComparisonCoordinator(DataUpdateCoordinator):
    """Coordinator for Monthly Tariff Comparison data.

    Update interval: configurable via TARIFF_COMPARISON_UPDATE_INTERVAL_DAYS
    (default 7 days).  On first load the cache is checked; if fresh the API
    fetch is skipped entirely.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator."""
        interval_days = entry.options.get(
            const.TARIFF_COMPARISON_UPDATE_INTERVAL_DAYS,
            const.DEFAULT_TARIFF_COMPARISON_UPDATE_INTERVAL_DAYS,
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{const.DOMAIN}_tariff_comparison",
            update_interval=timedelta(days=interval_days),
            config_entry=entry,
        )
        self._entry = entry
        self._config_dir: str = hass.config.config_dir
        self._force_refresh = True
        # Active per-tariff simulation background tasks keyed by import tariff code
        self._simulation_tasks: dict[str, asyncio.Task] = {}
        # Background fetch task (only one at a time)
        self._fetch_task: asyncio.Task | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Return cached data immediately; trigger background fetch if stale.

        This method returns as fast as possible so the event loop is never
        blocked.  Heavy I/O and CPU work runs in _background_fetch_and_calculate
        which calls async_set_updated_data() when complete.
        """
        opts = self._entry.options
        tariffs_json: str = opts.get(const.TARIFF_COMPARISON_TARIFFS, "[]")
        try:
            tariff_configs: list[dict] = (
                json.loads(tariffs_json) if tariffs_json else []
            )
        except json.JSONDecodeError as exc:
            raise UpdateFailed(f"Invalid tariff JSON in config: {exc}") from exc

        if not tariff_configs:
            _LOGGER.debug("No tariffs configured — returning empty comparison")
            return {}

        now = datetime.now(timezone.utc)
        current_data_year = _target_data_year(now)
        period_from, period_to = _period_bounds(now)

        max_cache_age = opts.get(
            const.TARIFF_COMPARISON_CACHE_MAX_AGE_DAYS,
            const.DEFAULT_TARIFF_COMPARISON_CACHE_MAX_AGE_DAYS,
        )

        # Load cache (blocking I/O → executor)
        cache = await self.hass.async_add_executor_job(read_cache, self._config_dir)

        cache_valid = (
            not self._force_refresh
            and cache is not None
            and cache_data_year(cache) == current_data_year
            and is_cache_fresh(cache, max_cache_age)
        )

        if cache_valid:
            _LOGGER.debug("Tariff cache is fresh — skipping API fetch")
            result = self._build_result_from_cache(
                cache, tariff_configs, period_from, period_to
            )
            # Schedule simulations for any non-current tariff that has no
            # completed simulation persisted to cache.  Safe to call on
            # every update — _start_simulations skips already-running tasks.
            cached_sims: dict = cache.get("simulation_results") or {}
            non_current_unsimulated = [
                tc
                for tc in tariff_configs
                if not tc.get("is_current", False)
                and (
                    tc.get("import_tariff_code", "") not in cached_sims
                    or cached_sims[tc.get("import_tariff_code", "")].get("status")
                    != "complete"
                )
            ]
            if non_current_unsimulated:
                tariff_rates = _rates_from_cache(cache.get("tariff_rates", {}))
                shared_export_code: str | None = cache.get("export_tariff_code") or None
                self.hass.async_create_task(
                    self._start_simulations(
                        non_current_unsimulated,
                        tariff_rates,
                        period_from,
                        period_to,
                        result,
                        shared_export_code=shared_export_code,
                    )
                )
            return result

        _LOGGER.info(
            "Tariff cache missing or stale — scheduling background fetch "
            "(period: %s → %s)",
            period_from.date(),
            period_to.date(),
        )
        self._force_refresh = False

        # Fire-and-forget: background task does the real work and calls
        # async_set_updated_data() when complete.  We return immediately so
        # the event loop is never blocked by network I/O or CPU work.
        if self._fetch_task is None or self._fetch_task.done():
            self._fetch_task = self.hass.async_create_task(
                self._background_fetch_and_calculate(
                    tariff_configs, period_from, period_to, current_data_year, cache
                )
            )

        # Return whatever we have right now (stale cache or empty)
        if cache is not None:
            try:
                return self._build_result_from_cache(
                    cache, tariff_configs, period_from, period_to
                )
            except Exception:  # noqa: BLE001
                pass
        return {"tariffs": [], "calculating": True}

    async def _background_fetch_and_calculate(
        self,
        tariff_configs: list[dict],
        period_from: datetime,
        period_to: datetime,
        data_year: str,
        existing_cache: dict | None,
    ) -> None:
        """Background task: fetch from API, calculate, update sensor.

        Runs entirely outside _async_update_data so the event loop stays free.
        Calls async_set_updated_data() on success.
        """
        try:
            session = async_get_clientsession(self.hass)
            result = await self._fetch_and_calculate(
                session,
                tariff_configs,
                period_from,
                period_to,
                data_year,
                existing_cache,
            )
            self.async_set_updated_data(result)
        except UpdateFailed as exc:
            _LOGGER.error("Background tariff fetch failed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("Unexpected error in background tariff fetch: %s", exc)

    async def _fetch_and_calculate(
        self,
        session: Any,
        tariff_configs: list[dict],
        period_from: datetime,
        period_to: datetime,
        data_year: str,
        existing_cache: dict | None,
    ) -> dict[str, Any]:
        """Fetch consumption + rates, run calculator, update cache, return result."""
        opts = self._entry.options
        api_key: str = opts.get(const.OCTOPUS_APIKEY, "")
        account_number: str = opts.get(const.OCTOPUS_ACCOUNT_NUMBER, "")
        mpan: str = opts.get(const.OCTOPUS_MPN, "")
        meter_serial: str = opts.get(const.OCTOPUS_METER_SERIAL, "")
        export_mpan: str | None = opts.get(const.OCTOPUS_EXPORT_MPN) or None
        export_serial: str | None = opts.get(const.OCTOPUS_EXPORT_METER_SERIAL) or None

        client = TariffComparisonClient(
            api_key=api_key,
            mpan=mpan,
            meter_serial=meter_serial,
            export_mpan=export_mpan,
            export_meter_serial=export_serial,
        )

        # --- ADJUST PERIOD BASED ON CURRENT TARIFF START DATE ---
        # Find the current tariff (is_current True)
        current_tariff = _select_current_tariff_config(tariff_configs)
        effective_period_from = period_from
        if current_tariff:
            import_code = current_tariff.get("import_tariff_code", "")
            if import_code:
                try:
                    start_date = await client.fetch_import_tariff_start_date(
                        session, import_code, account_number=account_number
                    )
                    if start_date and start_date > period_from:
                        effective_period_from = start_date
                        _LOGGER.info(
                            "Current tariff %s started after period_from (%s > %s), adjusting comparison window.",
                            import_code,
                            start_date.date(),
                            period_from.date(),
                        )
                except Exception as exc:
                    _LOGGER.warning(
                        "Failed to fetch current tariff start date: %s", exc
                    )

        # All subsequent fetches/calculations use effective_period_from
        period_from = effective_period_from

        # ── 0. Resolve the account's current export tariff code (shared) ─
        # All comparison tariffs use the same export tariff — the one actually
        # on the account.  Two resolution strategies in priority order:
        #   A) Account API (requires account_number) — uses OctopusAgileRatesClient
        #   B) MPAN endpoint (no account_number needed) — uses the meter-point
        #      agreements endpoint directly
        shared_export_code: str | None = None
        if export_mpan and export_serial and api_key:
            # Strategy A: account-based (preferred when account_number is known)
            if account_number:
                try:
                    from ..octopus_agile import (
                        OctopusAgileRatesClient,
                        _active_agreement,
                    )

                    agile_client = OctopusAgileRatesClient(api_key, account_number)
                    # Strategy A-1: is_export flag
                    await agile_client._find_current_tariffs(session)  # noqa: SLF001
                    shared_export_code = agile_client.export_tariff_code
                    if shared_export_code:
                        _LOGGER.debug(
                            "Export tariff resolved via account API (is_export flag): %s",
                            shared_export_code,
                        )
                    else:
                        # Strategy A-2: match by export MPAN directly — handles
                        # accounts where is_export flag is missing or unreliable.
                        _LOGGER.debug(
                            "is_export lookup returned no export tariff for account %s "
                            "— trying MPAN match (export MPAN: %s)",
                            account_number,
                            export_mpan,
                        )
                        try:
                            meters = await agile_client._get_electricity_meters(session)  # noqa: SLF001
                            for meter in meters:
                                if meter.get("mpan") == export_mpan:
                                    agreement = _active_agreement(
                                        meter.get("agreements", [])
                                    )
                                    if agreement:
                                        shared_export_code = agreement.get(
                                            "tariff_code"
                                        )
                                        _LOGGER.debug(
                                            "Export tariff resolved via account API "
                                            "(MPAN match %s): %s",
                                            export_mpan,
                                            shared_export_code,
                                        )
                                    break
                        except Exception as inner_exc:  # noqa: BLE001
                            _LOGGER.warning(
                                "Account MPAN-match lookup failed: %s", inner_exc
                            )
                        if not shared_export_code:
                            _LOGGER.warning(
                                "Account API (both is_export and MPAN-match) returned "
                                "no export tariff for account %s, export MPAN %s — "
                                "trying MPAN endpoint fallback",
                                account_number,
                                export_mpan,
                            )
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning(
                        "Account-based export tariff lookup failed — trying MPAN "
                        "endpoint: %s",
                        exc,
                    )
            else:
                _LOGGER.debug(
                    "No account_number configured — skipping Strategy A, "
                    "using MPAN endpoint for export tariff"
                )

            # Strategy B: MPAN endpoint fallback (works without account_number)
            if not shared_export_code:
                try:
                    shared_export_code = await client.fetch_export_tariff_code(session)
                    if shared_export_code:
                        _LOGGER.debug(
                            "Export tariff resolved via MPAN endpoint: %s",
                            shared_export_code,
                        )
                    else:
                        _LOGGER.error(
                            "No active export tariff found for export MPAN %s — "
                            "export earnings will be 0 in all comparisons. "
                            "Check that the export meter has an active agreement "
                            "on the Octopus account.",
                            export_mpan,
                        )
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.error(
                        "MPAN-based export tariff lookup failed for MPAN %s — "
                        "export earnings will be 0. Error: %s",
                        export_mpan,
                        exc,
                    )
        elif export_mpan or export_serial:
            _LOGGER.warning(
                "Export meter partially configured "
                "(export_mpan=%s, export_serial=%s) — skipping export tariff lookup",
                export_mpan,
                export_serial,
            )
        else:
            _LOGGER.debug("No export meter configured — export earnings will be 0")

        # ── 1. Fetch import consumption ──────────────────────────────────
        try:
            import_slots = await client.fetch_consumption(
                session, period_from, period_to, export=False
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Failed to fetch import consumption: %s", exc)
            raise UpdateFailed(f"Import consumption fetch failed: {exc}") from exc

        # ── 2. Fetch export consumption (optional) ───────────────────────
        export_slots: list[dict] | None = None
        export_meter_missing = False
        if export_mpan and export_serial:
            try:
                export_slots = await client.fetch_consumption(
                    session, period_from, period_to, export=True
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Failed to fetch export consumption: %s", exc)
                export_slots = None
        else:
            export_meter_missing = True

        # ── 3. Fetch rates for each tariff ───────────────────────────────
        tariff_rates_cache: dict[str, dict] = {}
        if existing_cache:
            # Parse ISO strings back to datetimes so comparisons in
            # _build_historical_rate_map work regardless of whether this
            # data came straight from the cache (strings) or from a fresh
            # API fetch (datetimes).
            tariff_rates_cache = _rates_from_cache(
                existing_cache.get("tariff_rates", {})
            )

        new_tariff_rates: dict[str, dict] = {}
        errors: list[str] = []

        for tc in tariff_configs:
            import_code: str = tc.get("import_tariff_code", "")
            # Use the shared account export tariff for all comparisons.
            # Per-tariff export_tariff_code in stored JSON is ignored.
            export_code: str | None = shared_export_code
            if not import_code:
                continue

            # Import rates — fetch fresh if not cached OR if cached rates don't
            # cover the start of the current period (e.g. stale cache from a
            # previous fetch that used a different/shorter window).
            cached_import = tariff_rates_cache.get(import_code)
            cached_import_ok = _rates_cover_period(cached_import, period_from)
            if not cached_import_ok:
                if cached_import is not None:
                    _LOGGER.info(
                        "Cached rates for %s don't cover period start %s — re-fetching",
                        import_code,
                        period_from.date(),
                    )
                try:
                    unit_rates = await client.fetch_unit_rates(
                        session, import_code, period_from, period_to
                    )
                    sc_rates = await client.fetch_standing_charges(
                        session, import_code, period_from, period_to
                    )
                    new_tariff_rates[import_code] = {
                        "unit_rates": unit_rates,
                        "standing_charges": sc_rates,
                    }
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning(
                        "Failed to fetch rates for %s: %s", import_code, exc
                    )
                    errors.append(import_code)
            else:
                new_tariff_rates[import_code] = tariff_rates_cache[import_code]

            # Export rates — same coverage check
            cached_export = tariff_rates_cache.get(export_code) if export_code else None
            cached_export_ok = _rates_cover_period(cached_export, period_from)
            if export_code and not cached_export_ok:
                if cached_export is not None:
                    _LOGGER.info(
                        "Cached export rates for %s don't cover period start %s — re-fetching",
                        export_code,
                        period_from.date(),
                    )
                try:
                    exp_unit_rates = await client.fetch_unit_rates(
                        session, export_code, period_from, period_to
                    )
                    new_tariff_rates[export_code] = {
                        "unit_rates": exp_unit_rates,
                        "standing_charges": [],
                    }
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning(
                        "Failed to fetch export rates for %s: %s", export_code, exc
                    )
                    errors.append(export_code)
            elif export_code:
                new_tariff_rates[export_code] = tariff_rates_cache[export_code]

        if len(errors) == len(tariff_configs):
            raise UpdateFailed(f"All tariff rate fetches failed: {errors}")

        # ── 4. Write updated cache ───────────────────────────────────────
        cache_payload = build_cache_payload(
            data_year=data_year,
            consumption_import=_slots_to_cache(import_slots),
            consumption_export=_slots_to_cache(export_slots or []),
            tariff_rates=_rates_to_cache(new_tariff_rates),
            export_tariff_code=shared_export_code,
        )
        await self.hass.async_add_executor_job(
            write_cache, self._config_dir, cache_payload
        )

        # ── 5. Run calculator for each tariff (Phase 1 — naive replay) ──
        # CPU-bound: run in executor so the event loop stays free
        result = await self.hass.async_add_executor_job(
            self._calculate_all,
            tariff_configs,
            import_slots,
            export_slots,
            new_tariff_rates,
            period_from,
            period_to,
            export_meter_missing,
            shared_export_code,
        )

        # ── 6. Schedule background Approach A simulation for non-current tariffs ──
        non_current = [tc for tc in tariff_configs if not tc.get("is_current", False)]
        if non_current:
            self.hass.async_create_task(
                self._start_simulations(
                    non_current,
                    new_tariff_rates,
                    period_from,
                    period_to,
                    result,
                    shared_export_code=shared_export_code,
                )
            )

        return result

    def _build_result_from_cache(
        self,
        cache: dict,
        tariff_configs: list[dict],
        period_from: datetime,
        period_to: datetime,
    ) -> dict[str, Any]:
        """Reconstruct result dict from cached data, overlaying completed simulations."""
        import_slots = _slots_from_cache(cache.get("consumption", {}).get("import", []))
        export_slots_raw = cache.get("consumption", {}).get("export", [])
        export_slots: list[dict] | None = (
            _slots_from_cache(export_slots_raw) if export_slots_raw else None
        )

        tariff_rates = _rates_from_cache(cache.get("tariff_rates", {}))
        # Restore the shared export tariff code that was resolved at fetch time
        shared_export_code: str | None = cache.get("export_tariff_code") or None

        opts = self._entry.options
        export_mpan = opts.get(const.OCTOPUS_EXPORT_MPN) or None
        export_serial = opts.get(const.OCTOPUS_EXPORT_METER_SERIAL) or None
        export_meter_missing = not (export_mpan and export_serial)

        result = self._calculate_all(
            tariff_configs,
            import_slots,
            export_slots,
            tariff_rates,
            period_from,
            period_to,
            export_meter_missing,
            shared_export_code,
        )

        # Overlay any completed simulation results that were persisted to cache.
        # Validates data_year matches so stale simulations from a previous month
        # are not shown against fresh consumption data.
        sim_results: dict = cache.get("simulation_results", {})
        cache_data_yr: str = cache.get("data_year", "")
        current_data_yr: str = _target_data_year(datetime.now(timezone.utc))
        for tariff_entry in result.get("tariffs", []):
            code = tariff_entry.get("import_tariff_code", "")
            sim = sim_results.get(code)
            if (
                sim
                and sim.get("status") == "complete"
                and sim.get("data_year", "") == cache_data_yr == current_data_yr
            ):
                tariff_entry["comparison_method"] = "simulation"
                tariff_entry["simulation_progress_pct"] = 100.0
                tariff_entry["data_quality_notes"] = []
                tariff_entry["monthly"] = sim["monthly"]

        return result

    def _calculate_all(
        self,
        tariff_configs: list[dict],
        import_slots: list[dict],
        export_slots: list[dict] | None,
        tariff_rates: dict[str, dict],
        period_from: datetime,
        period_to: datetime,
        export_meter_missing: bool,
        shared_export_code: str | None = None,
    ) -> dict[str, Any]:
        """Run the cost calculator for each configured tariff and assemble result."""
        client_helper = TariffComparisonClient("", "", "")
        now = datetime.now(timezone.utc)
        tariff_results: list[dict] = []
        any_low_coverage = False

        for tc in tariff_configs:
            import_code: str = tc.get("import_tariff_code", "")
            # All tariffs share the same export tariff (the account's real one).
            export_code: str | None = shared_export_code
            include_sc: bool = tc.get("include_standing_charges", True)
            is_current: bool = tc.get("is_current", False)

            if not import_code or import_code not in tariff_rates:
                _LOGGER.warning("Skipping tariff %s — no rate data", import_code)
                continue

            import_raw = tariff_rates[import_code].get("unit_rates", [])
            sc_raw = tariff_rates[import_code].get("standing_charges", [])
            import_rate_map = client_helper.build_rate_map(
                import_raw, period_from, period_to
            )

            export_rate_map: dict[datetime, float] | None = None
            if export_code and export_code in tariff_rates:
                exp_raw = tariff_rates[export_code].get("unit_rates", [])
                export_rate_map = client_helper.build_rate_map(
                    exp_raw, period_from, period_to
                )

            calc_result = calculate_tariff_cost(
                import_slots=import_slots,
                import_rate_map=import_rate_map,
                standing_charges=sc_raw,
                export_slots=export_slots,
                export_rate_map=export_rate_map,
                include_standing_charges=include_sc,
                period_to=period_to,
            )

            coverage = calc_result["coverage_pct"]
            if coverage < 95.0:
                any_low_coverage = True

            comparison_method = "real_meter_reads" if is_current else "naive_replay"
            data_quality_notes: list[str] = []
            if not is_current:
                data_quality_notes.append(
                    "Non-current tariff: costs based on current-tariff-optimised "
                    "meter reads (naive replay). Battery schedule would differ on "
                    "this tariff. Results indicative only."
                )

            tariff_entry = {
                "name": tc.get("name", import_code),
                "import_tariff_code": import_code,
                "export_tariff_code": export_code,
                "is_current": is_current,
                "include_standing_charges": include_sc,
                "comparison_method": comparison_method,
                "simulation_progress_pct": 0.0,
                "data_quality_notes": data_quality_notes,
                "coverage_pct": coverage,
                "monthly": calc_result["monthly"],
            }
            tariff_results.append(tariff_entry)

        return {
            "generated_at": now.isoformat(),
            "data_period": {
                "from": period_from.date().isoformat(),
                "to": period_to.date().isoformat(),
            },
            "coverage_warning": any_low_coverage,
            "tariffs": tariff_results,
            "export_configured": export_slots is not None,
            "export_meter_serial_missing": export_meter_missing,
        }

    async def async_refresh_now(self) -> None:
        """Force an immediate full refresh, bypassing the cache.

        Called by the ``battery_charge_calculator.refresh_tariff_comparison``
        service handler.
        """
        self._force_refresh = True
        # Cancel any running background tasks before re-fetch
        if self._fetch_task and not self._fetch_task.done():
            self._fetch_task.cancel()
            self._fetch_task = None
        for task in list(self._simulation_tasks.values()):
            if not task.done():
                task.cancel()
        self._simulation_tasks.clear()
        await self.async_refresh()

    async def _start_simulations(
        self,
        tariff_configs: list[dict],
        tariff_rates: dict[str, dict],
        period_from: datetime,
        period_to: datetime,
        current_result: dict[str, Any],  # noqa: ARG002 — kept for API compat
        shared_export_code: str | None = None,
    ) -> None:
        """Launch background simulation tasks for all non-current tariffs."""
        for tc in tariff_configs:
            code = tc.get("import_tariff_code", "")
            if not code:
                continue
            if (
                code in self._simulation_tasks
                and not self._simulation_tasks[code].done()
            ):
                continue
            task = self.hass.async_create_task(
                self._run_tariff_simulation(
                    tc,
                    tariff_rates,
                    period_from,
                    period_to,
                    shared_export_code=shared_export_code,
                )
            )
            self._simulation_tasks[code] = task

    def _push_simulation_progress(
        self,
        import_code: str,
        comparison_method: str,
        simulation_progress_pct: float,
        data_quality_notes: list[str],
        monthly: list[dict] | None = None,
    ) -> None:
        """Update the live coordinator data for one tariff and notify listeners.

        Reads self.data live so concurrent simulations never overwrite each
        other's progress — each only touches its own tariff entry.
        """
        if not self.data:
            return
        live = copy.deepcopy(self.data)
        _update_tariff_entry(
            live,
            import_code,
            comparison_method=comparison_method,
            simulation_progress_pct=simulation_progress_pct,
            data_quality_notes=data_quality_notes,
            monthly=monthly,
        )
        self.async_set_updated_data(live)

    async def _run_tariff_simulation(
        self,
        tariff_config: dict,
        tariff_rates: dict[str, dict],
        period_from: datetime,
        period_to: datetime,
        shared_export_code: str | None = None,
    ) -> None:
        """Background coroutine: run full Approach A GeneticEvaluator simulation.

        Iterates day-by-day through the comparison window.  Calls
        hass.async_add_executor_job() for all CPU-bound work (D-5 compliance).
        Updates sensor progress via _push_simulation_progress() so concurrent
        simulations never overwrite each other's state.
        """
        from ..genetic_evaluator import GeneticEvaluator
        from .simulator import TariffSimulator

        import_code: str = tariff_config.get("import_tariff_code", "")
        # Use the shared account export tariff — per-tariff export_tariff_code
        # is never stored in the config, so tariff_config.get() would always
        # return None.  The coordinator resolves it once at fetch time.
        export_code: str | None = shared_export_code
        include_sc: bool = tariff_config.get("include_standing_charges", True)

        _LOGGER.info("Starting Approach A simulation for tariff: %s", import_code)

        # Build rate maps
        if import_code not in tariff_rates:
            _LOGGER.warning("No rate data for %s — cannot simulate", import_code)
            return

        import_raw = tariff_rates[import_code].get("unit_rates", [])
        sc_raw = tariff_rates[import_code].get("standing_charges", [])

        # Diagnostic: warn if import_raw appears to be missing early slots (stale cache).
        # For a 31-day Agile period we expect ~1489 entries (seed + 48 slots/day).
        # If the first entry starts after period_from the cache predates the seed fix —
        # the user must delete the tariff cache file and let the coordinator re-fetch.
        if import_raw:
            first_vf = import_raw[0].get("valid_from")
            if (
                first_vf
                and hasattr(first_vf, "date")
                and first_vf.date() > period_from.date()
            ):
                _LOGGER.warning(
                    "Rate data for %s starts on %s but period starts %s — "
                    "cached rates are missing early slots.  Delete the tariff cache "
                    "file and restart HA to force a fresh fetch.",
                    import_code,
                    first_vf.date(),
                    period_from.date(),
                )

        import_rate_map = _build_historical_rate_map(import_raw, period_from, period_to)

        export_rate_map: dict[datetime, float] | None = None
        if export_code and export_code in tariff_rates:
            exp_raw = tariff_rates[export_code].get("unit_rates", [])
            export_rate_map = _build_historical_rate_map(
                exp_raw, period_from, period_to
            )

        # Build PowerCalulator from entry config
        opts = self._entry.options
        power_calculator = _build_power_calculator(opts)
        inverter_size_kw: float = opts.get(
            const.INVERTER_SIZE_KW, const.DEFAULT_INVERTER_SIZE_KW
        )
        inverter_efficiency: float = opts.get(
            const.INVERTER_EFFICIENCY, const.DEFAULT_INVERTER_EFFICIENCY
        )
        battery_capacity_kwh: float = opts.get(
            const.BATTERY_CAPACITY_KWH, const.DEFAULT_BATTERY_CAPACITY_KWH
        )
        battery_start_kwh: float = (
            battery_capacity_kwh * 0.5
        )  # assume 50% SOC at day start

        # Fetch Open-Meteo historical temperatures (one fetch, shared across tariffs)
        session = async_get_clientsession(self.hass)
        lat = self.hass.config.latitude
        lon = self.hass.config.longitude
        om_client = OpenMeteoHistoricalClient(lat, lon)
        weather_data: dict[date, list[float]] = {}
        try:
            weather_data = await om_client.fetch_temperatures(
                session, period_from.date(), period_to.date()
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "Open-Meteo fetch failed for simulation — using 10°C: %s", exc
            )

        # Fetch historical solar data from HA recorder (if entity is configured).
        # Returns dict[date, list[float]] — 48 half-hourly kWh values per day.
        # Falls back to empty dict (→ 0.0 solar per slot) on any error.
        solar_entity_id: str = self._entry.options.get(const.SOLAR_ENERGY_ENTITY, "")
        solar_data: dict[date, list[float]] = {}
        if solar_entity_id:
            try:
                solar_data = await fetch_solar_history(
                    self.hass, solar_entity_id, period_from, period_to
                )
                solar_days = len(solar_data)
                _LOGGER.info(
                    "Fetched historical solar data for %s: %d days of 48-slot generation",
                    solar_entity_id,
                    solar_days,
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "Solar history fetch failed for %s — running simulation without solar: %s",
                    solar_entity_id,
                    exc,
                )
        else:
            _LOGGER.debug(
                "No solar entity configured — simulation will use zero solar for all slots"
            )

        # Build day range
        day_range: list[date] = []
        current_day = period_from.date()
        end_day = period_to.date()
        while current_day < end_day:
            day_range.append(current_day)
            current_day += timedelta(days=1)

        total_days = len(day_range)
        if total_days == 0:
            return

        # Pre-slice the full-month rate maps into per-day dicts BEFORE the executor
        # loop starts.  Each executor thread receives its own isolated copy of only
        # its day's 48 slots — no shared mutable state between the event loop and
        # any running executor thread.  Slicing by `.date()` is safe for UTC keys:
        # datetime(2026, 3, 1, 0, 0, tzinfo=utc).date() == date(2026, 3, 1).
        per_day_import: dict[date, dict[datetime, float]] = {}
        per_day_export: dict[date, dict[datetime, float] | None] = {}
        for day_obj in day_range:
            per_day_import[day_obj] = {
                k: v for k, v in import_rate_map.items() if k.date() == day_obj
            }
            if export_rate_map is not None:
                per_day_export[day_obj] = {
                    k: v for k, v in export_rate_map.items() if k.date() == day_obj
                }
            else:
                per_day_export[day_obj] = None

        simulator = TariffSimulator()
        monthly_import_pence: dict[str, float] = defaultdict(float)
        monthly_export_pence: dict[str, float] = defaultdict(float)

        # Signal that this tariff is now being simulated
        self._push_simulation_progress(
            import_code,
            comparison_method="simulation_in_progress",
            simulation_progress_pct=0.0,
            data_quality_notes=[
                "Simulation starting. Currently showing naive replay data. "
                "Values will update as simulation progresses."
            ],
        )

        for day_idx, day_obj in enumerate(day_range):
            hourly_temps = weather_data.get(day_obj, [10.0] * 24)
            solar_30min = solar_data.get(day_obj)  # None → simulate_day uses 0.0

            try:
                day_result = await self.hass.async_add_executor_job(
                    simulator.simulate_day,
                    day_obj,
                    hourly_temps,
                    per_day_import[day_obj],
                    per_day_export[day_obj],
                    power_calculator,
                    inverter_size_kw,
                    inverter_efficiency,
                    battery_capacity_kwh,
                    battery_start_kwh,
                    solar_30min,
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "Simulation day %s failed — skipping: %s",
                    day_obj,
                    exc,
                    exc_info=True,
                )
                continue

            month_key = day_obj.strftime("%Y-%m")
            monthly_import_pence[month_key] += day_result["import_cost_pence"]
            monthly_export_pence[month_key] += day_result["export_earnings_pence"]

            # Report progress every 5 days and on the final day
            days_done = day_idx + 1
            progress_interval = max(1, total_days // 20)  # ~5% steps
            if days_done % progress_interval == 0 or days_done == total_days:
                progress_pct = round(days_done / total_days * 100.0, 1)
                self._push_simulation_progress(
                    import_code,
                    comparison_method="simulation_in_progress",
                    simulation_progress_pct=progress_pct,
                    data_quality_notes=[f"Simulation {progress_pct:.0f}% complete."],
                )

        # Simulation complete
        final_monthly = _build_simulation_monthly(
            monthly_import_pence, monthly_export_pence, sc_raw, include_sc
        )

        self._push_simulation_progress(
            import_code,
            comparison_method="simulation",
            simulation_progress_pct=100.0,
            data_quality_notes=[],
            monthly=final_monthly,
        )

        # Persist simulation results to cache
        await self._persist_simulation_result(import_code, final_monthly)
        _LOGGER.info("Approach A simulation complete for %s", import_code)

    async def _persist_simulation_result(
        self,
        import_code: str,
        monthly: list[dict],
    ) -> None:
        """Append completed simulation results to the on-disk cache."""
        cache = await self.hass.async_add_executor_job(read_cache, self._config_dir)
        if cache is None:
            return
        simulation_results = cache.setdefault("simulation_results", {})
        simulation_results[import_code] = {
            "status": "complete",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "data_year": cache.get("data_year", ""),
            "monthly": monthly,
        }
        await self.hass.async_add_executor_job(write_cache, self._config_dir, cache)


# ──────────────────────────── module-level helpers ────────────────────────────


def _update_tariff_entry(
    result: dict[str, Any],
    import_code: str,
    comparison_method: str,
    simulation_progress_pct: float,
    data_quality_notes: list[str],
    monthly: list[dict] | None = None,
) -> None:
    """Mutate the matching tariff entry in *result* in-place."""
    for entry in result.get("tariffs", []):
        if entry.get("import_tariff_code") == import_code:
            entry["comparison_method"] = comparison_method
            entry["simulation_progress_pct"] = simulation_progress_pct
            entry["data_quality_notes"] = data_quality_notes
            if monthly is not None:
                entry["monthly"] = monthly
            break


def _build_simulation_monthly(
    monthly_import_pence: dict[str, float],
    monthly_export_pence: dict[str, float],
    standing_charges: list[dict],
    include_standing_charges: bool,
) -> list[dict]:
    """Convert accumulated per-month pence totals into the monthly breakdown format."""
    all_months = sorted(set(monthly_import_pence) | set(monthly_export_pence))
    results: list[dict] = []
    for month_key in all_months:
        year, month = int(month_key[:4]), int(month_key[5:7])
        import_gbp = round(monthly_import_pence.get(month_key, 0.0) / 100.0, 2)
        export_gbp = round(monthly_export_pence.get(month_key, 0.0) / 100.0, 2)

        sc_gbp = 0.0
        if include_standing_charges and standing_charges:
            month_start = datetime(year, month, 1, tzinfo=timezone.utc)
            month_end = (
                datetime(year + 1, 1, 1, tzinfo=timezone.utc)
                if month == 12
                else datetime(year, month + 1, 1, tzinfo=timezone.utc)
            )
            for sc in standing_charges:
                sc_from = sc.get("valid_from")
                sc_to = sc.get("valid_to")
                if sc_from is None:
                    continue
                overlap_start = max(sc_from, month_start)
                overlap_end = min(sc_to, month_end) if sc_to else month_end
                if overlap_start >= overlap_end:
                    continue
                sc_gbp += (
                    sc["value_inc_vat"] * (overlap_end - overlap_start).days
                ) / 100.0
            sc_gbp = round(sc_gbp, 2)

        net_gbp = round(import_gbp - export_gbp + sc_gbp, 2)
        results.append(
            {
                "month": month_key,
                "import_cost_gbp": import_gbp,
                "export_earnings_gbp": export_gbp,
                "standing_charge_gbp": sc_gbp,
                "net_cost_gbp": net_gbp,
            }
        )
    return results


def _rates_cover_period(rates_data: dict | None, period_from: datetime) -> bool:
    """Return True if the cached rates contain a rate that covers period_from.

    Checks whether the first unit_rate entry's valid_from is at or before
    period_from — i.e. the rates actually start at the beginning of the window.
    Returns False if rates_data is None, empty, or starts after period_from.
    """
    if not rates_data:
        return False
    unit_rates = rates_data.get("unit_rates", [])
    if not unit_rates:
        return False
    first = unit_rates[0]
    vf = first.get("valid_from")
    if vf is None:
        return False
    # vf may be a datetime (fresh fetch) or already parsed from cache
    if isinstance(vf, datetime):
        return vf <= period_from
    return False


def _build_power_calculator(opts: dict) -> PowerCalulator:
    """Construct a PowerCalulator from a config entry options dict."""
    heating_type = opts.get(const.HEATING_TYPE, const.DEFAULT_HEATING_TYPE)
    cop = float(opts.get(const.HEATING_COP, const.DEFAULT_HEATING_COP))
    heat_loss_val = opts.get(const.HEATING_HEAT_LOSS, const.DEFAULT_HEATING_HEAT_LOSS)
    heat_loss = float(heat_loss_val) if heat_loss_val else None
    indoor_temp = float(
        opts.get(const.HEATING_INDOOR_TEMP, const.DEFAULT_HEATING_INDOOR_TEMP)
    )
    flow_temp = float(
        opts.get(const.HEATING_FLOW_TEMP, const.DEFAULT_HEATING_FLOW_TEMP)
    )
    base_load = opts.get(const.BASE_LOAD_KWH_30MIN, const.DEFAULT_BASE_LOAD_KWH_30MIN)

    known_points_raw = opts.get(
        const.HEATING_KNOWN_POINTS, const.DEFAULT_HEATING_KNOWN_POINTS
    )
    known_points = None
    if known_points_raw and isinstance(known_points_raw, str):
        try:
            known_points = json.loads(known_points_raw)
        except json.JSONDecodeError, ValueError:
            known_points = None

    return PowerCalulator(
        heating_type=heating_type,
        cop=cop,
        heat_loss=heat_loss,
        indoor_temp=indoor_temp,
        heating_flow_temp=flow_temp,
        known_points=known_points,
        base_load_kwh_30min=float(base_load) if base_load else None,
    )


# ──────────────────────────── cache serialisation helpers ────────────────────


def _slots_to_cache(slots: list[dict]) -> list[dict]:
    """Convert slot dicts to JSON-serialisable form (datetimes → ISO strings)."""
    return [
        {
            "interval_start": s["interval_start"].isoformat()
            if isinstance(s["interval_start"], datetime)
            else s["interval_start"],
            "consumption": s["consumption"],
        }
        for s in slots
    ]


def _slots_from_cache(raw: list[dict]) -> list[dict]:
    """Parse cached slot dicts back to timezone-aware datetimes."""
    result = []
    for s in raw:
        ts = s["interval_start"]
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = ts
        result.append({"interval_start": dt, "consumption": float(s["consumption"])})
    return result


def _rates_to_cache(rates: dict[str, dict]) -> dict[str, dict]:
    """Serialise rate dicts (datetime → ISO string for JSON storage)."""
    out: dict[str, dict] = {}
    for code, data in rates.items():
        out[code] = {
            "unit_rates": [
                {
                    "valid_from": _dt_to_str(r["valid_from"]),
                    "valid_to": _dt_to_str(r.get("valid_to")),
                    "value_inc_vat": r["value_inc_vat"],
                }
                for r in data.get("unit_rates", [])
            ],
            "standing_charges": [
                {
                    "valid_from": _dt_to_str(r["valid_from"]),
                    "valid_to": _dt_to_str(r.get("valid_to")),
                    "value_inc_vat": r["value_inc_vat"],
                }
                for r in data.get("standing_charges", [])
            ],
        }
    return out


def _rates_from_cache(raw: dict[str, dict]) -> dict[str, dict]:
    """Parse cached rate dicts back to timezone-aware datetimes."""
    out: dict[str, dict] = {}
    for code, data in raw.items():
        out[code] = {
            "unit_rates": [
                {
                    "valid_from": _str_to_dt(r["valid_from"]),
                    "valid_to": _str_to_dt(r.get("valid_to")),
                    "value_inc_vat": float(r["value_inc_vat"]),
                }
                for r in data.get("unit_rates", [])
            ],
            "standing_charges": [
                {
                    "valid_from": _str_to_dt(r["valid_from"]),
                    "valid_to": _str_to_dt(r.get("valid_to")),
                    "value_inc_vat": float(r["value_inc_vat"]),
                }
                for r in data.get("standing_charges", [])
            ],
        }
    return out


def _dt_to_str(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat() if isinstance(dt, datetime) else str(dt)


def _str_to_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
