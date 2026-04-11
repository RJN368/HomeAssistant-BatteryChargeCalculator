"""Data source layer for the ML training pipeline.

Exports the :class:`HistoricalDataSource` protocol and the three concrete
source implementations used to fetch historical training data:

- :class:`GivEnergyHistorySource` — house consumption from GivEnergy Cloud
- :class:`OctopusHistorySource`   — grid import from Octopus Energy API
- :class:`OpenMeteoHistorySource` — outdoor temperature from Open-Meteo archive

All sources satisfy the :class:`HistoricalDataSource` protocol:
``async fetch(session, start, end) -> pd.Series | None``
returning a UTC 30-min DatetimeIndex Series (float64).
"""

from __future__ import annotations

from .base import HistoricalDataSource
from .givenergy_history import GivEnergyHistorySource
from .octopus_history import OctopusHistorySource
from .openmeteo_history import OpenMeteoHistorySource

__all__ = [
    "HistoricalDataSource",
    "GivEnergyHistorySource",
    "OctopusHistorySource",
    "OpenMeteoHistorySource",
]
