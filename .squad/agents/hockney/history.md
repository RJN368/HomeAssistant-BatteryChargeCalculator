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

---

### 2026-04-10 — D-17 Revised: Temperature-Correlation Discriminator for EV vs Heat Pump

**Task:** Revise the D-17 Hybrid-D algorithm to prevent heat pump loads from being falsely excluded as EV charging, particularly when the physics model is uncalibrated or absent.

**Root cause of original failure:** The residual approach only distinguishes EV from heat pump if the physics model *correctly* predicts the heat pump draw. When `heating_type = "none"`, or COP / heat_loss parameters are wrong, the entire heat pump load appears as residual and gets flagged identically to an EV. This is a common state for new installs.

**Key physical insight used:**
- Heat pump draw is strongly anti-correlated with outdoor temperature (r ≈ −0.7 to −0.9 over a winter day spanning ≥5°C).
- EV charger draw is temperature-independent (r ≈ 0 ± 0.1 over any multi-hour window).
- This correlation is a reliable physical discriminator that does not require a calibrated physics model.

**Revised algorithm adds three discriminator gates after candidate detection:**

1. **Temperature-correlation gate (step 2b):** Compute Pearson r between `power_kwh` and `outdoor_temp_c` over a ±6-slot (±3h) context window around each candidate run.
   - `r < −0.4` → strongly anti-correlated → heating load → **do not exclude**
   - `|r| < 0.2` or `r > 0` → temperature-independent → EV/appliance → **exclude**
   - `−0.4 ≤ r < −0.2` → ambiguous → proceed to secondary discriminator

2. **Ambiguous block secondary discriminator (step 2c):** For ambiguous blocks:
   - Temperature span ≥ 3°C within block → meaningful thermal variation → heating → **do not exclude**
   - Mean consumption within 20% of proxy physics estimate → consistent with heating → **do not exclude**
   - Otherwise → **exclude**

3. **CV fallback (step 2c, cold start with no temperature data):** When neither physics nor temperature is available, use coefficient of variation within the block. CV < 0.20 (flat sustained load) → EV-like → **exclude**. CV ≥ 0.20 (variable) → likely heating → **do not exclude**.

**Cold-start with temperature but no physics:** Compute a proxy physics estimate using 100 W/°C heat loss and a 20°C setpoint assumption. Use as physics_kwh proxy for the residual calculation before applying the correlation gate. Better than falling back to pure percentile threshold.

**Threshold derivation summary:**
- Heat pump at −2°C to +8°C winter range: r ≈ −0.70 to −0.90. Worst-case mild night (4°C span): r ≈ −0.45. Safety margin of 0.05–0.5 below the −0.4 threshold.
- EV at 3.3 kW constant: r ≈ 0 ± 0.10. The gap to −0.2 is reliable.
- CV for EV: 0.03–0.15. CV for heating: 0.25–0.50 (thermostat cycling, defrost). CV_EV_THRESHOLD = 0.20 sits cleanly in the gap.

**Open questions resolved:**
- **Q1 (DHW/immersion heater):** CLOSED. Temperature-correlation discriminator handles this — immersion heaters are scheduled, temperature-independent loads (r ≈ 0). They will be correctly excluded from training, same as EV. No MAX_RUN_SLOTS parameter needed.
- **Q2 (Audit notification):** CLOSED. Robert confirmed: raise HA persistent notification via `persistent_notification.create` with `notification_id = "bcc_ev_exclusion"` (replaces on retrain). EV blocks also written to MLModelStatusSensor attributes.

**New constants added:**
```python
TEMP_CORRELATION_UPPER   = -0.4
TEMP_CORRELATION_LOWER   = -0.2
TEMP_CONTEXT_SLOTS       = 6
TEMP_RANGE_MIN_C         = 3.0
CV_EV_THRESHOLD          = 0.20
PROXY_HEAT_LOSS_W_PER_C  = 100.0
PROXY_SETPOINT_C         = 20.0
```

**New `ev_blocks` dict fields:** `r_temp` (Pearson r in context window), `cv` (CV fallback mode), `reason` (classification reason string).

**New `ev_detection_mode` values:** `"proxy_physics_cold_start"` (temp available, no physics), `"temporal_cv_fallback"` (neither available). Existing `"residual_iqr"` unchanged.

**Files written:** `.squad/decisions/inbox/hockney-d17-revised.md`
