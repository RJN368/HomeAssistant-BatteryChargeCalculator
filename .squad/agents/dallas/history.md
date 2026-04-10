# Dallas — HomeAssistant Specialist

## Project Context
- Project: HomeAsssitant-BatteryChargeCalculator
- Created: 2026-04-09
- User: robert.nash

## Learnings

## History

- 2026-04-10: ML sensor and const.py ML constants added. Created `sensors/ml_model_status.py` (MLModelStatusSensor), appended ML constants block to `const.py`, added `scikit-learn` to `manifest.json` requirements, exported `MLModelStatusSensor` from `sensors/__init__.py`, registered sensor conditionally in `sensor.py` when `ML_ENABLED` is True.
- 2026-04-10: ML wiring in config_flow, coordinators, power_calculator. Added `_ml_settings_schema` helper and `async_step_ml_settings` to both `BatteryChargCalculatorConfigFlow` (initial flow, calls `_create_entry`) and `BatteryChargCalculatorFlowHandler` (options flow, calls `_save_and_exit`). All terminal `_create_entry`/`_save_and_exit` call sites in both flow classes now route through `async_step_ml_settings` first (D-16). In `coordinators.py`: conditional `MLPowerEstimator` instantiation in `__init__` (only when `ML_ENABLED=True`, D-16), `async_start` in `_async_setup`, monthly retrain timer (`async_track_time_interval` 30 days, D-9), `_async_maybe_retrain_ml` method, `async_shutdown` cleanup for both timers and estimator, ML correction hook (D-1) in `octopus_state_change_listener` after physics estimate. In `power_calculator.py`: added `physics_estimate` alias of `from_temp_and_time` for explicit use by `MLPowerEstimator` during training data construction.
