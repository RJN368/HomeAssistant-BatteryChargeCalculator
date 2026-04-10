# Keaton — Lead

## Project Context
- Project: HomeAsssitant-BatteryChargeCalculator
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
