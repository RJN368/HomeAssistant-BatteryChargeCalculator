# Squad Decisions

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction

---

## ML Power Estimation Feature
*Added: 2026-04-10 — sources: Keaton (architecture), Hockney (model selection), Fenster (implementation)*

### D-1: Physics model is the foundation — ML adds an additive residual correction

$$\hat{y}(T, t) = \hat{y}_{physics}(T, t) + w_{ML} \cdot \hat{\delta}_{ML}(\mathbf{x})$$

- `from_temp_and_time()` behaviour is **unchanged** when ML is not ready or disabled
- `w_ML = 0` at cold start; degrades gracefully to pure physics
- ML correction clamped to ±2×RMSE_train to prevent wild extrapolation
- *Agreed by: Keaton, Hockney*

### D-2: Scope of changes to existing files

| File | Change |
|---|---|
| `power_calculator.py` | Extract `_physics_estimate()`; add `ml_model`/`predict_with_ml()` hook |
| `coordinators.py` | Instantiate ML estimator; schedule retrain; inject into `PowerCalculator` |
| `const.py` | Add ML config constants (see D-8) |
| `manifest.json` | Add `scikit-learn` to requirements |
| `config_flow.py` | Add `step_ml_settings` / `step_ml_sensors` options step (opt-in) |
| `sensors/__init__.py` | Export `MLModelStatusSensor` |
| `sensor.py` | Register `MLModelStatusSensor` |

*Agreed by: Keaton, Fenster*

### D-3: New files — ml/ sub-package with data sources layer

**Resolved 2026-04-10 (Keaton revision v2):** Option B (ml/ sub-package) adopted and extended with `ml/sources/` data-ingestion layer. Auto-entity-detection removed; all data comes from external APIs.

```
custom_components/battery_charge_calculator/
└── ml/
    ├── __init__.py
    ├── data_pipeline.py          — DataFrame construction, feature engineering, cleaning
    ├── model_trainer.py          — training + prediction (sklearn Pipeline)
    ├── model_persistence.py      — joblib atomic save/load
    └── sources/
        ├── __init__.py           — exports: get_consumption_source(), get_temp_source()
        ├── base.py               — HistoricalDataSource Protocol
        ├── givenergy_history.py  — fetch consumption from GivEnergy cloud API
        ├── octopus_history.py    — fetch consumption from Octopus API
        └── openmeteo_history.py  — fetch temperature from Open-Meteo API
sensors/
└── ml_model_status.py            — MLModelStatusSensor (unchanged from D-11)
```

All sources implement `HistoricalDataSource` (Protocol, `@runtime_checkable`): `async def fetch(session, start, end) -> pd.Series | None`. Returns UTC 30-min `DatetimeIndex` Series (kWh or °C). `None` = hard failure; empty Series = no data for range.

*Resolved by: Keaton (architecture), Fenster (implementation spec)*

### D-4: Model persistence — joblib with atomic write

```python
# Atomic save (POSIX rename — safe against HA mid-restart)
fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
joblib.dump(model, tmp_path, compress=3)
os.replace(tmp_path, path)   # atomic on Linux
```

- `joblib` ships inside scikit-learn — no extra dependency
- Load with `try/except`; retrain on any load error
- At startup: load if model present and within age threshold; else train in executor
- *Agreed by: Keaton, Fenster*

✅ **Resolved 2026-04-10 by Keaton:** `hass.config.path("battery_charge_calculator_model.pkl")` — HA config directory persists across HACS updates; `custom_components/` would be wiped on each HACS upgrade, silently destroying the trained model.

### D-5: Async safety — all blocking work in executor

All of the following run via `hass.async_add_executor_job()` or the Recorder's own executor:

1. `get_significant_states` (Recorder, synchronous)
2. DataFrame construction (`build_training_df`) — CPU work
3. scikit-learn `.fit()` — CPU-bound
4. `joblib.dump / load` — blocking I/O

*Agreed by: Keaton, Hockney, Fenster*

### D-6: Model choice ✅ CLOSED

**Decision: `HistGradientBoostingRegressor` as primary; `Ridge(alpha=1.0)` as cold-start fallback.**

Switching condition: use HistGBR when N_clean ≥ 500 (per D-10 gate); fall back to Ridge while below that gate.

Rationale: native NaN handling in HistGBR is critical — Open-Meteo failures or GivEnergy gaps produce NaN features; Ridge would need imputation at every inference. Inference time for 48 slots: 5–15 ms (within 50 ms budget). Model size 1–5 MB — acceptable on Pi 4. Ridge fallback ensures the blend formula continues working during cold-start. *Resolved 2026-04-10 by Keaton.*

| Option considered | Proposed by | Inference (48 slots) | Model size | Native NaN | Notes |
|---|---|---|---|---|---|
| `Ridge + PolynomialFeatures(degree=2)` | Keaton | <1 ms | <50 KB | No | Superseded by Ridge fallback role |
| `HistGradientBoostingRegressor` + `Ridge` fallback | Hockney | 5–15 ms | 1–5 MB | **Yes** | **Selected** |
| `GradientBoostingRegressor` (n_estimators=100, max_depth=3) | Fenster | ~50–100 ms | ~5–20 MB | No | Not selected |

### D-7: Feature set ✅ CLOSED

**Decision: Hockney's full 15-feature vector, plus `octopus_import_kwh` as conditional feature #16 when `ML_CONSUMPTION_SOURCE == "both"`.** *Resolved 2026-04-10 by Keaton.*

```
 1. physics_kwh              — physics model output for slot (kWh)
 2. outdoor_temp             — °C (Open-Meteo or HA entity)
 3. temp_delta_1slot         — temp change from previous 30-min slot
 4. temp_delta_24h           — temp change from same slot 24 h prior
 5. rolling_mean_6h          — rolling mean consumption (past 6 h)
 6. hour_sin                 — sin(2π × hour / 24)
 7. hour_cos                 — cos(2π × hour / 24)
 8. dow_sin                  — sin(2π × day_of_week / 7)
 9. dow_cos                  — cos(2π × day_of_week / 7)
10. doy_sin                  — sin(2π × day_of_year / 365)
11. doy_cos                  — cos(2π × day_of_year / 365)
12. is_weekend               — bool (0/1)
13. slot_index               — 0–47 (half-hour slot within 24 h)
14. temp_delta_1slot_sq      — temp_delta_1slot²
15. physics_kwh_sq           — physics_kwh²
--- "both" mode only (see D-18) ---
16. octopus_import_kwh       — grid import for slot from Octopus API (kWh); NaN otherwise
```

Rationale: circular time encodings eliminate midnight/Sunday discontinuities; multi-lag temperature captures thermal inertia; `physics_kwh` as feature lets HistGBR learn the residual directly. Feature #16 lets the model separate high-consumption/low-solar days from high-consumption/high-solar days; HistGBR handles NaN natively when absent.

### D-8: Config constants

**Updated 2026-04-10 (Keaton revision v2):** `ML_POWER_SENSOR_ENTITY_ID` and `ML_TEMP_SENSOR_ENTITY_ID` removed (auto-entity-detection abandoned). New source-selection constants added.

```python
# ML — data source selection
ML_CONSUMPTION_SOURCE           = "ml_consumption_source"
ML_TEMP_SOURCE                  = "ml_temp_source"
ML_TEMP_ENTITY_ID               = "ml_temp_entity_id"   # only if ML_TEMP_SOURCE == "ha_entity"

# Valid values
ML_CONSUMPTION_SOURCE_GIVENERGY = "givenergy"
ML_CONSUMPTION_SOURCE_OCTOPUS   = "octopus"
ML_CONSUMPTION_SOURCE_BOTH      = "both"   # GivEnergy primary; Octopus cross-validation
ML_TEMP_SOURCE_OPENMETEO        = "openmeteo"
ML_TEMP_SOURCE_HA_ENTITY        = "ha_entity"

# Defaults
DEFAULT_ML_CONSUMPTION_SOURCE  = ML_CONSUMPTION_SOURCE_GIVENERGY
DEFAULT_ML_TEMP_SOURCE         = ML_TEMP_SOURCE_OPENMETEO

# Retained (unchanged)
ML_TRAINING_DAYS              = "ml_training_days"       # lookback window
DEFAULT_ML_TRAINING_DAYS      = 90                       # Fenster/Keaton agree
ML_ENABLED                    = "ml_enabled"             # master switch, default False
ML_BLEND_WEIGHT               = "ml_blend_weight"        # Keaton default 0.3; Hockney ramp
ML_MIN_TRAINING_DAYS          = "ml_min_training_days"   # ⚠️ open — see D-10
ML_TRAINING_LOOKBACK_DAYS     = "ml_training_lookback_days"

# New — Octopus meter serial (distinct from MPAN; required for consumption history)
OCTOPUS_METER_SERIAL          = "octopus_meter_serial"
```

Backwards compatibility: existing config entries with no `ML_CONSUMPTION_SOURCE` key default to `"givenergy"`; no `ML_TEMP_SOURCE` key defaults to `"openmeteo"`. Any stored `ML_POWER_SENSOR_ENTITY_ID` / `ML_TEMP_SENSOR_ENTITY_ID` values are silently ignored on load.

**Blend weight — ✅ CLOSED 2026-04-10 by Keaton:** Hockney's 0→1 ramp adopted:
```
w_ML = min(1.0, (N_clean - 500) / 2000)
```
- N_clean = 500 (gate threshold): w_ML = 0.0 → pure physics
- N_clean = 2500: w_ML = 1.0 → full ML weight
- Between: linear interpolation

Rationale: fixed w=0.3 gives no safety net at cold start; the ramp builds trust gradually as evidence accumulates. D-1 (`w_ML = 0` at cold start) is preserved exactly. `ML_BLEND_WEIGHT` constant retained for manual override but ramp is the default behaviour.

### D-9: Retrain schedule ✅ CLOSED

**Decision: Monthly retrain + RMSE-triggered immediate retrain.** *Resolved 2026-04-10 by Keaton.*

- **Scheduled:** retrain on the 1st of each month at 03:00 local time via `async_track_point_in_time` + monthly recurrence.
- **RMSE trigger:** at each 03:00 daily health check, compute rolling 7-day RMSE on held-out last-7-days samples. If 7-day RMSE > 1.5× training RMSE → trigger immediate retrain regardless of schedule.
- **Lookback window:** rolling 12 months with exponential sample weights (recent data weighted higher).

Rationale: monthly reduces SD card writes by ~4× vs weekly (52 → ~14 retrains/year). Residential patterns are stable month-to-month. RMSE trigger catches structural changes (new appliance, EV added) quickly. Daily RMSE check is cheap (predict only, no refit).

### D-10: Minimum training data gate ✅ CLOSED

**Decision: Hockney's quality-based gate — N_clean ≥ 500 AND temp_range ≥ 5°C.** *Resolved 2026-04-10 by Keaton.*

```python
def training_gate_passed(df: pd.DataFrame) -> bool:
    n_clean = len(df)
    temp_range = df["outdoor_temp"].max() - df["outdoor_temp"].min()
    return n_clean >= 500 and temp_range >= 5.0
```

If gate not passed: state = `insufficient_data`; pure physics used; no training attempt; re-evaluated at next scheduled interval.

Rationale: raw count gates can pass on bad data (repeated identical readings). Temperature range gate ensures meaningful variation has been observed. 500 clean slots ≈ 10 days at 48 slots/day with `valid_fraction_per_day ≥ 0.6`, ensuring weekday+weekend coverage.

### D-11: MLModelStatusSensor

**Entity ID:** `sensor.battery_charge_calculator_ml_model_status`

States: `disabled` | `insufficient_data` | `training` | `ready` | `error`

Attributes: `last_trained`, `training_samples`, `r2_score` (or equivalent), `blend_weight`, `model_age_days`, `ml_enabled`, `error_message`

*Agreed by: Keaton (full spec); Fenster references sensor implicitly*

### D-12: Anomaly detection for training data (Hockney)

1. Hard exclusions: zero/negative where `physics_kwh > 0.2`; absolute value > `MAX_SLOT_KWH`
2. Temporal gap exclusion: exclude ±1 slot around gaps > 15 min
3. **Residual z-score fencing** (primary): `|z| > 3.5` using per-`slot_index` z-score of `residual = actual - physics_kwh`
4. Per-slot IQR fallback: `actual > Q3 + 3×IQR` (safety net)
5. Flat-line freeze: ≥6 consecutive identical non-zero readings → exclude run

Training proceeds only if: N_clean ≥ 500, valid_fraction_per_day ≥ 0.6, temp range ≥ 5°C, ≥ 20 clean slots per `slot_index`.

*Agreed by: Hockney; not contradicted by others*

### D-13: Questions for Robert ✅ ALL CLOSED

**Updated 2026-04-10 (Keaton final close-out):** All Q1–Q11 resolved using Robert's confirmed answers.

1. **Model choice** (D-6): ✅ HistGBR + Ridge fallback. See D-6.
2. **Feature set** (D-7): ✅ Full 15-feature vector + conditional Octopus feature #16. See D-7.
3. **Retrain schedule** (D-9): ✅ Monthly + RMSE-triggered. See D-9.
4. **Min data gate** (D-10): ✅ N_clean ≥ 500 + temp_range ≥ 5°C. See D-10.
5. **Model file path** (D-4): ✅ `hass.config.path("battery_charge_calculator_model.pkl")`. See D-4.
6. **Blend weight default** (D-8): ✅ 0→1 ramp. See D-8.
7. **EV charging exclusion** (D-17): ✅ Auto-detection from power data; no binary sensor required. See D-17 (Hockney Hybrid-D algorithm).
8. **GivEnergy API timestamp handling**: ✅ Parse defensively — convert tz-aware offsets to UTC; assume UTC for naive timestamps. Wrapper: `parse_givenergy_timestamp(raw)` in `givenergy_history.py`.
9. **"Both" source mode** (D-18): ✅ Octopus import is a feature column (#16) in the model. See D-7 and D-18.
10. **Open-Meteo rate limits**: ✅ No caching layer needed. 52 retrains/year × 1 call = 52 calls/year; well within 10,000/day free tier.
11. **HA entity fallback for temperature**: ✅ Open-Meteo failure falls back to HA Recorder entity (`ML_TEMP_ENTITY_ID`). If both fail, training proceeds with `outdoor_temp = NaN`; HistGBR handles natively. See D-15.

---

### D-14: External API client shared design

*Added 2026-04-10 — source: Fenster*

- **Session ownership**: caller (coordinator / `MLDataOrchestrator`) owns one `aiohttp.ClientSession` per fetch cycle and passes it to each source. Sources do not create sessions. Follows existing `octopus_agile.py` pattern.
- **Return type contract**: all sources return `pd.Series` (`dtype=float64`, UTC `DatetimeIndex`, `freq="30min"`, named). `None` = source unreachable or auth failed. Empty Series = no data for range. Callers handle both without crashing.
- **Chunking limits**:
  | Source | Max days per request | Chunk strategy |
  |---|---|---|
  | GivEnergy Cloud | 30 days (safe undocumented limit) | 30-day windows; concatenate |
  | Octopus Consumption | No documented limit | 90-day single call + pagination via `next` cursor |
  | Open-Meteo Archive | Unlimited for historical | 90-day single call |
- **Error handling**:
  - 4xx (except 429): log error, return `None`
  - 429: respect `Retry-After` header (default 60 s); retry once only
  - 5xx: log error, return `None`
  - Partial data: return what was received; training pipeline filters on `valid_fraction_per_day ≥ 0.6`
  - Network timeout: `aiohttp.ClientTimeout(total=30)` per request (60 s for Open-Meteo archive)

*Agreed by: Fenster; consistent with Keaton v2*

---

### D-15: Data source fallback logic and signal quality

*Added 2026-04-10 — source: Keaton revision v2*

**Consumption source fallback:**

| Priority | Condition | Source | Signal quality |
|---|---|---|---|
| 1 | `ML_CONSUMPTION_SOURCE == "givenergy"` (default) | GivEnergy REST API | Full (includes solar self-consumption) |
| 2 | GivEnergy fails **or** source == `"octopus"` | Octopus API | Partial (grid import only) |
| 3 | Both fail | Training aborted for this cycle | — |

**Temperature source fallback:**

| Priority | Condition | Source |
|---|---|---|
| 1 | `ML_TEMP_SOURCE == "openmeteo"` (default) | Open-Meteo archive API |
| 2 | Open-Meteo fails **or** source == `"ha_entity"` | HA Recorder entity (`ML_TEMP_ENTITY_ID`) |
| 3 | Both fail | Training proceeds with `outdoor_temp = NaN`; NaN-aware model path or imputation required |

**Surfacing fallback state** — all events reported via `MLModelStatusSensor` attributes (extends D-11):
```
consumption_source           # "givenergy" | "octopus" | "both"
consumption_source_fallback  # bool
consumption_signal_quality   # "full" | "partial"
temp_source                  # "openmeteo" | "ha_entity" | "imputed"
temp_source_fallback         # bool
last_fetch_error             # str | null
```
No silent degradation. When GivEnergy fails and Octopus fallback is used, an HA persistent notification is raised warning the user that the model trained on import-only data.

*Agreed by: Keaton; consistent with Fenster implementation spec*

---

### D-16: Optionality and graceful degradation architecture

*Added 2026-04-10 — source: Keaton*
*Status: ✅ CLOSED*

The ML feature is fully opt-in. The integration must continue to work identically to today when ML is disabled or unavailable. Every ML code path must be explicitly guarded.

**Master switch:** `ML_ENABLED` defaults to `False`. ML is never activated without the user explicitly enabling it via the config flow options step.

**Code-path guard pattern** (required at every ML call site in `coordinators.py`, `power_calculator.py`, `sensor.py`):
```python
if self._ml_estimator and self._ml_estimator.is_ready:
    prediction = self._ml_estimator.predict(features)
else:
    prediction = None   # falls through to pure physics
```

`PowerCalculator.from_temp_and_time()` is **unchanged**. ML correction is applied as a post-step in the coordinator, consistent with D-1.

**Failure mode table:**

| Failure | Required response |
|---|---|
| `scikit-learn` not importable | `ImportError` caught at `ml/__init__.py` top-level; `ML_AVAILABLE = False`; warning logged once; ML silently disabled for session |
| Model file corrupt/incompatible | `except Exception` in `model_persistence.py`; retrain triggered; state → `error` temporarily |
| Training fails at startup | Warning logged; state → `error`; integration continues with pure physics; no HA restart |
| Individual API fetch fails | Training skipped for cycle; warning; next retrain at next scheduled interval |
| Training gate not passed | State → `insufficient_data`; pure physics used; re-evaluated at next interval |

**No ML failure mode may crash the integration, raise an unhandled exception to the HA event loop, or produce orphaned entities.**

**Entity registration guard:** `MLModelStatusSensor` registered only when `ML_ENABLED = True`. No orphaned entities when ML is disabled.

**Config field optionality:** No new required config fields. `OCTOPUS_METER_SERIAL` is optional. GivEnergy API token reused from existing config entry — no new credential prompts. All ML config keys use `.get(key, default)`.

---

### D-17: EV charging auto-detection

*Added 2026-04-10 — source: Keaton (requirement), Hockney (algorithm spec)*
*Status: ✅ CLOSED — algorithm specified*

**Decision:** Statistical auto-detection of EV / large-load charging blocks. No binary sensor, no user configuration. Fully parameter-free from user perspective (all thresholds are internal constants with documented derivation).

**Algorithm: Hybrid D (Residual Magnitude + Persistence + Absolute Floor)**

Call `detect_ev_blocks(power_kwh, physics_kwh)` in `build_training_df()` **after** D-12 hard exclusions and **before** residual z-score fencing.

**Inputs:** `power_kwh` (UTC 30-min float Series), `physics_kwh` (same index, or `None` at cold start)

**Outputs:** `exclusion_mask` (bool Series, True = exclude), `ev_blocks` (list of detected run dicts)

**Internal constants:**
```python
LARGE_LOAD_FLOOR_KWH     = 1.5   # kWh/slot  (~3 kW sustained) — minimum to flag
RESIDUAL_IQR_MULTIPLIER  = 4.0   # residual > 4×IQR AND > RESIDUAL_ABS_MIN
RESIDUAL_ABS_MIN_KWH     = 1.0   # kWh/slot minimum residual
MIN_RUN_SLOTS            = 3     # ≥ 90 consecutive minutes to qualify as EV
BUFFER_SLOTS             = 1     # ±1 buffer slot around each detected run
COLD_START_PERCENTILE    = 98    # used when physics_kwh is None
COLD_START_FLOOR_KWH     = 2.5   # kWh/slot (~5 kW) cold-start absolute floor
```

**Derivation of RESIDUAL_IQR_MULTIPLIER = 4.0:** A 7 kW EV charger adds ~3.0–3.5 kWh/slot residual against a typical IQR of ±0.25–0.40 kWh → ratio 8–14. A cold-snap heating residual is ±0.3–0.8 kWh → ratio 1–3. The gap above 4.0 cleanly separates EV from heating. Conservative (under-excludes rather than over-excludes).

**Integration in `data_pipeline.py`:**
```python
# Stage 2: EV / large-load block exclusion (D-12 extension)
ev_exclusion, ev_blocks = detect_ev_blocks(
    power_kwh   = df["actual_kwh"],
    physics_kwh = df["physics_kwh"] if physics_available else None,
)
df = df[~ev_exclusion]
```

**MLModelStatusSensor attribute extensions (D-11 + D-17):**
```python
"ev_detection_mode":    "residual_iqr" | "cold_start_absolute",
"ev_excluded_slots":    int,
"ev_excluded_fraction": float,
"ev_blocks_detected":   int,
"ev_blocks":            list[dict],   # capped at 20 most recent; sorted desc
```

**D-12 extension (appended to D-12 §5):**
> 6. **EV / large-load block exclusion (Hybrid D, Hockney 2026-04-10):** Before residual z-score fencing, run `detect_ev_blocks(power_kwh, physics_kwh)`. Flags runs of ≥3 consecutive slots where `residual > max(4.0×IQR_residual, 1.0 kWh)` AND `actual > 1.5 kWh/slot`. Cold-start path: flags slots above `max(98th percentile, 2.5 kWh)`. Applies ±1 buffer slot. Detected blocks logged to `MLModelStatusSensor`.

**Open questions for Robert:**
1. **DHW/immersion heater distinction**: EV sessions ≥3 h; DHW typically 1–2 h (2–4 slots). Add `MAX_RUN_SLOTS` upper bound to exempt DHW from exclusion? Low complexity, but adds edge-case handling. Deferred to Robert's call.
2. **Persistent audit log**: write `ev_blocks` to `<config_dir>/bcc_ev_exclusions.json` for post-retrain audit? Low effort. Robert to confirm.
3. **22 kW rapid charger**: 11 kWh/slot reliably caught by cold-start path. No special treatment needed. ✅ Closed.

---

### D-18: "both" source mode implementation + OCTOPUS_METER_SERIAL

*Added 2026-04-10 — source: Fenster*
*Status: ✅ CLOSED (implementation spec)*

**`OCTOPUS_METER_SERIAL` constant** (already listed in D-8, usage confirmed here): place after `OCTOPUS_EXPORT_MPN` in `const.py`. Field is optional; surfaces only in `step_ml_settings` options step when `ML_CONSUMPTION_SOURCE` includes `"octopus"` or `"both"`.

**Auto-discovery:** At config flow time, attempt to auto-populate from Octopus API `GET /v1/electricity-meter-points/{mpan}/meters/`. Pre-fill the field with discovered value; user may override. On any failure, fall back to manual entry. If meter_serial absent at fetch time: log warning, return `None`, skip gracefully — no crash.

**"both" mode — feature construction:**
```
ML_CONSUMPTION_SOURCE == "both":
  y  (target)   = givenergy_consumption_kwh
  X  (features) = [base D-7 features, octopus_import_kwh]
```
`octopus_import_kwh` column included only when `ML_CONSUMPTION_SOURCE == "both"` AND Octopus fetch succeeded. Otherwise column is `NaN` (HistGBR handles natively).

**DST / UTC normalisation (required):** all Series must be UTC before join. `_normalise_to_utc()` handles tz-naive (assume Europe/London, localize → UTC), tz-aware Europe/London (convert → UTC), and deduplicates DST fall-back duplicates (keep first).

**Inference-time `octopus_import_kwh` proxy:** use per-`slot_index` mean from training data, computed once at train time, persisted in `model_metadata["slot_import_means"]`.

**Model compatibility guard:** if `model_metadata["trained_with_octopus_feature"]` differs from whether Octopus is currently available, **invalidate loaded model and force retrain**. Feature column schema is authoritative in `model_metadata["feature_columns"]`.

**Cross-file changes:**

| File | Change |
|---|---|
| `const.py` | `OCTOPUS_METER_SERIAL` (already in D-8) |
| `config_flow.py` | `async_step_ml_octopus_serial` + `_ml_octopus_serial_schema()` + `_fetch_meter_serial()` |
| `ml/sources/octopus_history.py` | Guard on empty `meter_serial`; return `None` + log warning |
| `ml/data_pipeline.py` | `build_feature_matrix()` with conditional `octopus_import_kwh`; `_normalise_to_utc()` helper |
| `ml/model_trainer.py` | Compute + persist `slot_import_means`; inject at inference; `validate_model_compatibility()` |
| `ml/model_persistence.py` | Persist `model_metadata` alongside `.pkl` (companion `.json` or embedded in joblib artifact) |
| `sensors/ml_model_status.py` | Expose `consumption_signal_quality` (D-15) |

**Open questions for Robert:**
1. **Auto-fill UX**: auto-populate `OCTOPUS_METER_SERIAL` into editable text field (recommended) or show a "Discover" button?
2. **Multiple meters**: if `GET .../meters/` returns more than one, pick first or pick latest `installation_date`? Recommendation: latest by `installation_date`.
3. **Export meter feature (v2)**: `octopus_export_kwh` would sharpen the solar proxy. Deferred; `OCTOPUS_EXPORT_MPN` already in `const.py`.

---

## Active Decisions (other)

*No other active decisions recorded.*
