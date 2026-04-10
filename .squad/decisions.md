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

⚠️ **Open — Robert to decide model file path:**
- Keaton: `<config_dir>/battery_charge_calculator_ml_model.pkl`
- Fenster: `custom_components/battery_charge_calculator/models/power_model.pkl`

### D-5: Async safety — all blocking work in executor

All of the following run via `hass.async_add_executor_job()` or the Recorder's own executor:

1. `get_significant_states` (Recorder, synchronous)
2. DataFrame construction (`build_training_df`) — CPU work
3. scikit-learn `.fit()` — CPU-bound
4. `joblib.dump / load` — blocking I/O

*Agreed by: Keaton, Hockney, Fenster*

### D-6: Model choice ⚠️ OPEN — Robert to decide

| Option | Proposed by | Inference (48 slots) | Model size | Native NaN | Notes |
|---|---|---|---|---|---|
| `Ridge + PolynomialFeatures(degree=2)` | Keaton | <1 ms | <50 KB | No | Simplest; very fast; linear only |
| `HistGradientBoostingRegressor` (primary) + `Ridge` fallback | Hockney | 5–15 ms | 1–5 MB | **Yes** | Handles sensor dropouts; switches at N_clean≥500 |
| `GradientBoostingRegressor` (n_estimators=100, max_depth=3) | Fenster | ~50–100 ms | ~5–20 MB | No | Middle ground; standard GBR |

All three options are within the <50 ms inference budget for 48 slots. Hockney recommends HistGBR for native NaN handling and better scaling as data grows.

### D-7: Feature set ⚠️ OPEN — Robert to decide

**Minimal (Keaton / Fenster):**
`outdoor_temp`, `hour_of_day`, `day_of_week`, `is_weekend`, [`day_of_year`]

**Full 15-feature vector (Hockney):**
```
[physics_kwh, outdoor_temp, temp_delta_1slot, temp_delta_24h, rolling_mean_6h,
 hour_sin, hour_cos, dow_sin, dow_cos, doy_sin, doy_cos,
 is_weekend, slot_index, temp_delta_1slot_sq, physics_kwh_sq]
```

Hockney's rationale: circular time encoding eliminates artificial midnight/Sunday discontinuities; multi-lag temperature captures thermal inertia; `physics_kwh` as feature lets ML learn residual directly.

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

### D-9: Retrain schedule ⚠️ OPEN — Robert to decide

| Option | Proposed by | Frequency | Lookback |
|---|---|---|---|
| Weekly retrain | Keaton, Fenster | Every 7 days at 03:00 | Rolling 90 days |
| Monthly + trigger | Hockney | Every 30 days; plus immediate if 7-day RMSE > 1.5× training RMSE | Rolling 12 months, exponential sample weights |

Hockney argues monthly reduces SD card wear and is sufficient given stable residential patterns; trigger-based re-run catches structural changes quickly.

### D-10: Minimum training data gate ⚠️ OPEN — Robert to decide

| Threshold | Proposed by | Rationale |
|---|---|---|
| ≥ 672 slots (14 days) | Keaton | Conservative; ensures weekday+weekend coverage |
| ≥ 500 clean slots (~10 days) + temp range ≥5°C | Hockney | Data quality rather than raw count |
| ≥ 96 slots (2 days) | Fenster | Minimum for meaningful patterns |

Recommended: adopt Hockney's quality-based gate (N_clean ≥ 500 + temp range ≥ 5°C) which subsumes the count-only approaches.

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

### D-13: Questions open for Robert

**Updated 2026-04-10 (Keaton revision v2):** Q-8 (auto-detect entities) resolved — explicit config via existing API credentials adopted. Q-8–11 added.

1. **Model choice** (D-6): Ridge+Poly vs HistGBR+Ridge vs GBR?
   *Note: if Open-Meteo fails and temp imputation is needed, HistGBR's native NaN handling becomes more important.*
2. **Feature set** (D-7): minimal vs full 15-feature?
3. **Retrain schedule** (D-9): weekly vs monthly+trigger?
4. **Min data gate** (D-10): 2 days vs 10 days vs 14 days?
5. **Model file path** (D-4): config root vs inside `custom_components/`?
6. **Blend weight default** (D-8): 0.3 fixed vs 0→1 ramp?
7. **EV charging exclusion**: add optional `ml_exclude_entity_id` (binary sensor) to mask EV charging periods from training data?
8. **GivEnergy API field mapping**: confirm which field in `energy-flow-data` response represents total house consumption (`consumption` vs `load` vs a sum). Needs live API response validation.
9. **"Both" source mode**: when `ML_CONSUMPTION_SOURCE == "both"`, is Octopus import a feature column in the model or post-hoc validation only? Feature adds complexity; validation-only safer for v1.
10. **Open-Meteo rate limits**: free tier allows 10,000 calls/day; 90-day historical fetch = 1 call; weekly retrains = 52 calls/year. Confirm no caching layer needed.
11. **HA entity fallback for temperature**: if `ML_TEMP_SOURCE == "ha_entity"`, fetch goes through HA Recorder. Should Open-Meteo failure fall back to Recorder entity, or impute with climatological mean?

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

## Active Decisions (other)

*No other active decisions recorded.*
