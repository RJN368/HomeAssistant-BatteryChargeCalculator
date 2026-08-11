"""Per-slot stage data model for planning pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class SlotStageData:
    """Normalized per-slot data produced during planning pipeline."""

    current_time: datetime
    temperature: float | None
    import_rate: float | None
    export_rate: float | None
    solar_estimate: float
    physics_kwh: float
