"""Base class for client-backed planning adapters."""

from __future__ import annotations


class ClientPlanningAdapterBase:
    """Minimal shared holder for injected API/service clients."""

    def __init__(self, client) -> None:
        self._client = client
