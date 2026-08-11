"""Axle planning adjustment snapshot model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AxlePlanningAdjustmentSnapshot:
    """Typed planning adjustment diagnostics view."""

    active: bool
    slot_adjustment_kwh_total: float
    required_export_energy_next_24h_kwh: float
