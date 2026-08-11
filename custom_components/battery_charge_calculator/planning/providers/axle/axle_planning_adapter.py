"""Axle planning adapter."""

from __future__ import annotations

from datetime import datetime

from .axle_planning_provider import AxlePlanningProvider


class AxlePlanningAdapter(AxlePlanningProvider):
    """Axle adapter over coordinator Axle helper methods."""

    def __init__(
        self,
        *,
        export_adjustment_fn,
        forced_action_fn,
    ) -> None:
        self._export_adjustment_fn = export_adjustment_fn
        self._forced_action_fn = forced_action_fn

    def export_adjustment_kwh(
        self,
        *,
        slot_start: datetime,
        slot_end: datetime,
        inverter_size_kw: float,
    ) -> float:
        return self._export_adjustment_fn(
            slot_start=slot_start,
            slot_end=slot_end,
            inverter_size_kw=inverter_size_kw,
        )

    def forced_action(
        self,
        *,
        slot_start: datetime,
        slot_end: datetime,
    ) -> str | None:
        return self._forced_action_fn(
            slot_start=slot_start,
            slot_end=slot_end,
        )
