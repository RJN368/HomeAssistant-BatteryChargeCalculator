"""Axle event payload model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AxleEvent:
    """Normalized event payload returned by Axle endpoint."""

    start_time: str
    end_time: str | None
    control_intent: str | None
    source_updated_at: str | None
