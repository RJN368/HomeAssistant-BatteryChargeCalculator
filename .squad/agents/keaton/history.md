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
