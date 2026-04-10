"""Base protocol for ML historical data sources."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pandas as pd

if TYPE_CHECKING:
    import aiohttp


@runtime_checkable
class HistoricalDataSource(Protocol):
    """Contract all data sources must satisfy.

    Every concrete source must expose a ``source_name`` property and an
    async ``fetch`` method that retrieves historical data for a given date
    range.

    ``fetch()`` returns a ``pd.Series`` with:

    - UTC ``DatetimeIndex`` at ``30min`` frequency
    - ``float64`` dtype  (kWh for consumption, °C for temperature)
    - ``None`` on hard failure (auth error, network unreachable)
    - Empty ``Series`` if no data exists for the requested range

    The caller (``MLDataOrchestrator`` / coordinator) owns the
    ``aiohttp.ClientSession`` lifetime and passes it into ``fetch``.
    Sources **must not** create or close sessions themselves.
    """

    @property
    def source_name(self) -> str:
        """Return a short identifier for this source (e.g. ``"givenergy"``)."""
        ...

    async def fetch(
        self,
        session: "aiohttp.ClientSession",
        start: datetime,
        end: datetime,
    ) -> "pd.Series | None":
        """Fetch historical data for the given UTC date range.

        Args:
            session: Shared ``aiohttp.ClientSession`` owned by the caller.
            start:   UTC-aware start datetime (inclusive).
            end:     UTC-aware end datetime (exclusive).

        Returns:
            ``pd.Series`` (float64, UTC DatetimeIndex, 30-min freq) on
            success; ``None`` on hard failure.
        """
        ...
