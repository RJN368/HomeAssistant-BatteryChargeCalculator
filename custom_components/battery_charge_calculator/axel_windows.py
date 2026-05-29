"""Axel window normalization and overlap helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

_ADJACENCY_THRESHOLD = timedelta(minutes=1)


@dataclass(slots=True)
class AxelWindow:
    """UTC-aware Axel control window."""

    start: datetime
    end: datetime
    control_intent: str | None = None
    source_updated_at: datetime | None = None


def normalize_windows(raw_windows: Iterable[Any]) -> list[AxelWindow]:
    """Parse, normalize, drop invalid windows, then sort and merge."""
    parsed: list[AxelWindow] = []
    for raw_window in raw_windows:
        window = _parse_window(raw_window)
        if window is None:
            continue
        if window.end <= window.start:
            continue
        parsed.append(window)

    parsed.sort(key=lambda item: item.start)
    return _merge_windows(parsed)


def overlaps_half_open(
    *,
    slot_start: datetime,
    slot_end: datetime,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    """Return True when [slot_start, slot_end) overlaps [window_start, window_end)."""
    slot_start_utc = _to_utc(slot_start)
    slot_end_utc = _to_utc(slot_end)
    window_start_utc = _to_utc(window_start)
    window_end_utc = _to_utc(window_end)
    return window_start_utc < slot_end_utc and window_end_utc > slot_start_utc


def _merge_windows(windows: list[AxelWindow]) -> list[AxelWindow]:
    if not windows:
        return []

    merged: list[AxelWindow] = [windows[0]]
    for current in windows[1:]:
        previous = merged[-1]
        gap = current.start - previous.end
        if current.start <= previous.end or gap <= _ADJACENCY_THRESHOLD:
            previous.end = max(previous.end, current.end)
            if previous.control_intent is None:
                previous.control_intent = current.control_intent
            previous.source_updated_at = _max_datetime(
                previous.source_updated_at, current.source_updated_at
            )
            continue

        merged.append(current)

    return merged


def _parse_window(raw_window: Any) -> AxelWindow | None:
    if isinstance(raw_window, AxelWindow):
        return AxelWindow(
            start=_to_utc(raw_window.start),
            end=_to_utc(raw_window.end),
            control_intent=raw_window.control_intent,
            source_updated_at=_to_optional_utc(raw_window.source_updated_at),
        )

    if hasattr(raw_window, "start_time"):
        start_raw = getattr(raw_window, "start_time", None)
        end_raw = getattr(raw_window, "end_time", None)
        intent = getattr(raw_window, "control_intent", None)
        updated_raw = getattr(raw_window, "source_updated_at", None)
    elif isinstance(raw_window, dict):
        start_raw = raw_window.get("start") or raw_window.get("start_time")
        end_raw = raw_window.get("end") or raw_window.get("end_time")
        intent = raw_window.get("control_intent") or raw_window.get("import_export")
        updated_raw = raw_window.get("source_updated_at") or raw_window.get("updated_at")
    else:
        return None

    start = _parse_datetime(start_raw)
    end = _parse_datetime(end_raw)
    if start is None or end is None:
        return None

    return AxelWindow(
        start=start,
        end=end,
        control_intent=str(intent) if intent is not None else None,
        source_updated_at=_parse_datetime(updated_raw),
    )


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None

    if isinstance(value, datetime):
        return _to_utc(value)

    if isinstance(value, str):
        iso_value = value.replace("Z", "+00:00")
        return _to_utc(datetime.fromisoformat(iso_value))

    return None


def _to_optional_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return _to_utc(value)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _max_datetime(left: datetime | None, right: datetime | None) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)
