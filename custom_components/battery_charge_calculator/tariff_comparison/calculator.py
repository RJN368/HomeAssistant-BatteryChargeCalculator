"""Cost calculation logic for the Annual Tariff Comparison feature.

Pure Python — no Home Assistant imports.

See §6 of _docs/tariff-comparison.md for the full specification, formulas,
and Hockney's implementation notes.
"""

from __future__ import annotations

import calendar
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

_LOGGER = logging.getLogger(__name__)

_LONDON_TZ_NAME = "Europe/London"


def calculate_tariff_cost(
    import_slots: list[dict],
    import_rate_map: dict[datetime, float],
    standing_charges: list[dict],
    export_slots: list[dict] | None,
    export_rate_map: dict[datetime, float] | None,
    include_standing_charges: bool = True,
) -> dict[str, Any]:
    """Calculate monthly and annual costs for one tariff against actual consumption.

    Args:
        import_slots: List of dicts with timezone-aware ``interval_start`` and
            ``consumption`` (kWh) keys — half-hourly import meter reads.
        import_rate_map: Pre-built {slot_start (UTC-aware datetime): rate_p_per_kwh}.
        standing_charges: List of dicts with ``valid_from``, ``valid_to``,
            ``value_inc_vat`` (p/day) — from the Octopus standing charges endpoint.
        export_slots: As import_slots but for export; None if export not configured.
        export_rate_map: As import_rate_map but for export; None if not configured.
        include_standing_charges: When False the standing charge term is zeroed
            (Iverson bracket from §6.3).

    Returns:
        A dict with keys:
        - ``monthly``: list of 12 dicts (YYYY-MM, import_cost_gbp, etc.)
        - ``annual``: dict with total import, export, standing charge, net costs
        - ``coverage_pct``: float — % of import slots with a direct rate-map hit
          BEFORE forward-fill (data quality indicator per Hockney §6.5)
        - ``slot_count``: int — total slot count processed
    """
    # Build fast lookup dicts keyed by interval_start
    import_by_ts: dict[datetime, float] = {
        s["interval_start"]: s["consumption"] for s in import_slots
    }
    export_by_ts: dict[datetime, float] = {}
    if export_slots:
        export_by_ts = {s["interval_start"]: s["consumption"] for s in export_slots}

    # Union of all slot timestamps (Hockney §6.2 — must iterate over union)
    all_timestamps = sorted(set(import_by_ts.keys()) | set(export_by_ts.keys()))

    if not all_timestamps:
        _LOGGER.warning("No consumption slots available for tariff cost calculation")
        return _empty_result()

    # Track direct rate-map hits (before any forward-fill)
    direct_hits = 0
    total_import_slots = len(import_by_ts)

    # Per-month accumulators: {YYYY-MM: {import_p, export_p}}
    monthly_import_p: dict[str, float] = defaultdict(float)
    monthly_export_p: dict[str, float] = defaultdict(float)

    last_import_rate: float = 0.0
    last_export_rate: float = 0.0

    for ts in all_timestamps:
        # Ensure timezone-aware UTC (defensive — should already be)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        month_key = ts.strftime("%Y-%m")

        # Import rate lookup
        import_rate = import_rate_map.get(ts)
        if import_rate is not None:
            if ts in import_by_ts:
                direct_hits += 1
            last_import_rate = import_rate
        else:
            import_rate = last_import_rate  # forward-fill

        # Export rate lookup
        export_rate: float = 0.0
        if export_rate_map:
            er = export_rate_map.get(ts)
            if er is not None:
                last_export_rate = er
                export_rate = er
            else:
                export_rate = last_export_rate  # forward-fill

        import_kwh = import_by_ts.get(ts, 0.0)
        export_kwh = export_by_ts.get(ts, 0.0)

        monthly_import_p[month_key] += import_kwh * import_rate
        monthly_export_p[month_key] += export_kwh * export_rate

    # Build standing charge lookup: list of (valid_from, valid_to, p_per_day)
    sc_entries: list[tuple[datetime, datetime | None, float]] = [
        (sc["valid_from"], sc.get("valid_to"), sc["value_inc_vat"])
        for sc in standing_charges
    ]

    # Collect all months in the slot data
    months_sorted = sorted(set(monthly_import_p) | set(monthly_export_p))

    monthly_results: list[dict] = []
    annual_import_gbp = 0.0
    annual_export_gbp = 0.0
    annual_sc_gbp = 0.0

    for month_key in months_sorted:
        year, month = int(month_key[:4]), int(month_key[5:7])
        days_in_month = calendar.monthrange(year, month)[1]

        # Standing charge for this month (§6.3 direct sum formula per Hockney)
        sc_gbp = 0.0
        if include_standing_charges and sc_entries:
            month_start = datetime(year, month, 1, tzinfo=timezone.utc)
            if month == 12:
                month_end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                month_end = datetime(year, month + 1, 1, tzinfo=timezone.utc)

            for sc_from, sc_to, sc_p_per_day in sc_entries:
                if sc_from is None:
                    continue
                # Clamp the validity window to this calendar month
                overlap_start = max(sc_from, month_start)
                overlap_end = min(sc_to, month_end) if sc_to else month_end
                if overlap_start >= overlap_end:
                    continue
                days_active = (overlap_end - overlap_start).days
                sc_gbp += (sc_p_per_day * days_active) / 100.0

        import_gbp = round(monthly_import_p.get(month_key, 0.0) / 100.0, 6)
        export_gbp = round(monthly_export_p.get(month_key, 0.0) / 100.0, 6)
        sc_gbp = round(sc_gbp, 6)
        net_gbp = round(import_gbp - export_gbp + sc_gbp, 6)

        monthly_results.append(
            {
                "month": month_key,
                "import_cost_gbp": round(import_gbp, 2),
                "export_earnings_gbp": round(export_gbp, 2),
                "standing_charge_gbp": round(sc_gbp, 2),
                "net_cost_gbp": round(net_gbp, 2),
            }
        )

        annual_import_gbp += import_gbp
        annual_export_gbp += export_gbp
        annual_sc_gbp += sc_gbp

    coverage_pct = (
        round((direct_hits / total_import_slots) * 100.0, 2)
        if total_import_slots > 0
        else 0.0
    )

    return {
        "monthly": monthly_results,
        "annual": {
            "import_cost_gbp": round(annual_import_gbp, 2),
            "export_earnings_gbp": round(annual_export_gbp, 2),
            "standing_charges_gbp": round(annual_sc_gbp, 2),
            "net_cost_gbp": round(
                annual_import_gbp - annual_export_gbp + annual_sc_gbp, 2
            ),
        },
        "coverage_pct": coverage_pct,
        "slot_count": len(all_timestamps),
    }


def _empty_result() -> dict[str, Any]:
    """Return a zeroed result dict when no slot data is available."""
    return {
        "monthly": [],
        "annual": {
            "import_cost_gbp": 0.0,
            "export_earnings_gbp": 0.0,
            "standing_charges_gbp": 0.0,
            "net_cost_gbp": 0.0,
        },
        "coverage_pct": 0.0,
        "slot_count": 0,
    }
