"""Historical solar production fetcher using HA's long-term statistics.

Uses homeassistant.components.recorder.statistics.statistics_during_period
(hourly resolution, retained for years) to get per-hour solar kWh, then
splits each hourly value into two 30-min slots.

Why hourly instead of 5-minute:
  HA only retains 5-min statistics for ~10 days by default.  For a previous
  calendar-month window we need long-term (hourly) statistics which are kept
  indefinitely.

Usage (must be called from the event loop — uses executor internally):
    from .ha_solar_history import fetch_solar_history

    solar_data = await fetch_solar_history(hass, entity_id, period_from, period_to)
    # Returns: dict[date, list[float]]  — 48 values per day (kWh/slot)
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _row_field(row: Any, key: str) -> Any:
    """Return a field from a statistics row (dict or object-style)."""
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _row_start_to_utc_datetime(value: Any) -> datetime | None:
    """Normalize StatisticsRow['start'] into a timezone-aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)

    return None


async def fetch_solar_history(
    hass: HomeAssistant,
    entity_id: str,
    period_from: datetime,
    period_to: datetime,
) -> dict[date, list[float]]:
    """Fetch historical solar kWh from HA long-term statistics.

    Returns a dict mapping each calendar date in [period_from, period_to)
    to a list of 48 float values (kWh per 30-min slot).  Missing hours are
    filled with 0.0 (safe default — GeneticEvaluator treats 0 solar as
    worst-case grid import, never invalid).

    The solar entity must be a cumulative energy sensor (device_class=energy,
    state_class=total or total_increasing) that HA tracks in long-term stats.
    Common examples: sensor.solar_energy_today, sensor.pv_energy_production.

    Raises nothing — all errors are logged and an empty dict is returned so
    the simulation falls back gracefully to zero solar.
    """
    try:
        from homeassistant.components.recorder import get_instance
    except ImportError:
        _LOGGER.warning("HA recorder component not available — solar history disabled")
        return {}

    if not entity_id:
        return {}

    # Extend period_from back 1 hour so we have a prior reading to diff against
    # (needed to compute kWh for the first hour of the window).
    fetch_from = period_from - timedelta(hours=1)

    try:
        stats: dict = await get_instance(hass).async_add_executor_job(
            _fetch_stats,
            hass,
            entity_id,
            fetch_from,
            period_to,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Failed to fetch solar statistics for %s: %s", entity_id, exc)
        return {}

    rows = stats.get(entity_id, [])
    if not rows:
        _LOGGER.info(
            "No long-term statistics found for solar entity %s — "
            "check that the entity has historical data in HA's energy dashboard",
            entity_id,
        )
        return {}

    # Convert cumulative sum → per-hour kWh by differencing consecutive rows.
    # Rows are ordered ascending by start time.
    # StatisticsRow may be dict-like (HA TypedDict) or object-like in tests.
    hourly_kwh: dict[datetime, float] = {}
    prev_sum: float | None = None

    for row in rows:
        raw_sum = _row_field(row, "sum")
        current_sum = float(raw_sum) if raw_sum is not None else None
        if current_sum is None:
            prev_sum = None
            continue
        if prev_sum is not None:
            delta = max(0.0, current_sum - prev_sum)  # clamp to 0 (never negative)
            # row start can be datetime or unix timestamp depending on HA internals
            row_start = _row_start_to_utc_datetime(_row_field(row, "start"))
            if row_start is None:
                prev_sum = current_sum
                continue
            hour_start = row_start.replace(second=0, microsecond=0, minute=0)
            hourly_kwh[hour_start] = delta
        prev_sum = current_sum

    if not hourly_kwh:
        _LOGGER.warning(
            "Solar statistics for %s have no differentiable values "
            "(all null or only one row) — falling back to zero solar",
            entity_id,
        )
        return {}

    _LOGGER.debug(
        "Fetched %d hourly solar values for %s (%s → %s)",
        len(hourly_kwh),
        entity_id,
        period_from.date(),
        period_to.date(),
    )

    # Build per-day 48-slot lists (split each hour into 2 × 30-min halves)
    result: dict[date, list[float]] = {}
    current_day = period_from.date()
    end_day = period_to.date()

    while current_day < end_day:
        slots: list[float] = []
        for hour in range(24):
            hour_dt = datetime(
                current_day.year,
                current_day.month,
                current_day.day,
                hour,
                0,
                tzinfo=timezone.utc,
            )
            kwh_this_hour = hourly_kwh.get(hour_dt, 0.0)
            half = kwh_this_hour / 2.0
            slots.append(half)  # HH:00
            slots.append(half)  # HH:30
        result[current_day] = slots
        current_day += timedelta(days=1)

    return result


def _fetch_stats(
    hass: HomeAssistant,
    entity_id: str,
    period_from: datetime,
    period_to: datetime,
) -> dict:
    """Blocking helper — runs inside recorder's executor thread."""
    from homeassistant.components.recorder.statistics import statistics_during_period

    return statistics_during_period(
        hass,
        period_from,
        period_to,
        {entity_id},
        "hour",
        None,  # units — None means use stored unit
        {"sum"},
    )
