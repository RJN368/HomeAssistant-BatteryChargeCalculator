## Learnings: Timezone/DST Test Coverage (2026-04-16)

- No unit test in tests/unit/test_coordinator.py or test_power_calculator.py directly verifies correct charge slot scheduling across DST transitions or alignment with cheap rate start times in local timezones.
- All test times use fixed UTC datetimes; Europe/London is set as the timezone in mocks, but dt_util.get_time_zone is stubbed to always return UTC, so DST effects are not simulated.
- No test simulates a DST boundary (e.g., last Sunday in March/October) or verifies slot start times (e.g., 23:30 vs 00:30) in local time.
- test_ml_sources.py and test_ml_data_pipeline.py focus on UTC normalization and time encoding, but do not test end-to-end scheduling logic or tariff alignment in local time.
- There is a gap: tests do not cover DST transitions or verify correct alignment of charge slots with local cheap rate periods.

### 2026-04-16
- Added DST-aware unit tests in `test_dst_timezone_alignment.py` to verify charge slot scheduling across DST transitions (Europe/London).
- Tests ensure correct slot activation at local cheap rate start times during both spring forward and fall back transitions, and fail if a 1-hour misalignment occurs.
- Used real timezone objects (zoneinfo/pytz) and local time assertions to catch subtle DST bugs.
