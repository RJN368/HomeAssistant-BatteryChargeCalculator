"""JSON disk cache for tariff comparison data.

Uses atomic POSIX rename on write (same pattern as D-4 model persistence) so
the cache is never left in a partially-written state if HA is interrupted.

Cache is stored at:
    {hass.config.path("battery_charge_calculator_tariff_cache.json")}
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

_LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_CACHE_FILENAME = "battery_charge_calculator_tariff_cache.json"


def cache_path(config_dir: str) -> str:
    """Return the absolute path to the cache file."""
    return os.path.join(config_dir, _CACHE_FILENAME)


def read_cache(config_dir: str) -> dict[str, Any] | None:
    """Read and parse the cache file.

    Returns the parsed dict, or None if the file does not exist or is corrupt.
    Must be called inside hass.async_add_executor_job (blocking I/O).
    """
    path = cache_path(config_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("schema_version") != SCHEMA_VERSION:
            _LOGGER.warning(
                "Tariff cache schema version mismatch (got %s, expected %s) — "
                "discarding cache",
                data.get("schema_version"),
                SCHEMA_VERSION,
            )
            return None
        return data
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Failed to read tariff cache from %s: %s", path, exc)
        return None


def write_cache(config_dir: str, cache_data: dict[str, Any]) -> None:
    """Write cache data atomically using a temp-file + rename pattern.

    Must be called inside hass.async_add_executor_job (blocking I/O).
    """
    path = cache_path(config_dir)
    fd, tmp_path = tempfile.mkstemp(dir=config_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cache_data, fh, default=_json_default)
        os.replace(tmp_path, path)
        _LOGGER.debug("Tariff cache written to %s", path)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to write tariff cache: %s", exc)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def is_cache_fresh(cache: dict[str, Any], max_age_days: int) -> bool:
    """Return True if the cache was generated within max_age_days."""
    generated_at_str = cache.get("generated_at")
    if not generated_at_str:
        return False
    try:
        generated_at = datetime.fromisoformat(generated_at_str)
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - generated_at
        return age.days < max_age_days
    except ValueError, TypeError:
        return False


def cache_data_year(cache: dict[str, Any]) -> str | None:
    """Return the 'data_year' key (e.g. '2025-04') stored in the cache."""
    return cache.get("data_year")


def build_cache_payload(
    data_year: str,
    consumption_import: list[dict],
    consumption_export: list[dict],
    tariff_rates: dict[str, dict],
) -> dict[str, Any]:
    """Construct the full cache payload ready for write_cache()."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_year": data_year,
        "consumption": {
            "import": consumption_import,
            "export": consumption_export,
        },
        "tariff_rates": tariff_rates,
    }


def _json_default(obj: Any) -> Any:
    """Fallback JSON serialiser for datetime objects."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class TariffComparisonCache:
    """Thin OO wrapper around the module-level cache functions.

    Provides the interface expected by tests and the coordinator:
        cache = TariffComparisonCache(path)
        cache.load() -> dict | None
        cache.save(data: dict) -> None
        cache.is_fresh(max_age_days, data_year) -> bool
    """

    def __init__(self, path: str | os.PathLike) -> None:
        """Initialise with the path to the JSON cache file."""
        self._path = str(path)
        self._dir = os.path.dirname(self._path) or "."
        self._data: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Synchronous helpers (for use inside executor jobs)
    # ------------------------------------------------------------------

    def load(self) -> dict[str, Any] | None:
        """Read and parse the cache; returns None on missing / corrupt file.

        Must be called from inside hass.async_add_executor_job.
        """
        if not os.path.exists(self._path):
            return None
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("schema_version") != SCHEMA_VERSION:
                _LOGGER.warning(
                    "Tariff cache schema version mismatch (got %s, expected %s) — "
                    "discarding cache",
                    data.get("schema_version"),
                    SCHEMA_VERSION,
                )
                return None
            self._data = data
            return data
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to read tariff cache from %s: %s", self._path, exc)
            return None

    def save(self, data: dict[str, Any]) -> None:
        """Write cache data atomically (temp-file + rename).

        Automatically injects ``schema_version`` and ``generated_at`` if absent.
        Must be called from inside hass.async_add_executor_job.
        """
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        fd, tmp_path = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, default=_json_default)
            os.replace(tmp_path, self._path)
            self._data = payload
            _LOGGER.debug("Tariff cache written to %s", self._path)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Failed to write tariff cache: %s", exc)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def is_fresh(self, max_age_days: int, data_year: str | None = None) -> bool:
        """Return True if the loaded cache is recent and (optionally) year-matches.

        Call load() first; if load() returned None, is_fresh() returns False.
        """
        data = self._data
        if data is None:
            # Try to load on demand
            data = self.load()
        if data is None:
            return False

        # Check data_year match first
        if data_year is not None and data.get("data_year") != data_year:
            return False

        return is_cache_fresh(data, max_age_days)
