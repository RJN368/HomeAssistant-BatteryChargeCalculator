"""Unit tests for Axel window normalization helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.battery_charge_calculator.axel_windows import (
    AxelWindow,
    normalize_windows,
    overlaps_half_open,
)


def test_normalize_windows_drops_invalid_and_missing_bounds() -> None:
    windows = normalize_windows(
        [
            {"start_time": "2026-05-28T10:00:00Z", "end_time": "2026-05-28T10:30:00Z"},
            {"start_time": "2026-05-28T11:00:00Z"},  # missing end
            {"end_time": "2026-05-28T11:30:00Z"},  # missing start
            {"start_time": "2026-05-28T12:00:00Z", "end_time": "2026-05-28T12:00:00Z"},
            {"start_time": "2026-05-28T12:30:00Z", "end_time": "2026-05-28T12:00:00Z"},
        ]
    )

    assert len(windows) == 1
    assert windows[0].start == datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc)
    assert windows[0].end == datetime(2026, 5, 28, 10, 30, tzinfo=timezone.utc)


def test_normalize_windows_converts_to_utc_sorts_and_merges_adjacent() -> None:
    windows = normalize_windows(
        [
            {
                "start_time": "2026-05-28T10:31:00+00:00",
                "end_time": "2026-05-28T11:00:00+00:00",
                "import_export": "export",
            },
            {
                "start_time": "2026-05-28T11:00:30+00:00",
                "end_time": "2026-05-28T11:30:00+00:00",
                "import_export": "export",
            },
            {
                "start_time": "2026-05-28T10:00:00+01:00",
                "end_time": "2026-05-28T10:31:00+01:00",
                "import_export": "import",
            },
        ]
    )

    assert len(windows) == 2

    # First window is the +01:00 payload normalized to UTC.
    assert windows[0].start == datetime(2026, 5, 28, 9, 0, tzinfo=timezone.utc)
    assert windows[0].end == datetime(2026, 5, 28, 9, 31, tzinfo=timezone.utc)

    # Second and third windows were <= 1 minute apart and are merged.
    assert windows[1].start == datetime(2026, 5, 28, 10, 31, tzinfo=timezone.utc)
    assert windows[1].end == datetime(2026, 5, 28, 11, 30, tzinfo=timezone.utc)


def test_normalize_windows_accepts_axel_window_objects() -> None:
    window = AxelWindow(
        start=datetime(2026, 5, 28, 13, 0, tzinfo=timezone.utc),
        end=datetime(2026, 5, 28, 13, 30, tzinfo=timezone.utc),
    )

    normalized = normalize_windows([window])

    assert len(normalized) == 1
    assert normalized[0].start.tzinfo == timezone.utc
    assert normalized[0].end.tzinfo == timezone.utc


def test_overlap_half_open_boundary_semantics() -> None:
    slot_start = datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc)
    slot_end = slot_start + timedelta(minutes=30)

    # Window ending exactly at slot start: no overlap.
    assert (
        overlaps_half_open(
            slot_start=slot_start,
            slot_end=slot_end,
            window_start=slot_start - timedelta(minutes=30),
            window_end=slot_start,
        )
        is False
    )

    # Window starting exactly at slot end: no overlap.
    assert (
        overlaps_half_open(
            slot_start=slot_start,
            slot_end=slot_end,
            window_start=slot_end,
            window_end=slot_end + timedelta(minutes=10),
        )
        is False
    )

    # Genuine overlap.
    assert (
        overlaps_half_open(
            slot_start=slot_start,
            slot_end=slot_end,
            window_start=slot_start + timedelta(minutes=10),
            window_end=slot_end + timedelta(minutes=10),
        )
        is True
    )
