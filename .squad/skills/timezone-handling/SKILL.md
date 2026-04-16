# Skill: Timezone- and DST-Safe Datetime Handling (Europe/London, UTC)

## Context
For Home Assistant integrations and ML services that operate in the UK, all time-based logic must be robust to DST transitions and timezone ambiguity. This skill documents the reusable pattern for safe datetime handling, as implemented in both the main integration and the ML app.

## Pattern
- **Always use timezone-aware datetimes** for all scheduling, slotting, and rate calculations.
- **Normalize all pandas Series to UTC** using a helper (see `_normalise_series_to_utc`):
  - If the index is tz-naive, localize to Europe/London, then convert to UTC.
  - Remove DST fall-back duplicates by keeping the first occurrence.
  - If localization fails, fallback to UTC and log a debug message.
- **For datetime parsing:**
  - Always parse with timezone info if available.
  - If parsing a naive datetime, assume Europe/London and localize, then convert to UTC.
- **For feature engineering:**
  - All circular encodings (hour, day-of-week, day-of-year) must use UTC.
- **Never use hardcoded offsets** (e.g., +1 hour for BST) or compare naive and aware datetimes.
- **Log a warning** if a naive datetime is detected in any input or intermediate value.

## Example (Python/Pandas)
```python
from zoneinfo import ZoneInfo
import pandas as pd

_LONDON_TZ = ZoneInfo("Europe/London")

def _normalise_series_to_utc(series: pd.Series) -> pd.Series:
    if series is None or len(series) == 0:
        return pd.Series(dtype=float)
    if not isinstance(series.index, pd.DatetimeIndex):
        series = series.copy()
        series.index = pd.to_datetime(series.index)
    idx = series.index
    if idx.tz is None:
        try:
            new_idx = idx.tz_localize(_LONDON_TZ, ambiguous="NaT", nonexistent="NaT").tz_convert("UTC")
        except Exception:
            new_idx = idx.tz_localize("UTC")
        series = series.copy()
        series.index = new_idx
        series = series[series.index.notna()]
    elif str(idx.tz) not in ("UTC", "utc"):
        series = series.copy()
        series.index = idx.tz_convert("UTC")
    series = series[~series.index.duplicated(keep="first")]
    return series
```

## When to Use
- Any time-based logic in Home Assistant custom components or ML services.
- When aligning, resampling, or joining time series data from sensors, APIs, or user input.
- When parsing datetimes from external sources (APIs, CSVs, etc).

## Why
- Prevents subtle bugs around DST transitions (spring forward, fall back).
- Ensures all time comparisons and slotting logic are consistent and safe.
- Aligns with Home Assistant and UK energy industry best practices.

---
_Last updated: 2026-04-16_
