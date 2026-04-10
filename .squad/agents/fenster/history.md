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

### 2026-04-10 — External API Client Patterns (ML Training Data Sources)

- **Session ownership:** always pass `aiohttp.ClientSession` into fetch functions (same pattern as `OctopusAgileRatesClient`). The coordinator owns the session lifetime; sources are stateless per-call.
- **GivEnergy Cloud REST:** `Authorization: Bearer {token}` header. Endpoint `GET /inverter/{serial}/energy/data?start_time=YYYY-MM-DD&end_time=YYYY-MM-DD&grouping=2`. Chunk into ≤30-day windows. Response field `consumption` = total house load kWh/slot (the correct ML target). Timezone of returned timestamps is unverified — may need `tz_localize("Europe/London").tz_convert("UTC")` if naive-local.
- **Octopus Consumption API:** same `aiohttp.BasicAuth(api_key, "")` as rate-fetch client. Endpoint requires both MPAN (`OCTOPUS_MPN`) and meter serial (`OCTOPUS_METER_SERIAL` — new const). Paginated via `next` cursor (full URL). 90-day single request is safe; no chunking needed.
- **Open-Meteo Archive:** no auth. Always use `timezone=UTC` (not `timezone=auto`) to avoid DST ambiguity. Returns hourly data — upsample to 30-min via `resample("30min").interpolate(method="time")`. Latitude/longitude from `hass.config` — no user config needed.
- **Uniform error handling:** 429 → read `Retry-After` header, sleep, retry once; 4xx → log + return `None`; 5xx → log + return `None`; network error → log + return `None`. Always use `aiohttp.ClientTimeout(total=30)` (60 for Open-Meteo which can be slow).
- **Uniform return type:** `pd.Series(float64)` with UTC `DatetimeIndex` at `"30min"` freq, name = `"consumption_kwh"` or `"temperature_c"`. `None` = fetch failure; empty Series = no data for range.
- **Base Protocol:** use `typing.Protocol` (not ABC) with `@runtime_checkable`. Methods: `source_name: str` property + `async fetch(session, start: date, end: date) -> pd.Series | None`.
- **Only one new const:** `OCTOPUS_METER_SERIAL = "octopus_meter_serial"`. `manifest.json` needs no changes — aiohttp is HA core.

### 2026-04-10 — "both" Source Mode: Feature Engineering Pattern

- **Multi-source feature engineering:** when `ML_CONSUMPTION_SOURCE == "both"`, GivEnergy `consumption` is the TARGET (y); Octopus `import_kwh` is an extra FEATURE column added to X. This encodes solar generation implicitly: low import relative to consumption signals high solar self-consumption.
- **Feature schema is dynamic:** `octopus_import_kwh` is a conditional extension — not part of the base D-7 feature list. The authoritative feature schema is `model_metadata["feature_columns"]`, serialised alongside the model. Never hardcode a fixed feature list when sources can change.
- **Schema compatibility check at load:** if `trained_with_octopus_feature` in model_metadata doesn't match current source config, invalidate the model and force retrain. Mismatched input shapes cause silent sklearn errors, not loud exceptions.
- **UTC normalisation before join:** GivEnergy timestamps may be Europe/London naive. Always call `_normalise_to_utc()` before any DataFrame join. Use `tz_localize("Europe/London", ambiguous="infer", nonexistent="shift_forward")` then `tz_convert("UTC")`, then drop duplicates from fall-back hour (`~index.duplicated(keep="first")`). Inner join after normalisation is DST-safe.
- **Inference imputation for unknown future values:** when the model was trained with `octopus_import_kwh`, use per-`slot_index` means from training data as the proxy at prediction time. This is more stable than deriving from physics (which would create a circular dependency: ML layer depending on physics estimate to produce its own input). Persist `slot_import_means: dict[int, float]` and `slot_import_global_mean: float` in model metadata.
- **Graceful "both" fallback:** if Octopus fetch fails during a training cycle when source is "both", train on GivEnergy-only (without the extra column), set `consumption_signal_quality = "partial"`, log warning. Do NOT crash. The absence of the feature is handled by the schema mismatch check on the next successful "both" cycle — it triggers retrain.
- **Auto-discovery of meter serial:** `GET /v1/electricity-meter-points/{mpan}/meters/` can pre-populate `OCTOPUS_METER_SERIAL` in the config flow. Use `aiohttp.BasicAuth(api_key, "")`. Catch all exceptions — failure should silently leave the field blank, not block setup. Pick meter with latest `installation_date` if multiple results are returned.
