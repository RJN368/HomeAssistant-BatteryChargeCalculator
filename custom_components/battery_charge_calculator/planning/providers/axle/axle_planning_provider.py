"""Axle planning provider contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class AxlePlanningProvider(ABC):
    """Contract for Axle planning constraints per slot."""

    @abstractmethod
    def export_adjustment_kwh(
        self,
        *,
        slot_start: datetime,
        slot_end: datetime,
        inverter_size_kw: float,
    ) -> float:
        """Return mandatory export energy adjustment for a slot."""

    @abstractmethod
    def forced_action(
        self,
        *,
        slot_start: datetime,
        slot_end: datetime,
    ) -> str | None:
        """Return forced dispatch action for a slot, if any."""
