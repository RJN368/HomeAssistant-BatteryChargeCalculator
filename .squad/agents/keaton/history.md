# Keaton — Lead

## Project Context
- Project: HomeAssistant-BatteryChargeCalculator
- Created: 2026-04-09
- User: robert.nash

## Learnings

### 2026-04-10 — ML Power Use Estimation Architecture

**Design decisions made for the AI/ML feature (pending Robert's approval):**

- **Model**: `sklearn.Pipeline(PolynomialFeatures(degree=2), Ridge)` — chosen over tree-based models to stay lightweight on Pi 4. Ridge + poly features captures temp×time interactions without high RAM.
- **Persistence**: joblib pickle at `<config_dir>/battery_charge_calculator_ml_model.pkl`. HA storage API is JSON-only; filesystem is correct for binary sklearn objects. joblib ships with scikit-learn.
- **Training thread safety**: ALL training and HA Recorder queries run in `hass.async_add_executor_job()`. Never block the event loop.
- **Blend pattern**: Physics model is never replaced. `from_temp_and_time()` returns weighted blend `(1-w)*physics + w*ml`. Default w=0.3. If ML not ready, falls back to pure physics identically to current behaviour.
- **Data gate**: Require ≥ 14 days of clean half-hour samples before ML activates. Prevents early-install garbage predictions.
- **Training schedule**: Startup load-or-train + weekly retrain at 03:00 via `async_track_time_interval`.
- **Config default**: `ml_enabled=False`. User must explicitly opt in — avoids surprising behaviour on upgrade.
- **Key unresolved**: EV charging sessions will skew training data. Raised as open question for Robert.
- **Features**: `[outdoor_temp, hour_of_day, day_of_week, day_of_year, is_weekend]` — simple but captures seasonal + behavioural patterns physics misses.
- **Status sensor**: R² score exposed as confidence signal; states cover disabled/insufficient_data/training/ready/error.

### 2026-04-10 — Multi-Source External API Data Strategy

**Robert directed: training data from external APIs, not HA Recorder. Explicit config, no auto-entity-detection.**

- **Consumption source**: GivEnergy REST API (`/v1/inverter/{serial}/energy-flow-data/date-range`) is the primary and correct target — returns total house consumption including solar self-consumption. Octopus API (grid import only) is fallback/cross-validation; it is a partial signal (no self-consumption). Both sources already have credentials in the existing config entry.
- **Temperature source**: Open-Meteo free archive API (`archive-api.open-meteo.com/v1/archive`) requires zero config — uses `hass.config.latitude` / `hass.config.longitude`. HA entity fallback retained as opt-in only.
- **File structure**: `ml/sources/` layer added under the ml/ sub-package. Each source implements a Protocol returning `pd.Series` (UTC DatetimeIndex, 30-min freq, NaN for missing slots).
- **Config constants**: `ML_POWER_SENSOR_ENTITY_ID` and `ML_TEMP_SENSOR_ENTITY_ID` removed. Replaced with `ML_CONSUMPTION_SOURCE` (`"givenergy"` | `"octopus"` | `"both"`) and `ML_TEMP_SOURCE` (`"openmeteo"` | `"ha_entity"`).
- **Fallback surfacing**: All source degradation (fallback to Octopus, fallback to HA entity, imputation) surfaced via `MLModelStatusSensor` attributes — no silent failures. HA persistent notification raised if GivEnergy fails and Octopus partial-signal fallback is used.
- **Config flow**: Dropdown for consumption source; read-only note that credentials are already configured; Open-Meteo described as automatic/free. No new credential fields.
- **Open-Meteo rate limits**: 90-day fetch = 1 API call; 52 retrains/year = 52 calls. Well within free tier (10k/day). No caching layer needed.
- **D-13 Q8 resolved**: auto-entity-detection question closed. New open questions raised: GivEnergy field mapping confirmation, "both" mode feature vs validation-only use of Octopus, HA Recorder temp fallback preference.

### 2026-04-10 — Robert's answers processed; all open D-13 questions closed; D-16 and D-17 added

**Robert's answers applied:**
- Q7 (EV exclusion): binary sensor approach rejected; auto-detection from power data statistical analysis required. Delegated to Hockney as D-17.
- Q8 (GivEnergy timestamps): defensive UTC handling — assume UTC, detect and convert if timezone offset present.
- Q9 ("both" mode): Octopus as additional feature column (#16) in the ML model, not validation-only.
- Q10/Q11 (Open-Meteo rates, HA fallback): confirmed acceptable; no caching layer needed.

**Architect-recommended defaults applied (Robert did not answer Q1–Q6):**
- **D-4 closed**: `hass.config.path("battery_charge_calculator_model.pkl")` — config dir survives HACS updates; `custom_components/` gets wiped.
- **D-6 closed**: `HistGradientBoostingRegressor` primary + `Ridge` cold-start fallback. Native NaN handling is critical for API failure scenarios; Ridge fallback covers the ramp-up period below 500 clean samples.
- **D-7 closed**: Hockney's full 15-feature vector adopted + `octopus_import_kwh` as feature #16 when `ML_CONSUMPTION_SOURCE == "both"`. Circular time encodings prevent midnight/weekend discontinuities.
- **D-8 blend weight closed**: Hockney's 0→1 ramp (`w = min(1.0, (N_clean - 500) / 2000)`). Gradual trust-building is safer than fixed 0.3 for a home energy system.
- **D-9 closed**: Monthly retrain + daily RMSE health check triggers immediate retrain if 7-day RMSE > 1.5× training RMSE. Protects SD card; trigger catches structural changes.
- **D-10 closed**: Hockney's quality-based gate (N_clean ≥ 500 AND temp_range ≥ 5°C). Quality matters more than raw count.

**New decisions added:**
- **D-16**: Optionality and graceful degradation architecture — ML is opt-in (`ML_ENABLED=False`); every failure mode silently falls back to pure physics; no ML failure may crash the integration or leak orphaned entities.
- **D-17**: EV charging auto-detection — statistical detection of sustained high-power blocks from the training data time series; zero user config required; specification delegated to Hockney via inbox.

### 2026-04-10 — D-18 final closes + inference simplification (Robert's answers)

**D-18 open questions closed:**
- **Q1 (meter serial auto-fill UX)**: Auto-populate `OCTOPUS_METER_SERIAL` from Octopus API discovery at config flow render time. Pre-fill editable text field with discovered value; user may override. No "Discover" button — seamless. Any failure falls back to empty manual entry.
- **Q2 (multiple meters)**: When `/v1/electricity-meter-points/{mpan}/meters/` returns multiple meters, select the one with the latest `installation_date`. Missing `installation_date` treated as epoch (loses to any real date).
- **Q3 (export meter v2)**: Deferred confirmed. `OCTOPUS_EXPORT_MPN` stays in `const.py`; no scope change now.

**D-18 inference spec — REVISED: slot_import_means removed:**
- Previous spec: inject per-`slot_index` means of `octopus_import_kwh` from training data as an inference-time proxy. This introduced stale-data risk, circular dependency, and compatibility complexity.
- **New spec**: at inference time, `octopus_import_kwh = NaN` always (for "both" mode). HistGBR handles NaN natively. No stored means; no lookup; no proxy injection.
- `slot_import_means` **removed from `model_metadata`**. Model compatibility guard simplified to single `feature_columns` list comparison. Force retrain only on schema change (mode switch), not on per-inference missing data.

**D-13 remaining questions closed (Robert's answers):**
- **Q2 ("both" mode at inference)**: GivEnergy-only signal path at prediction time. Octopus is training-only. Drives the D-18 inference revision above.
- **Q3 (EV blocks notification)**: HA persistent notification raised when `n_ev_blocks_excluded > 0` during any training cycle. Notification ID includes retrain timestamp; one per retrain.
- **Q4 (GivEnergy timestamps)**: FIX AT SOURCE. `_parse_givenergy_timestamp()` in `GivEnergyHistorySource` silently normalises to UTC. Zero log warnings for timezone conversion. Supersedes the earlier "defensive UTC handling + log warning" directive from D-13 Q8.

**Net result**: "both" mode inference path is now exactly as simple as "givenergy" mode at runtime. The Octopus column exists in the feature schema and is always `NaN` at prediction time; HistGBR's native NaN handling means zero branching code needed.

### 2026-04-10 — ml/model_trainer.py and ml/model_persistence.py implemented

**Files created:**
- `custom_components/battery_charge_calculator/ml/model_trainer.py`
- `custom_components/battery_charge_calculator/ml/model_persistence.py`

**model_trainer.py — key implementation details:**
- `TrainedModel` dataclass: holds fitted estimator, `model_type`, `feature_columns`, `trained_at` (UTC), `n_training_samples`, `training_rmse`, `blend_weight`, `trained_with_octopus_feature`, `slot_residual_std`.
- `FEATURE_COLUMNS` (14 features) and `FEATURE_COLUMNS_WITH_OCTOPUS` (15 features) defined — must stay in sync with `data_pipeline.py`.
- `train_power_model(df)`: validates columns → computes residual target (actual − physics) → selects feature schema → 85/15 train/val split → fits HistGBR (n ≥ 500) or Ridge+SimpleImputer pipeline (n < 500) → reports held-out RMSE → computes blend weight and per-dataset residual std → returns `TrainedModel`.
- `compute_blend_weight(n_clean)`: linear ramp 0.0→1.0 over [500, 2500] using `np.clip` (D-8).
- `predict_correction(model, features)`: selects feature columns in schema order → calls `estimator.predict()` → clamps output to `±(_BLEND_CORRECTION_CAP × training_rmse)` (D-1).
- `check_model_compatibility(model, current_feature_columns)`: simple list equality; returns False on schema mismatch to force retrain on source mode switch (D-18).
- All D-6 hyperparameters defined as module-level private constants with comments.
- No homeassistant imports; pure sklearn / numpy / pandas.

**model_persistence.py — key implementation details:**
- `get_model_path(config_dir)`: returns `{config_dir}/battery_charge_calculator_model.pkl` (D-4).
- `save_model(model, config_dir)`: atomic write via `tempfile.mkstemp` (same dir) → `joblib.dump(compress=3)` → `os.replace()` POSIX rename. Temp file cleaned up on any exception before re-raise.
- `load_model(config_dir)`: returns `None` (no raise) for missing file, corrupt pickle, or wrong type. WARNING logged for unexpected failures; DEBUG for normal missing-file case.
- `model_age_days(model)`: `(now_utc − model.trained_at).total_seconds() / 86400.0`.
- `should_retrain(model, current_rmse_7day)`: returns True if model is None, age > 35 days, or 7-day RMSE > 1.5× training RMSE (D-9). RMSE trigger skipped when `current_rmse_7day is None` (startup path).
- `_RETRAIN_RMSE_TRIGGER` imported from `model_trainer` to avoid duplication.
- No homeassistant imports; pure Python + joblib.
