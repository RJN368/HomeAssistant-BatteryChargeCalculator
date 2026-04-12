# Fenster — Python Expert

## Project Context
- Project: HomeAssistant-BatteryChargeCalculator
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

### 2026-04-10 — ml/sources Layer Implementation Patterns

- **Package layout:** `ml/__init__.py` is docstring-only (no imports that could trigger ImportError at HA startup). `ml/sources/__init__.py` re-exports Protocol + 3 concrete classes via `__all__`. No circular imports.
- **Protocol:** `HistoricalDataSource` uses `typing.Protocol` + `@runtime_checkable`. All three sources satisfy it structurally (duck typing). `TYPE_CHECKING` guard used for `aiohttp` / `pd` annotations in `base.py` to avoid runtime import cost.
- **`_normalise_to_utc(ts_input: str | datetime)`:** lives in `givenergy_history.py`. Uses `pd.Timestamp` for flexible parsing. Naive inputs assumed Europe/London (D-18); uses `tz_localize(_LONDON_TZ, ambiguous="infer", nonexistent="shift_forward")` then `tz_convert(UTC)`. Tz-aware inputs use `tz_convert(UTC)` directly.
- **GivEnergy endpoint:** POST `https://api.givenergy.cloud/v1/inverter/{serial}/energy-flow-data/export/` with JSON body `{"start_date", "end_date", "grouping": 2}`. Auth header: `Authorization: Bearer {token}`.
- **GivEnergy chunking:** 30-day windows, each chunk POSTed separately; any `None` from a chunk aborts the whole fetch (fail-fast). After concatenation: dedup DST duplicates (keep first), sort, `resample("30min").mean()`, `ffill(limit=1)`.
- **Octopus meter_serial guard:** `if not self._meter_serial.strip()` → log warning + return `None` immediately. Full pagination via `next` cursor; `_get_page()` helper handles retry logic cleanly.
- **Open-Meteo:** Single GET, `timeout=60`, `timezone=UTC` param. Hourly → 30-min via `resample("30min").interpolate(method="time")`. `pd.to_datetime(times, utc=True)` treats naive Open-Meteo strings as UTC.
- **429 handling pattern (all three sources):** read `Retry-After` header (default 60 s), `await asyncio.sleep(retry_after)`, retry once; on second failure log error + return None.
- **Error handling precedence:** 401/403 → log auth error, return None; 429 → sleep + retry once; other non-ok → log + return None; `aiohttp.ClientError` → log + return None; `asyncio.TimeoutError` → log + return None.
- **Empty vs None:** all sources return empty `pd.Series(dtype="float64")` when credentials are valid but no data exists for the range; `None` only for hard failures.

### 2026-04-10 — "both" Source Mode: Feature Engineering Pattern

- **Multi-source feature engineering:** when `ML_CONSUMPTION_SOURCE == "both"`, GivEnergy `consumption` is the TARGET (y); Octopus `import_kwh` is an extra FEATURE column added to X. This encodes solar generation implicitly: low import relative to consumption signals high solar self-consumption.
- **Feature schema is dynamic:** `octopus_import_kwh` is a conditional extension — not part of the base D-7 feature list. The authoritative feature schema is `model_metadata["feature_columns"]`, serialised alongside the model. Never hardcode a fixed feature list when sources can change.
- **Schema compatibility check at load:** if `trained_with_octopus_feature` in model_metadata doesn't match current source config, invalidate the model and force retrain. Mismatched input shapes cause silent sklearn errors, not loud exceptions.
- **UTC normalisation before join:** GivEnergy timestamps may be Europe/London naive. Always call `_normalise_to_utc()` before any DataFrame join. Use `tz_localize("Europe/London", ambiguous="infer", nonexistent="shift_forward")` then `tz_convert("UTC")`, then drop duplicates from fall-back hour (`~index.duplicated(keep="first")`). Inner join after normalisation is DST-safe.
- **Inference imputation for unknown future values:** when the model was trained with `octopus_import_kwh`, use per-`slot_index` means from training data as the proxy at prediction time. This is more stable than deriving from physics (which would create a circular dependency: ML layer depending on physics estimate to produce its own input). Persist `slot_import_means: dict[int, float]` and `slot_import_global_mean: float` in model metadata.
- **Graceful "both" fallback:** if Octopus fetch fails during a training cycle when source is "both", train on GivEnergy-only (without the extra column), set `consumption_signal_quality = "partial"`, log warning. Do NOT crash. The absence of the feature is handled by the schema mismatch check on the next successful "both" cycle — it triggers retrain.
- **Auto-discovery of meter serial:** `GET /v1/electricity-meter-points/{mpan}/meters/` can pre-populate `OCTOPUS_METER_SERIAL` in the config flow. Use `aiohttp.BasicAuth(api_key, "")`. Catch all exceptions — failure should silently leave the field blank, not block setup. Pick meter with latest `installation_date` if multiple results are returned.

### 2026-04-10 — ml_power_estimator.py Orchestrator Patterns

- **File location:** `ml/ml_power_estimator.py` is the sole HA boundary in the `ml/` sub-package. Only this file imports `homeassistant.*`. All other `ml/` files are pure Python.
- **Coordinator injection pattern:** `MLPowerEstimator.__init__` receives `hass` and `config_entry`. `set_physics_calculator(pc)` is called by coordinator after `PowerCalulator` is constructed — this avoids circular dependency at init time.
- **async_start pattern:** loads model in executor; checks `check_model_compatibility(model, current_feature_cols)` for schema drift (source changed between retrains); if model is `None` or `should_retrain()` → `async_trigger_retrain()` schedules background task. Does not block startup.
- **Training task lifecycle:** `_training_in_progress: bool` guards against concurrent retrains. `async_trigger_retrain()` is a no-op if training is in progress. `async_shutdown()` cancels the pending task and awaits cancellation safely.
- **Session ownership in training:** short-lived `aiohttp.ClientSession` created inside `_run_training_pipeline()` via `async with` context manager. Open-Meteo uses a separate session with 60s timeout (different from the 30s default for other sources). Session is closed automatically on exit.
- **ev_stats separation from build_training_dataframe:** `build_training_dataframe` only returns `df` (no ev_stats). `_compute_ev_stats_sync()` runs `detect_ev_blocks()` as a separate executor step before the full pipeline, on the resampled+aligned raw series. This gives the sensor accurate ev_stats with minimal code duplication (accepted redundancy).
- **Inference features with NaN rolling values:** `temp_delta_1slot`, `temp_delta_24h`, `rolling_mean_6h` are set to `NaN` at inference. HistGBR handles natively; Ridge pipeline uses `SimpleImputer(strategy="mean")`. No per-slot rolling averages stored separately (avoids needing to extend `TrainedModel` dataclass).
- **D-1 blend weight re-computed at inference:** `compute_blend_weight(model.n_training_samples)` is called in `predict()` every time, not cached — this ensures if the model is updated mid-session the weight is always current.
- **D-15 fallback notifications:** GivEnergy→Octopus fallback raises a HA persistent notification (`notification_id = "bcc_partial_consumption_signal"`) as a fire-and-forget `async_create_task`. Does not await.
- **_fetch_temperature signature:** `session` parameter is accepted but unused (Open-Meteo creates its own 60s-timeout session; HA entity path uses recorder). This matches function signature consistency for the caller pattern.
- **`_build_physics_series` executor invocation:** passed as `hass.async_add_executor_job(self._build_physics_series, start_dt, end_dt, temp_series)`. `start` and `end` are part of the signature for API consistency even though the method iterates `temp_series.index`.
