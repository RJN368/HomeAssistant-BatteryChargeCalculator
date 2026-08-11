"""Axle state snapshot model."""

from __future__ import annotations

from dataclasses import dataclass

from .axle_planning_adjustment_snapshot import AxlePlanningAdjustmentSnapshot


@dataclass(frozen=True, slots=True)
class AxleStateSnapshot:
    """Typed read model for Axle diagnostics/state consumers."""

    source_status: str
    is_active: bool
    suppression_reason: str | None
    last_transition_reason: str | None
    last_error: str | None
    active_window_start: str | None
    active_window_end: str | None
    cache_age_seconds: float | None
    planning: AxlePlanningAdjustmentSnapshot
