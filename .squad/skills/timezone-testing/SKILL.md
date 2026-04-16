# Skill: Testing Timezone and DST Handling in Scheduling Logic

## Problem
Scheduling logic that aligns events (e.g., charge slots) with tariff periods must handle timezone and DST transitions correctly. Bugs often occur when code assumes UTC or naive datetimes, leading to misalignment during DST changes.

## Pattern
- Use real timezone-aware datetime objects (e.g., Europe/London via zoneinfo or pytz).
- Simulate dates around DST transitions (e.g., last Sunday in March/October for Europe/London).
- Verify both UTC and local time representations of scheduled slots.
- Avoid stubbing timezone utilities to always return UTC in tests that check DST logic.
- Assert that slot start times match expected local tariff periods (e.g., cheap rate starts at 00:30 local time, even after DST change).

## Example (pytest)
```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

def test_slot_alignment_across_dst():
    tz = ZoneInfo("Europe/London")
    # DST start: 2026-03-29 01:00 UTC → 02:00 local
    dt_before = datetime(2026, 3, 29, 0, 30, tzinfo=tz)  # Before DST
    dt_after = datetime(2026, 3, 29, 1, 30, tzinfo=tz)   # After DST
    # ...invoke scheduling logic...
    # assert slot times in local time match expected cheap rate start
```

## Anti-patterns
- Stubbing all timezone utilities to UTC (prevents DST simulation)
- Using only UTC datetimes in tests for local-time scheduling

## When to Apply
- Any scheduling, tariff, or time-based logic that must align with local time or tariff periods
- When bugs are suspected or observed around DST changes

# Timezone & DST Testing Skill

## Purpose
Ensure all time-based scheduling logic is robust to timezone and DST transitions, especially for Europe/London and similar regions.

## Pattern
- Use real timezone objects (zoneinfo or pytz) in tests.
- Simulate both spring forward and fall back DST boundaries.
- Assert that scheduled slots align with local time expectations (e.g., cheap rate start).
- Include negative tests: ensure a 1-hour misalignment causes test failure.
- Patch datetime.now() to simulate local time at DST boundaries.

## Example
See `tests/unit/test_dst_timezone_alignment.py` for a reusable pattern.

## Rationale
- Prevents subtle 1-hour errors during DST changes.
- Ensures user-facing schedules always match local wall clock time.

## When to Apply
- Any scheduling, tariff, or time slot logic that depends on local time.
- When adding new regions or timezones.

---
Last updated: 2026-04-16
