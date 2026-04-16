## Learnings

### 2026-04-16: Timezone/DST Handling Review

- The code uses timezone-aware datetimes for most planning logic (e.g., `datetime.now(tz=self.tz)` in the coordinator, and `current_time` is always advanced in 30-min increments from a timezone-aware base).
- However, the Octopus Agile rate expansion (`_expand_to_30min_slots`) and rate parsing use `datetime.fromisoformat`, which returns naive datetimes if the input string lacks timezone info. This can cause misalignment if the API returns local times without explicit UTC offset, especially around DST transitions.
- The slot matching logic in `_expand_to_30min_slots` compares `current_tod` (minutes since midnight) to rate windows, which is DST-sensitive and can misalign by 1 hour if the base `current` is not in the correct timezone.
- The coordinator's planning loop uses `ceil_dt(datetime.now(), timedelta(minutes=30)).astimezone(self.tz)`, which is correct, but if any slot or rate is naive, the comparison may silently fail or misalign.
- No explicit conversion to local timezone is done after parsing rate times; this is a risk if Octopus API changes or DST rules shift.

**Action:**
- All datetimes parsed from Octopus API should be made timezone-aware (preferably UTC) immediately after parsing, and explicitly converted to local timezone before use in slot matching or planning.
- Add checks to ensure all slot and rate datetimes are always timezone-aware before any comparison.
- Consider logging a warning if any naive datetime is detected in rate or slot objects.

---
- All datetime handling in backend is now timezone-aware and DST-safe (Europe/London for scheduling, UTC for storage).
- Naive datetimes are detected, warnings logged, and converted to UTC.
- Unit tests cover DST transitions and naive datetime detection.

### 2026-04-16: ML app timezone and DST handling review

- All ML app files with time-based logic were reviewed: estimator.py, ml/data_pipeline.py, ml/model_trainer.py, ml/power_calculator.py.
- All datetime operations in the ML app are now timezone-aware and DST-safe:
  - All pandas Series are normalized to UTC, with explicit handling for tz-naive (assumed Europe/London) and DST transitions (see _normalise_series_to_utc in data_pipeline.py).
  - All datetime objects for model training, inference, and feature engineering are either UTC-aware or explicitly localized.
  - No hardcoded offsets or naive datetime usage remain; all conversions are explicit and consistent with the main integration’s Europe/London policy.
- No code changes were required at this time, but the review confirmed that the ML app is robust to DST and timezone issues, matching the main integration’s standards.
