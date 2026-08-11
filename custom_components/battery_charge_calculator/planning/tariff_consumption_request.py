"""Tariff consumption request model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TariffConsumptionRequest:
    """Provider-specific account/request identifiers for consumption fetch."""

    identifiers: dict[str, str]
