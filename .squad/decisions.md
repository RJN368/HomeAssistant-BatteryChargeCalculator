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

**Post-close-out questions (raised 2026-04-10, resolved with Robert's confirmed answers):**

**Q2 — "both" mode at inference time:** ✅ **CLOSED.** Robert confirmed: use GivEnergy-only signal at prediction time. Octopus data is for TRAINING only. `build_inference_row()` always sets `octopus_import_kwh = np.nan`; no `slot_import_means` proxy; no circular dependency. See D-18 revised inference spec.

**Q3 — EV blocks: show user notification?** ✅ **CLOSED.** Robert confirmed: YES. Implementation in D-17 revised: HA persistent notification via `notification_id = "bcc_ev_exclusion"` (replaces on each retrain). See D-17 for full spec.

**Q4 — GivEnergy API timestamp handling:** ✅ **CLOSED (supersedes item 8 above).** Robert confirmed: fix at source. Always normalise to UTC at ingestion inside `GivEnergyHistorySource`. Silent correction — NO log warnings. `_parse_givenergy_timestamp(raw)` in `givenergy_history.py`: `ts.tz_localize("UTC")` for naive timestamps, `ts.tz_convert("UTC")` for tz-aware.

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

### D-17: EV charging auto-detection — Revised (Temperature-Correlation Discriminator)

*Added 2026-04-10 — source: Keaton (requirement), Hockney (algorithm spec)*
*Revised 2026-04-10 — Hockney: temperature-correlation discriminator added; original Hybrid-D superseded*
*Status: ✅ CLOSED — revised algorithm*

**Decision:** Statistical auto-detection of EV / large-load charging blocks with temperature-correlation discriminator to prevent heat pump false positives. No binary sensor, no user configuration. Fully parameter-free from user perspective.

**Problem addressed by revision:** The original Hybrid-D algorithm misclassifies heat pump electrical consumption as EV charging when the physics model is uncalibrated (`heating_type = "none"`, poorly-fitted Lorenz curve, or incorrect COP/heat_loss values) — the expected state for new installs. Physical key difference: heat pump electrical draw is strongly anti-correlated with outdoor temperature; an EV charger is temperature-independent.

**Algorithm: Hybrid-D with Temperature-Correlation Discriminator**

Call `detect_ev_blocks(power_kwh, physics_kwh, outdoor_temp_c)` in `build_training_df()` **after** D-12 hard exclusions and **before** residual z-score fencing.

**Function signature:**
```python
def detect_ev_blocks(
    power_kwh: pd.Series,          # UTC 30-min actual consumption
    physics_kwh: pd.Series | None, # same index, or None at cold start
    outdoor_temp_c: pd.Series | None = None,  # same index, or None if unavailable
) -> tuple[pd.Series, list[dict]]:
    """
    Detect and flag EV / large-load charging blocks for exclusion from ML training.

    Returns:
        exclusion_mask: bool Series (True = exclude)
        ev_blocks:      list of detected run dicts for MLModelStatusSensor
    """
```

**Internal constants:**
```python
# Original Hybrid-D constants (unchanged)
LARGE_LOAD_FLOOR_KWH     = 1.5    # kWh/slot (~3 kW sustained) — minimum to flag
RESIDUAL_IQR_MULTIPLIER  = 4.0    # residual > 4 × IQR
RESIDUAL_ABS_MIN_KWH     = 1.0    # kWh/slot minimum residual
MIN_RUN_SLOTS            = 3      # ≥ 90 consecutive minutes to qualify
BUFFER_SLOTS             = 1      # ±1 buffer slot around each detected run
COLD_START_PERCENTILE    = 98     # used when physics_kwh is None and outdoor_temp is None
COLD_START_FLOOR_KWH     = 2.5    # kWh/slot cold-start absolute floor

# NEW: temperature-correlation discriminator constants
TEMP_CORRELATION_UPPER   = -0.4   # stronger anti-correlation → heating → DO NOT exclude
TEMP_CORRELATION_LOWER   = -0.2   # weaker anti-correlation → ambiguous band
TEMP_CONTEXT_SLOTS       = 6      # ±6 slots (±3 hours) context window for Pearson r
TEMP_RANGE_MIN_C         = 3.0    # minimum temperature span in block to count as heating evidence
CV_EV_THRESHOLD          = 0.20   # low CV = flat sustained = EV-like

# NEW: cold-start physics proxy constant
PROXY_HEAT_LOSS_W_PER_C  = 100.0  # conservative proxy: 100 W/°C of ΔT from 20°C setpoint
PROXY_SETPOINT_C         = 20.0   # assumed internal setpoint for proxy estimate
```

**Stage 2 full algorithm:**
```
┌─────────────────────────────────────────────────────────────────────────┐
│ Stage 2: detect_ev_blocks(power_kwh, physics_kwh, outdoor_temp_c)       │
│                                                                          │
│  2a. CANDIDATE BLOCK DETECTION                                           │
│      (unchanged from original D-17 Hybrid-D)                            │
│                                                                          │
│      Case A — physics available AND outdoor_temp available:             │
│        residual = power_kwh - physics_kwh                               │
│        iqr = IQR(residual)                                               │
│        candidate = (residual > max(4.0 × iqr, 1.0)) AND                 │
│                    (power_kwh > 1.5)                                     │
│                                                                          │
│      Case B — physics_kwh is None BUT outdoor_temp available:           │
│        proxy_physics = PROXY_HEAT_LOSS_W_PER_C × max(0,                 │
│                          PROXY_SETPOINT_C - outdoor_temp_c) / 1000 / 2  │
│        Use proxy_physics in place of physics_kwh for residual calc      │
│        → ev_detection_mode = "proxy_physics_cold_start"                 │
│                                                                          │
│      Case C — both physics_kwh and outdoor_temp are None:               │
│        candidate = power_kwh > max(                                      │
│                      percentile(power_kwh, 98), 2.5 kWh)               │
│        → ev_detection_mode = "temporal_cv_fallback"                     │
│        → skip to step 2c (CV discriminator)                             │
│                                                                          │
│      Apply MIN_RUN_SLOTS persistence gate → run-length filter           │
│      Produce: candidate_runs (list of [start_idx, end_idx] pairs)       │
│                                                                          │
│  2b. TEMPERATURE-CORRELATION DISCRIMINATOR (Cases A and B only)         │
│      For each candidate run:                                             │
│        window = [run_start - TEMP_CONTEXT_SLOTS,                        │
│                  run_end   + TEMP_CONTEXT_SLOTS]  (clipped to series)   │
│        r = pearsonr(power_kwh[window], outdoor_temp_c[window])          │
│                                                                          │
│        if r < TEMP_CORRELATION_UPPER (i.e. r < −0.4):                  │
│          → strongly anti-correlated → HEATING LOAD                      │
│          → REMOVE from candidate list (do not exclude from training)    │
│                                                                          │
│        elif |r| < 0.2 OR r > 0:                                         │
│          → uncorrelated or positive correlation → EV / appliance        │
│          → KEEP in candidate list (will be excluded)                    │
│                                                                          │
│        else (−0.4 ≤ r < −0.2):                                          │
│          → AMBIGUOUS → proceed to step 2c secondary discriminator       │
│                                                                          │
│  2c. SECONDARY DISCRIMINATOR (ambiguous blocks + Case C CV fallback)    │
│                                                                          │
│      For ambiguous temperature blocks (−0.4 ≤ r < −0.2):               │
│        temp_span = max(outdoor_temp_c[run]) - min(outdoor_temp_c[run])  │
│        if temp_span ≥ TEMP_RANGE_MIN_C (3.0°C):                         │
│          → meaningful thermal variation in block → HEATING              │
│          → REMOVE from candidate list                                    │
│                                                                          │
│        elif mean power within 20% of proxy physics at mean block temp:  │
│          → consistent with physics estimate → HEATING                   │
│          → REMOVE from candidate list                                    │
│                                                                          │
│        else:                                                             │
│          → KEEP in candidate list (exclude from training)               │
│                                                                          │
│      For Case C (CV fallback, no temp data):                            │
│        cv = std(power_kwh[run]) / mean(power_kwh[run])                  │
│        if cv < CV_EV_THRESHOLD (0.20):                                  │
│          → flat sustained load → KEEP (exclude)                         │
│        else:                                                             │
│          → variable load → likely heating → REMOVE                      │
│                                                                          │
│  2d. BUFFER SLOTS                                                        │
│      Apply ±BUFFER_SLOTS (±1 slot) around each surviving candidate run  │
│      Build and return exclusion_mask + ev_blocks metadata               │
└─────────────────────────────────────────────────────────────────────────┘
```

**Integration in `data_pipeline.py`:**
```python
# Stage 1: Hard exclusions (D-12 steps 1–2)
#   - zero/NaN readings where physics predicts > 0.2 kWh
#   - gaps > 15 min around HA restarts
#   - flat-line frozen sensor detection

# Stage 2: EV / large-load block exclusion (D-17 revised)
ev_exclusion_mask, ev_blocks = detect_ev_blocks(
    power_kwh      = df["actual_kwh"],
    physics_kwh    = df["physics_kwh"] if physics_available else None,
    outdoor_temp_c = df["outdoor_temp_c"] if "outdoor_temp_c" in df.columns else None,
)
df = df[~ev_exclusion_mask]

# Capture stats for MLModelStatusSensor
ev_stats = {
    "ev_detection_mode":    _infer_detection_mode(physics_available, outdoor_temp_available),
    "ev_excluded_slots":    int(ev_exclusion_mask.sum()),
    "ev_excluded_fraction": float(ev_exclusion_mask.mean()),
    "ev_blocks_detected":   len(ev_blocks),
    "ev_blocks":            ev_blocks[:20],  # cap at 20 most recent
}

# Stage 3: Residual z-score fencing (D-12 step 3, |z| > 3.5)
# Stage 4: Per-slot IQR fallback (D-12 step 4, 3× fence)
# Stage 5: Flat-line freeze exclusion (D-12 step 5)
```

**HA Persistent Notification:**
```python
if ev_stats["ev_blocks_detected"] >= 1:
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": "Battery Charge Calculator — ML Training",
            "message": (
                f"Excluded {ev_stats['ev_excluded_slots']} slots across "
                f"{ev_stats['ev_blocks_detected']} large-load blocks from ML "
                f"training data. These are likely EV charging sessions. "
                f"See the ML Model Status sensor for details."
            ),
            "notification_id": "bcc_ev_exclusion",
        },
    )
```

`notification_id = "bcc_ev_exclusion"` ensures the notification replaces the previous one on each retrain rather than stacking.

**MLModelStatusSensor attribute extensions (D-11 + revised D-17):**
```python
"ev_detection_mode":    str,   # one of: "residual_iqr" | "proxy_physics_cold_start" |
                               #         "temporal_cv_fallback"
"ev_excluded_slots":    int,
"ev_excluded_fraction": float,
"ev_blocks_detected":   int,
"ev_blocks": [                 # list of dicts, capped at 20, sorted by start desc
    {
        "start":     str,    # ISO-8601 UTC start of block
        "end":       str,    # ISO-8601 UTC end of block
        "slots":     int,    # number of 30-min slots in block (incl. buffer)
        "mean_kwh":  float,
        "peak_kwh":  float,
        "r_temp":    float | None,  # Pearson r with outdoor_temp in context window
        "cv":        float | None,  # coefficient of variation (CV fallback only)
        "reason":    str,    # "ev_iqr_residual" | "ev_proxy_residual" |
                             # "ev_no_temp_cv" | "ev_ambiguous_secondary"
    }
]
```

**D-12 extension (appended to D-12 §5):**
> 6. **EV / large-load block exclusion (Hybrid-D with Temperature Discriminator, Hockney 2026-04-10 revised):** Before residual z-score fencing, run `detect_ev_blocks(power_kwh, physics_kwh, outdoor_temp_c)`. Identifies runs of ≥3 consecutive slots with large residuals, then applies temperature-correlation discriminator (r < −0.4 → heating → preserved; ambiguous band uses thermal variation and proxy-physics secondary check; Case C CV < 0.20 when no temp data). Detected blocks logged to `MLModelStatusSensor`; HA persistent notification raised per retrain cycle when blocks found.

**Closed open questions:**
1. **DHW/immersion heater distinction** (previously deferred): ✅ **CLOSED.** Temperature-correlation discriminator handles this correctly. Scheduled hot-water loads are calendar/tariff-driven, not weather-driven; Pearson r near zero → correctly excluded. `MAX_RUN_SLOTS` not needed.
2. **Persistent audit log** (previously deferred): ✅ **CLOSED.** Robert confirmed blocks shown via HA persistent notification (see above). No separate JSON file; sensor attributes provide auditability.
3. **22 kW rapid charger**: ✅ **CLOSED.** 11 kWh/slot reliably caught by absolute floor path. No special treatment needed.

---

### D-18: "both" source mode implementation + OCTOPUS_METER_SERIAL

*Added 2026-04-10 — source: Fenster*
*Status: ✅ CLOSED (implementation spec)*

**`OCTOPUS_METER_SERIAL` constant** (already listed in D-8, usage confirmed here): place after `OCTOPUS_EXPORT_MPN` in `const.py`. Field is optional; surfaces only in `step_ml_settings` options step when `ML_CONSUMPTION_SOURCE` includes `"octopus"` or `"both"`.

**Auto-discovery (✅ Q1 CLOSED — Robert confirmed: auto-populate):** At `step_ml_settings`, call `_fetch_meter_serial()` against Octopus API (executor job) at render time. Pre-fill the `vol_schema` text field with the discovered value; field remains `vol.Optional(str)` — user may override. On any API failure (network, auth, no meters returned), fall back to empty text field. No crash; no blocking.

**Multiple meters (✅ Q2 CLOSED — Robert confirmed: pick latest by `installation_date`):** If `GET /v1/electricity-meter-points/{mpan}/meters/` returns more than one device, sort descending by `installation_date`; select index 0. Devices with missing/null `installation_date` treated as epoch (1970-01-01) so they lose to any device with a real date. No fallback to first-in-list to avoid selecting a decommissioned meter.

**"both" mode — feature construction:**
```
ML_CONSUMPTION_SOURCE == "both":
  y  (target)   = givenergy_consumption_kwh
  X  (features) = [base D-7 features, octopus_import_kwh]
```
`octopus_import_kwh` column included only when `ML_CONSUMPTION_SOURCE == "both"` AND Octopus fetch succeeded. Otherwise column is `NaN` (HistGBR handles natively).

**DST / UTC normalisation (required):** all Series must be UTC before join. `_normalise_to_utc()` handles tz-naive (assume Europe/London, localize → UTC), tz-aware Europe/London (convert → UTC), and deduplicates DST fall-back duplicates (keep first).

**Inference-time `octopus_import_kwh`:** always `NaN` when mode is `"both"`. `HistGradientBoostingRegressor` handles NaN natively; a column that is always NaN at inference contributes zero information. `build_inference_row()` sets `octopus_import_kwh = np.nan` unconditionally. No stored proxy values, no circular dependency. `slot_import_means` **removed**.

**`model_metadata` schema (revised):**
```python
model_metadata = {
    "feature_columns": [...],              # authoritative feature list
    "trained_with_octopus_feature": bool,  # True iff "both" mode active at train time
    "train_date": str,                     # ISO-8601
    "n_clean_samples": int,
    "train_rmse": float,
    "blend_weight_at_train": float,
}
```
`slot_import_means` is gone.

**Model compatibility guard (simplified):** compare `model_metadata["feature_columns"]` against current feature columns. Schema drift occurs only on mode switch (`"givenergy"` ↔ `"both"`), not on per-inference missing values.
```python
def validate_model_compatibility(model_metadata: dict, current_feature_columns: list[str]) -> bool:
    """Return True if the loaded model is compatible with the current feature schema."""
    return model_metadata.get("feature_columns") == current_feature_columns
```

**Cross-file changes:**

| File | Change |
|---|---|
| `const.py` | `OCTOPUS_METER_SERIAL` (already in D-8) |
| `config_flow.py` | `async_step_ml_octopus_serial` + `_ml_octopus_serial_schema()` + `_fetch_meter_serial()` |
| `ml/sources/octopus_history.py` | Guard on empty `meter_serial`; return `None` + log warning |
| `ml/data_pipeline.py` | `build_feature_matrix()` with conditional `octopus_import_kwh`; `_normalise_to_utc()` helper; `build_inference_row()` sets `octopus_import_kwh = np.nan` unconditionally |
| `ml/model_trainer.py` | Remove `_compute_slot_import_means()`; remove `slot_import_means` from `model_metadata`; `predict()` passes `NaN` for `octopus_import_kwh`; `validate_model_compatibility()` uses list comparison only |
| `ml/model_persistence.py` | Persist `model_metadata` alongside `.pkl` (companion `.json` or embedded in joblib artifact) |
| `sensors/ml_model_status.py` | Expose `consumption_signal_quality` (D-15) |

**All open questions closed:**
- **Q1 Auto-fill UX**: ✅ Auto-populate from API discovery at render time. See auto-discovery section above.
- **Q2 Multiple meters**: ✅ Pick latest by `installation_date`. See auto-discovery section above.
- **Q3 Export meter (v2)**: ✅ Deferred. `OCTOPUS_EXPORT_MPN` already in `const.py`. No further action in current scope.

---

## Annual Tariff Comparison Feature
*Added: 2026-04-13 — sources: Keaton (architecture), Hockney (maths review)*

### ADC-1: Separate coordinator (`TariffComparisonCoordinator`)

**Decision:** New lightweight `TariffComparisonCoordinator` (`tariff_comparison/__init__.py`), weekly update interval. Not merged into `BatteryChargeCoordinator`.

**Rationale:** `BatteryChargeCoordinator` runs every 60 seconds for real-time battery scheduling. Tariff comparison is a once-a-week operation; merging would add unnecessary complexity and risk to the hot path. Separate coordinator keeps concerns isolated.

*Agreed by: Keaton — proposed, awaiting Robert's answers to OQ-1 through OQ-6*

---

### ADC-2: Disk cache for historical data

**Decision:** JSON cache at `{config_dir}/battery_charge_calculator_tariff_cache.json` using the same atomic write pattern from D-4 (`tempfile.mkstemp` + `os.replace`).

**Rationale:** Historical meter consumption and tariff rates are immutable once a period has passed. Without a cache, a weekly update would re-fetch ~17,520 rows × N tariffs needlessly. Cache invalidated only on year rollover, new tariff added, or user refresh service call.

*Agreed by: Keaton — proposed*

---

### ADC-3: New sub-package `tariff_comparison/`

**Decision:** New sub-package alongside `ml/` rather than adding files to the top-level package.

```
custom_components/battery_charge_calculator/
├── tariff_comparison/
│   ├── __init__.py       — package init; exports TariffComparisonCoordinator
│   ├── client.py         — TariffComparisonClient (Octopus historical data)
│   ├── calculator.py     — cost calculation; returns monthly breakdown dicts
│   └── cache.py          — JSON disk cache with atomic write
└── sensors/
    └── tariff_comparison.py   — TariffComparisonSensor
```

**Rationale:** Mirrors `ml/` sub-package precedent (D-3); keeps feature self-contained and trivially removable. Consistent with D-2's scope-of-change table.

*Agreed by: Keaton — proposed*

---

### ADC-4: Tariff config stored as JSON string in options

**Decision:** `TARIFF_COMPARISON_TARIFFS` stored as a JSON-serialised string in the config entry options dict.

**Rationale:** HA config entry options are a flat dict; nested structures require serialisation. Consistent with `HEATING_KNOWN_POINTS` precedent (`const.py` line 37). Options flow deserialises with `json.loads()` before validation.

*Agreed by: Keaton — proposed*

---

### ADC-5: v1 uses manual tariff code entry (JSON text area)

**Decision:** Options flow presents a JSON text area for entering tariff pairs. No live Octopus product catalogue dropdown.

**Rationale:** The Octopus `/v1/products/` endpoint returns hundreds of entries including obsolete historical tariffs; filtering them into a meaningful dropdown requires substantial additional logic. Deferred to v2.

*Agreed by: Keaton — proposed*

---

### ADC-6: Rolling 12-month window ending at start of current month

**Decision:** Comparison window = `(today − 12 months, rounded to month start)` → `(start of current month)`.

**Rationale:** Avoids partial-month data at either boundary. Window advances automatically each month. Default; may be overridden per OQ-2.

*Agreed by: Keaton — proposed*

---

### ADC-7: Export earnings optional (gated on export meter serial)

**Decision:** Export earnings calculated only when `OCTOPUS_EXPORT_METER_SERIAL` is provided. If absent, export earnings = 0.0 and `export_meter_serial_missing: true` is set in sensor attributes.

**Rationale:** Export meter serial is not yet in the config (D-18 deferred export meter as "v2"). Feature must work without it.

*Agreed by: Keaton — proposed*

---

### ADC-H1: Critical — Rate-before-window seed required in `fetch_unit_rates()` *(Hockney)*

**Decision:** `fetch_unit_rates()` must issue a second request with `period_to={period_from}&page_size=1` before the main window fetch, and prepend the returned rate to `raw_rates` before building the rate map.

**Problem fixed:** The Octopus unit-rates endpoint filters `valid_from >= period_from`. For Standard Variable / Fixed tariffs whose single rate predates the window, the main query returns zero rows. Forward-fill has no seed → all 17,520 slots compute at 0 p/kWh silently. Agile unaffected (rates always generated within the window).

*Required by: Hockney — 2026-04-13. Applies to: `tariff_comparison/client.py` `fetch_unit_rates()`*

---

### ADC-H2: Critical — Export-only slots must not be dropped *(Hockney)*

**Decision:** The per-slot loop must iterate over the **union** of all distinct `interval_start` timestamps from both import and export lists. Missing per-meter slots default to 0 kWh.

**Problem fixed:** The prior implicit iteration domain was import-only. Any 30-min window present in export data but absent from import data (e.g. solar PV export during low-load hours) would be silently dropped — understating export earnings.

*Required by: Hockney — 2026-04-13. Applies to: `tariff_comparison/calculator.py` slot iteration*

---

### ADC-H3: Formula gap — `include_standing_charges` Iverson bracket *(Hockney)*

**Decision:** The monthly net-cost formula must make the standing charge conditional explicit:

$$\text{net\_cost\_gbp}(m) = \text{import\_cost\_gbp}(m) - \text{export\_earnings\_gbp}(m) + \text{standing\_charge\_gbp}(m) \times [\text{include\_standing\_charges}]$$

**Problem fixed:** The function signature accepts `include_standing_charges: bool` and §4.2 supports per-tariff override, but the printed formula unconditionally added standing charges.

*Required by: Hockney — 2026-04-13. Applies to: `_docs/tariff-comparison.md` §6.3 and `tariff_comparison/calculator.py`*

---

### ADC-H4: Formula gap — `days(m)` must use calendar arithmetic, not slot count *(Hockney)*

**Decision:** `days(m)` in the standing charge formula must be computed via `calendar.monthrange` or `datetime.date` arithmetic — calendar days, not `slot_count / 48`.

**Problem fixed:** On DST spring-forward days, `slot_count / 48 = 0.958`; on fall-back days, `1.042`. This corrupts standing charges by ~±46p on those months. Calendar arithmetic gives integer day counts unaffected by DST.

*Required by: Hockney — 2026-04-13. Applies to: `tariff_comparison/calculator.py` monthly aggregation*

---

### ADC-H5: Implementation requirement — timezone-aware UTC throughout *(Hockney)*

**Decision:** All `datetime` values used as rate map keys and consumption `interval_start` values must be timezone-aware UTC at every parse boundary (API response and cache load). Implementers must verify `isinstance(dt.tzinfo, timezone)` or equivalent at parse time.

**Problem fixed:** Mixed tz-aware / tz-naive datetimes cause every dict lookup to fail silently — `coverage_pct` reads as 0%, forward-fill pads the entire year from the last seed rate.

*Required by: Hockney — 2026-04-13. Applies to: `tariff_comparison/client.py` and `tariff_comparison/calculator.py`*

---

### D-TC-1: v1 uses Approach C — naive replay with honest disclosure

*Added: 2026-04-13 — source: Keaton (amendment), raised by robert.nash*

**Decision:** For all non-current tariffs in v1, real meter reads are replayed against each tariff's historical rates directly. The limitation is disclosed explicitly in sensor attributes:

- `comparison_method: "naive_replay"` per non-current tariff entry in sensor `extra_state_attributes`.
- `data_quality_notes` array includes a plain-English warning string per non-current tariff.
- The Lovelace ApexCharts card **must** render `data_quality_notes` visibly (footnote or tooltip on the chart) — this is a functional requirement, not cosmetic.

**Rationale:** Delivers the feature without delay. Full simulation (Approach A) requires 365 × `GeneticEvaluator` runs per non-current tariff per year, an `OpenMeteoHistoricalClient`, and background task orchestration — significant additional scope. Honest disclosure ensures Robert is not misled; the directional bias (current tariff looks comparatively better because its consumption pattern was optimised for it) is a known, flagged artefact.

*Decided by: Keaton — 2026-04-13*

---

### D-TC-2: v2 upgrade target is Approach A — full battery schedule simulation

*Added: 2026-04-13 — source: Keaton (amendment)*

**Decision:** The v2 upgrade path from Approach C to full simulation (Approach A) is fully specified in §6.7 of `_docs/tariff-comparison.md`. Required additions:

1. `OpenMeteoHistoricalClient` — fetches `archive-api.open-meteo.com/v1/archive` for historical hourly temperature; free, no auth, same lat/lon as ML feature.
2. Per-day `PowerCalculator.calculate()` + `GeneticEvaluator.run()` calls via `hass.async_add_executor_job()` (consistent with D-5).
3. Background task accumulating simulated import/export per day → monthly cost aggregation.
4. On completion: `comparison_method` updated to `"simulation"`; `data_quality_notes` cleared; results cached to disk.

**Why not Approach B (base-load isolation):** Accuracy gain is marginal unless 12-month battery SOC history is reliably available (uncertain). Adds complexity without the rigour of full simulation. Not recommended for v2.

**No architectural changes required:** `GeneticEvaluator`, `PowerCalculator`, and Open-Meteo access are all existing or straightforward additions. Upgrade is additive only.

*Decided by: Keaton — 2026-04-13*

---

### Open questions — Annual Tariff Comparison (pending Robert's answers)

| # | Question | Default | Blocking? |
|---|---|---|---|
| OQ-1 | Export meter serial: manual config, auto-discover from account API, or defer? | Defer (export = 0) | No |
| OQ-2 | Rolling 12 months vs. fixed calendar year? | Rolling 12 months | No |
| OQ-3 | Current tariff: always pinned or user-removable? | Always pinned | No |
| OQ-4 | Standing charges: included by default? | Yes | No |
| OQ-5 | Update interval: weekly or monthly? | Weekly | No |
| OQ-6 | Partial month handling at window boundary? | Rolling window avoids it | No |
| OQ-7 | Consumption data model for non-current tariffs: Approach A (simulation), B (base-load isolation), or C (naive replay with disclosure)? | Approach C for v1; Approach A for v2 | No (C is safe default) |

All defaults are safe; implementation can proceed without Robert's answers.

---

## Active Decisions (other)

*No other active decisions recorded.*
