"""Planning strategy contract."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from custom_components.battery_charge_calculator.planning.models import (
    PlanningInputs,
    PlanningProviderContext,
)


class PlanningStrategy(Protocol):
    """Strategy contract that orchestrates provider calls for planning."""

    async def collect_inputs(
        self,
        *,
        context: PlanningProviderContext,
    ) -> PlanningInputs:
        """Collect provider-backed inputs required for planning."""

    def axle_constraints(
        self,
        *,
        slot_start: datetime,
        slot_end: datetime,
        inverter_size_kw: float,
    ) -> tuple[float, str | None]:
        """Return (adjustment_kwh, forced_action) for the slot."""
