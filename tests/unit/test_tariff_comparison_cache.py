"""Unit tests for tariff_comparison.cache.TariffComparisonCache.

Covers: is_fresh (true / stale / wrong year), save+load round-trip,
missing file, corrupt JSON, and atomic write (no .tmp left behind).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from custom_components.battery_charge_calculator.tariff_comparison.cache import (
    TariffComparisonCache,
)

UTC = timezone.utc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_cache(
    path: Path, generated_at: datetime, data_year: str, extra: dict | None = None
) -> None:
    """Write a minimal cache JSON file directly to disk."""
    data = {
        "schema_version": 1,
        "generated_at": generated_at.isoformat(),
        "data_year": data_year,
    }
    if extra:
        data.update(extra)
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests: is_fresh
# ---------------------------------------------------------------------------


class TestIsFresh:
    def test_returns_true_when_cache_is_recent_and_year_matches(self, tmp_path):
        """Cache generated 1 day ago with matching data_year → is_fresh returns True."""
        cache_path = tmp_path / "cache.json"
        generated_at = datetime.now(UTC) - timedelta(days=1)
        _write_cache(cache_path, generated_at, "2025-04")

        cache = TariffComparisonCache(cache_path)
        assert cache.is_fresh(max_age_days=7, data_year="2025-04") is True

    def test_returns_false_when_cache_is_stale(self, tmp_path):
        """Cache generated 8 days ago → is_fresh returns False (max_age=7)."""
        cache_path = tmp_path / "cache.json"
        generated_at = datetime.now(UTC) - timedelta(days=8)
        _write_cache(cache_path, generated_at, "2025-04")

        cache = TariffComparisonCache(cache_path)
        assert cache.is_fresh(max_age_days=7, data_year="2025-04") is False

    def test_returns_false_when_data_year_mismatches(self, tmp_path):
        """Cache with wrong data_year → is_fresh returns False regardless of age."""
        cache_path = tmp_path / "cache.json"
        generated_at = datetime.now(UTC) - timedelta(days=1)
        _write_cache(cache_path, generated_at, "2024-04")  # old year

        cache = TariffComparisonCache(cache_path)
        assert cache.is_fresh(max_age_days=7, data_year="2025-04") is False

    def test_returns_false_when_cache_file_missing(self, tmp_path):
        """No cache file on disk → is_fresh returns False, no exception."""
        cache_path = tmp_path / "does_not_exist.json"
        cache = TariffComparisonCache(cache_path)
        assert cache.is_fresh(max_age_days=7, data_year="2025-04") is False

    def test_exactly_at_max_age_boundary(self, tmp_path):
        """Cache exactly max_age_days old is considered stale (strictly less than required)."""
        cache_path = tmp_path / "cache.json"
        generated_at = datetime.now(UTC) - timedelta(days=7)
        _write_cache(cache_path, generated_at, "2025-04")

        cache = TariffComparisonCache(cache_path)
        # Exactly 7 days old with max_age=7 — must be stale (not fresh)
        assert cache.is_fresh(max_age_days=7, data_year="2025-04") is False


# ---------------------------------------------------------------------------
# Tests: save + load round-trip
# ---------------------------------------------------------------------------


class TestSaveAndLoad:
    def test_save_and_load_roundtrip(self, tmp_path):
        """Write a dict via save(), read it back via load() — values preserved."""
        cache_path = tmp_path / "cache.json"
        cache = TariffComparisonCache(cache_path)

        payload = {
            "schema_version": 1,
            "data_year": "2025-04",
            "consumption": {
                "import": [
                    {"interval_start": "2025-04-01T00:00:00Z", "consumption": 0.3}
                ]
            },
            "numbers": [1, 2, 3],
        }

        cache.save(payload)
        loaded = cache.load()

        assert loaded is not None
        assert loaded["data_year"] == "2025-04"
        assert loaded["numbers"] == [1, 2, 3]
        assert loaded["consumption"]["import"][0]["consumption"] == 0.3

    def test_load_returns_none_when_file_missing(self, tmp_path):
        """load() on a non-existent cache file returns None, raises no exception."""
        cache_path = tmp_path / "no_file_here.json"
        cache = TariffComparisonCache(cache_path)

        result = cache.load()

        assert result is None

    def test_load_returns_none_for_corrupt_json(self, tmp_path):
        """load() on a file with invalid JSON returns None, raises no exception."""
        cache_path = tmp_path / "cache.json"
        cache_path.write_text("{ this is not valid JSON !!!", encoding="utf-8")

        cache = TariffComparisonCache(cache_path)
        result = cache.load()

        assert result is None

    def test_save_creates_file_at_path(self, tmp_path):
        """After save(), the cache file must exist on disk."""
        cache_path = tmp_path / "cache.json"
        cache = TariffComparisonCache(cache_path)

        cache.save({"data_year": "2025-04"})

        assert cache_path.exists()

    def test_save_adds_generated_at(self, tmp_path):
        """save() must inject a 'generated_at' timestamp into the stored JSON."""
        cache_path = tmp_path / "cache.json"
        cache = TariffComparisonCache(cache_path)

        cache.save({"data_year": "2025-04"})
        loaded = cache.load()

        assert loaded is not None
        assert "generated_at" in loaded


# ---------------------------------------------------------------------------
# Tests: atomic write
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_no_tmp_file_left_behind_on_success(self, tmp_path):
        """Atomic write must rename a temp file; no .tmp files should remain on success."""
        cache_path = tmp_path / "cache.json"
        cache = TariffComparisonCache(cache_path)

        cache.save({"data_year": "2025-04", "numbers": list(range(100))})

        leftover_tmp_files = list(tmp_path.glob("*.tmp"))
        assert leftover_tmp_files == [], (
            f"Temp files left behind after successful save: {leftover_tmp_files}"
        )

    def test_target_file_is_valid_json_after_save(self, tmp_path):
        """The file written by save() must be parseable JSON."""
        cache_path = tmp_path / "cache.json"
        cache = TariffComparisonCache(cache_path)

        cache.save({"data_year": "2025-04"})

        raw = cache_path.read_text(encoding="utf-8")
        parsed = json.loads(raw)  # must not raise
        assert isinstance(parsed, dict)

    def test_overwrite_updates_content(self, tmp_path):
        """Calling save() twice with different data → load() returns the latest."""
        cache_path = tmp_path / "cache.json"
        cache = TariffComparisonCache(cache_path)

        cache.save({"data_year": "2024-04"})
        cache.save({"data_year": "2025-04"})

        loaded = cache.load()
        assert loaded is not None
        assert loaded["data_year"] == "2025-04"
