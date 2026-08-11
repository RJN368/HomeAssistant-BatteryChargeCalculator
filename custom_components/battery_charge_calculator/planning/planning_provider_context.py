"""Planning provider execution context model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class PlanningProviderContext:
    """Execution context supplied to provider-backed planning strategy."""

    hass: Any
    config_entry: Any
    session: Any
    time_now: datetime
