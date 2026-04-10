# Hockney — Heatloss & Maths Expert

## Project Context
- Project: HomeAsssitant-BatteryChargeCalculator
- Created: 2026-04-09
- User: robert.nash

## Learnings

### 2026-04-10 — ML Model Selection for Power Consumption Learning

**Task:** Recommend ML model, features, anomaly detection, and blend strategy for a Pi 4 / Home Assistant environment overlaid on existing `PowerCalulator` physics model.

**Key decisions made:**

- **Hybrid residual approach**: ML model trained on `actual - physics_kwh` (residual), not raw consumption. Include `physics_kwh` itself as a feature. This makes cold-start trivial (residual = 0 → pure physics) and reduces required training data.
- **Feature engineering**: Circular sinusoidal encoding for hour/day-of-week/day-of-year. Multi-lag temperature features (1-slot delta, 24h delta, 6h rolling mean) to capture thermal mass lag. 15 features total.
- **Model: HistGradientBoostingRegressor** (primary, ≥500 clean slots). Parameters: max_iter=100, max_depth=4, lr=0.05, min_samples_leaf=20, l2=1.0. Handles native NaN (essential for HA sensor dropouts). Inference on 48 slots ≈ 5–15ms on Pi 4, model ≈ 2–4 MB. Ridge Regression fallback when data is sparse.
- **Anomaly detection**: Residual z-score fencing (|z| > 3.5) after physics subtraction removes heteroscedasticity from temperature-driven variance. Belt-and-braces per-slot IQR (3× fence). Exclude zero readings where physics predicts >0.2 kWh, gaps > 15 min around HA restarts, and frozen sensor flat-lines.
- **Blend**: Additive correction formula: `ŷ = ŷ_physics + w_ml × δ_ml`. Weight w_ml ramps linearly from 0→1 over N_clean = 500→1000 slots, then decays with staleness factor exp(-days_since_retrain/60). Correction clamped to ±2×RMSE_train.
- **Retraining**: Monthly full retrain, rolling 12-month window, exponential sample weights (λ=0.004, half-life ≈6 months). Trigger retrain if 7-day RMSE > 1.5× training RMSE. No incremental/warm_start — can't forget old patterns.
- **Minimum viable**: 60 days clean data, ≥5°C temperature range, ≥500 clean slots, ≥60% daily quality before first training attempt.

**Physics model notes (from reading power_calculator.py):**
- `from_temp_and_time` returns heating + base_load; base_load is time-indexed (48 slots).
- Carnot-adjusted COP: `COP(T) = rated_cop × (T_flow − 7) / (T_flow − T_outdoor)`, clamped [1.0, 3×rated].
- Heat load = heat_loss (W/°C) × ΔT; divide by COP for electrical input; scale to kWh/30min.
- The residual the ML model learns is therefore: occupancy patterns, appliance cycles, thermal mass lag, systematic physics calibration error.

**Files written:** `.squad/decisions/inbox/hockney-ml-model-selection.md`

---

### 2026-04-10 — EV and Large-Load Charging Block Auto-Detection

**Task:** Specify a zero-config algorithm to detect and exclude EV/large-load charging blocks from ML training data. Extension of D-12 anomaly detection.

**Algorithm chosen: Hybrid D** — residual magnitude + persistence gate + absolute floor.

**Core logic:**
- Compute `residual = actual - physics_kwh` per slot (when physics is available).
- Slot is a **candidate** if: `residual > max(4.0 × IQR(residual), 1.0 kWh)` **AND** `actual > 1.5 kWh/slot`.
- Apply **persistence gate**: only flag runs of ≥ 3 consecutive candidate slots (≥ 90 min continuous load).
- Apply **±1 buffer slot** around each detected run to catch ramp-up/down.
- **Cold start (no physics)**: fall back to absolute threshold `max(98th percentile, 2.5 kWh/slot)`.

**Threshold reasoning:**
- `LARGE_LOAD_FLOOR_KWH = 1.5`: no residential charger runs sustainably below ~3 kW; eliminates appliance variance.
- `RESIDUAL_IQR_MULTIPLIER = 4.0`: typical physics residual IQR ≈ 0.25–0.40 kWh; EV residual ≈ 3.0–3.5 kWh → ratio 8–14×. Cold-snap heating residuals ≤ 2× IQR. Gap is clean above multiplier 4.0.
- `MIN_RUN_SLOTS = 3`: eliminates kettles, ovens, brief spikes; EV sessions are always ≥ 90 min.
- `COLD_START_FLOOR_KWH = 2.5`: conservative; misses 3 kW slow charger until first retrain, acceptable tradeoff.

**Key engineering decisions:**
- Algorithm is O(N); runs in < 10 ms on Pi 4 for 90-day window (4,320 slots). Safe for executor thread.
- Detected blocks stored in `MLModelStatusSensor` attributes: `ev_blocks_detected`, `ev_excluded_slots`, `ev_excluded_fraction`, `ev_blocks` (list, capped at 20 entries).
- Cannot distinguish simultaneous heating + EV — err on side of exclusion (correct behaviour; physics handles the temperature-explained portion).
- DHW boost cycles (1–2 h) are shorter than EV sessions but could be caught; noted as D-12 open question for Robert.

**Files written:** `.squad/decisions/inbox/hockney-ev-detection.md`
