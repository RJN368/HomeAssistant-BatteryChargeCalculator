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
    ) -> dict:
        """Simulate one day under the given tariff rates.

        Takes 24 hourly temperatures and resamples them to 48 × 30-min slots.
        Builds a fresh GeneticEvaluator for the day, populates it with demand
        estimates from *power_calculator*, and runs the genetic algorithm.

        Solar is set to 0.0 for all slots — historical solar data is not
        available for the simulation window.

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

        for slot_idx in range(48):
            hour = slot_idx // 2
            minute = 30 if slot_idx % 2 else 0
            slot_dt = datetime(
                date_obj.year, date_obj.month, date_obj.day,
                hour, minute, tzinfo=timezone.utc,
            )
            temp = temps_30min[slot_idx]
            demand_kwh = power_calculator.from_temp_and_time(
                current_time=slot_dt, tempdata=temp
            )
            import_rate = rate_map_import.get(slot_dt, 0.0)
            export_rate = rate_map_export.get(slot_dt, 0.0) if rate_map_export else 0.0

            evaluator.add_data(
                start_datetime=slot_dt,
                import_price=import_rate,
                export_price=export_rate,
                demand_in=max(0.0, float(demand_kwh)),
                solar_in=0.0,  # no historical solar data
            )

        timeslots, _ = evaluator.evaluate()

        if not timeslots:
            _LOGGER.debug("GeneticEvaluator returned no timeslots for %s", date_obj)
            return {
                "import_cost_pence": 0.0,
                "export_earnings_pence": 0.0,
                "date": date_obj,
            }

        # Accumulate costs directly from slot.cost (pence)
        # Positive cost = grid import; negative cost = export earnings
        import_cost_pence: float = sum(
            ts.cost for ts in timeslots if ts.cost > 0.0
        )
        export_earnings_pence: float = sum(
            -ts.cost for ts in timeslots if ts.cost < 0.0
        )

        return {
            "import_cost_pence": import_cost_pence,
            "export_earnings_pence": export_earnings_pence,
            "date": date_obj,
        }
