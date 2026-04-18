"""TariffSimulator — run a single day's GeneticEvaluator simulation.

Used by TariffComparisonCoordinator for the Approach A background simulation
pipeline (§6.7 of tariff-comparison.md).  Pure Python — no HA imports so that
the class can be safely called from hass.async_add_executor_job().
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..power_calculator import PowerCalulator

_LOGGER = logging.getLogger(__name__)

_FALLBACK_TEMP = 10.0  # °C used when no weather data is available for a slot


class TariffSimulator:
    """Run a single-day GeneticEvaluator simulation for a non-current tariff.

    Designed to be constructed once and reused across many days.  Thread-safe
    (no shared mutable state between calls to simulate_day).
    """

    def simulate_day(
        self,
        date_obj: date,
        hourly_temps: list[float],
        rate_map_import: dict[datetime, float],
        rate_map_export: dict[datetime, float] | None,
        power_calculator: PowerCalulator,
        inverter_size_kw: float,
        inverter_efficiency: float,
        battery_capacity_kwh: float,
        battery_start_kwh: float,
        solar_data_30min: list[float] | None = None,
    ) -> dict:
        """Simulate one day under the given tariff rates.

        Takes 24 hourly temperatures and resamples them to 48 × 30-min slots.
        Builds a fresh GeneticEvaluator for the day, populates it with demand
        estimates from *power_calculator*, and runs the genetic algorithm.

        *solar_data_30min* is an optional list of 48 kWh values (one per
        30-min slot) sourced from HA's long-term recorder statistics via
        :func:`ha_solar_history.fetch_solar_history`.  When provided, each
        slot receives the real historical generation value; when ``None`` or
        shorter than 48 entries, missing slots default to 0.0.

        Returns:
            ``{"import_cost_pence": float, "export_earnings_pence": float,
               "date": date_obj}``

        The GeneticEvaluator sets ``timeslot.cost`` in pence:
        - positive (charge/discharge) = import cost
        - negative (export) = export earnings

        Summing costs directly avoids division-by-zero when rates are 0 p/kWh
        and is more numerically stable than back-calculating kWh.
        """
        # Lazy import to keep module importable without HA deps
        from ..genetic_evaluator import GeneticEvaluator

        # Resample 24 hourly temps → 48 × 30-min temps (duplicate each hour)
        temps_30min: list[float] = []
        for t in hourly_temps:
            temps_30min.append(float(t))  # HH:00
            temps_30min.append(float(t))  # HH:30
        # Pad with fallback if fewer than 24 hours were supplied
        while len(temps_30min) < 48:
            temps_30min.append(_FALLBACK_TEMP)

        evaluator = GeneticEvaluator(
            battery_start=battery_start_kwh,
            standing_charge=0.0,  # standing charges handled separately per §6.3
            inverter_size_kw=inverter_size_kw,
            inverter_efficiency=inverter_efficiency,
            battery_capacity_kwh=battery_capacity_kwh,
        )

        # Diagnostic: log what we got in this day's rate map vs what we expect.
        # rate_map_import is pre-sliced to this day only (48 slots expected).
        slots_in_map = len(rate_map_import)
        nonzero_import = sum(1 for v in rate_map_import.values() if v > 0.0)
        nonzero_export = (
            sum(1 for v in rate_map_export.values() if v > 0.0)
            if rate_map_export
            else 0
        )
        midnight_dt = datetime(
            date_obj.year, date_obj.month, date_obj.day, 0, 0, tzinfo=timezone.utc
        )
        midnight_rate = rate_map_import.get(midnight_dt)
        if slots_in_map < 48 or midnight_rate is None:
            _LOGGER.warning(
                "Incomplete rate data for %s: map has %d/48 slots, "
                "midnight lookup=%s (nonzero import=%d, export=%d). "
                "Rates for this day may be incomplete.",
                date_obj,
                slots_in_map,
                f"{midnight_rate}p" if midnight_rate is not None else "MISSING",
                nonzero_import,
                nonzero_export,
            )
        else:
            _LOGGER.debug(
                "Simulating %s: %d/48 slots, midnight=%.4fp, nonzero import=%d, export=%d",
                date_obj,
                slots_in_map,
                midnight_rate,
                nonzero_import,
                nonzero_export,
            )

        for slot_idx in range(48):
            hour = slot_idx // 2
            minute = 30 if slot_idx % 2 else 0
            slot_dt = datetime(
                date_obj.year,
                date_obj.month,
                date_obj.day,
                hour,
                minute,
                tzinfo=timezone.utc,
            )
            temp = temps_30min[slot_idx]
            demand_kwh = power_calculator.from_temp_and_time(
                current_time=slot_dt, tempdata=temp
            )
            import_rate = rate_map_import.get(slot_dt, 0.0)
            export_rate = rate_map_export.get(slot_dt, 0.0) if rate_map_export else 0.0
            solar_kwh = (
                float(solar_data_30min[slot_idx])
                if solar_data_30min and slot_idx < len(solar_data_30min)
                else 0.0
            )

            evaluator.add_data(
                start_datetime=slot_dt,
                import_price=import_rate,
                export_price=export_rate,
                demand_in=max(0.0, float(demand_kwh)),
                solar_in=solar_kwh,
            )

        nonzero_import = sum(1 for v in rate_map_import.values() if v > 0.0)
        nonzero_export = (
            sum(1 for v in rate_map_export.values() if v > 0.0)
            if rate_map_export
            else 0
        )
        _LOGGER.debug(
            "Simulating %s — import slots with rate: %d/48, export slots with rate: %d/48",
            date_obj,
            nonzero_import,
            nonzero_export,
        )

        timeslots, _ = evaluator.evaluate()

        if not timeslots:
            _LOGGER.warning(
                "GeneticEvaluator returned no timeslots for %s — import_rate_map has %d entries, "
                "nonzero=%d; check timezone alignment between rate map keys and slot_dt",
                date_obj,
                len(rate_map_import),
                nonzero_import,
            )
            return {
                "import_cost_pence": 0.0,
                "export_earnings_pence": 0.0,
                "date": date_obj,
            }

        # Accumulate costs directly from slot.cost (pence)
        # Positive cost = grid import; negative cost = export earnings
        import_cost_pence: float = sum(ts.cost for ts in timeslots if ts.cost > 0.0)
        export_earnings_pence: float = sum(
            -ts.cost for ts in timeslots if ts.cost < 0.0
        )

        _LOGGER.debug(
            "Simulated %s → import=%.2fp export=%.2fp",
            date_obj,
            import_cost_pence,
            export_earnings_pence,
        )

        return {
            "import_cost_pence": import_cost_pence,
            "export_earnings_pence": export_earnings_pence,
            "date": date_obj,
        }
