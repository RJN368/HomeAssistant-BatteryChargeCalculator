# Fenster — Python Expert

## Project Context
- Project: HomeAsssitant-BatteryChargeCalculator
- Created: 2026-04-09
- User: robert.nash

## Learnings

### 2026-04-10 — HA Recorder & ML Pipeline Integration Patterns

- **Recorder access:** `get_significant_states` is synchronous blocking I/O. Always wrap via `get_instance(hass).async_add_executor_job(get_significant_states, hass, start, end, entity_ids, ...)`. Import from `homeassistant.components.recorder` (get_instance) and `homeassistant.components.recorder.history` (get_significant_states).
- **significant_changes_only=False** is needed for power sensors to capture all state transitions, not just "significant" ones.
- **HA state values:** `.state` is always a string. States with `.state in ("unavailable", "unknown")` must be filtered before `float()` conversion.
- **Resampling strategy differs by sensor type:** cumulative kWh sensors use `.resample().last().diff()`; instantaneous W sensors use `.resample().mean() * 0.5 / 1000`. Detect via `device_class` attribute.
- **Atomic model save on Linux:** `os.replace(tmp_path, path)` uses POSIX `rename()` which is atomic. No file locking needed for weekly write / read pattern.
- **Async pipeline pattern:** fetch (recorder executor) → build DataFrame (hass executor) → train (hass executor) → save (hass executor). Only `fetch` goes through recorder's executor; the rest use `hass.async_add_executor_job`.
- **RPi model choice:** `GradientBoostingRegressor(n_estimators=100, max_depth=3)` trains in ~5s on 4,320 rows. `Ridge` with `PolynomialFeatures(degree=2)` is a valid fallback at <0.1s.
- **joblib** ships standalone (not via `sklearn.externals`). Import as `import joblib`.
- **Weekly training trigger:** use `async_track_time_interval` with `timedelta(weeks=1)` + `async_create_task` — same pattern as the existing hourly planning timer in `coordinators.py`.
- **ML sensors must be optional:** use `vol.Optional` in config_flow so existing installations without ML configured are not broken.
