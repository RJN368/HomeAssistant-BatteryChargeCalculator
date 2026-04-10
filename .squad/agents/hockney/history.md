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
