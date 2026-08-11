"""Plan computation result model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PlanComputationResult:
    """Computed plan output consumed by the coordinator."""

    timeslots: list
    total_cost: float
    daily_forecast: list[dict]
    slot_adjustment_kwh_total: float
    import_rates: list[dict]
    today_consumption: list[dict]
