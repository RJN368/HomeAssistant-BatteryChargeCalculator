"""Provider family metadata used by planning factory composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


ProviderBuilder = Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class ProviderFamily:
    """Configuration for one provider capability family."""

    option_key: str
    default_key: str
    provider_name: str
    builders: dict[str, ProviderBuilder]
