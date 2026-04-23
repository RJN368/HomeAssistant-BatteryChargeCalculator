# Dallas — HomeAssistant Specialist

## Project Context
- Project: HomeAssistant-BatteryChargeCalculator
- Created: 2026-04-09
- User: robert.nash

## Learnings

- **Tariff comparison wiring (2026-04-16):** `async_setup_tariff_coordinator` must be called in `__init__.py:async_setup_entry` BEFORE `hass.config_entries.async_forward_entry_setups` so that the coordinator is registered in `hass.data[DOMAIN]` before `sensor.py:async_setup_entry` runs and looks it up. The coordinator key uses `entry_id + "_tariff"` convention.
- **Separate coordinator per feature:** The tariff comparison coordinator is decoupled from `BatteryChargeCoordinator` (weekly vs. 1-min update interval). Storing it under a distinct key in `hass.data[DOMAIN]` avoids complicating the hot-path coordinator.
- **Config flow step chaining:** New `async_step_tariff_comparison` appended after `async_step_ml_settings` in both initial and options flows, following the same pattern (call next step on submit, show form on None). Initial flow terminates in `_create_entry()`; options flow terminates in `_save_and_exit()`.
- **BooleanSelector / TextSelector:** HA's selector helpers require importing from `homeassistant.helpers.selector`. `TextSelectorConfig(multiline=True, type=TextSelectorType.TEXT)` is the correct way to produce a multi-line text area in HA config flows.
- **Cache key naming:** The tariff cache JSON uses `data_year` (YYYY-MM format, start of the rolling 12-month window) so cache invalidation is trivially checked against the current target year without parsing dates.
- **Atomic cache writes:** Always use `tempfile.mkstemp` + `os.replace` (same as D-4 ML model) for any JSON cache write in HA config directory to prevent partial-write corruption on HA restart.

## History

- 2026-04-10: ML sensor and const.py ML constants added. Created `sensors/ml_model_status.py` (MLModelStatusSensor), appended ML constants block to `const.py`, added `scikit-learn` to `manifest.json` requirements, exported `MLModelStatusSensor` from `sensors/__init__.py`, registered sensor conditionally in `sensor.py` when `ML_ENABLED` is True.
- 2026-04-10: ML wiring in config_flow, coordinators, power_calculator. Added `_ml_settings_schema` helper and `async_step_ml_settings` to both `BatteryChargCalculatorConfigFlow` (initial flow, calls `_create_entry`) and `BatteryChargCalculatorFlowHandler` (options flow, calls `_save_and_exit`). All terminal `_create_entry`/`_save_and_exit` call sites in both flow classes now route through `async_step_ml_settings` first (D-16). In `coordinators.py`: conditional `MLPowerEstimator` instantiation in `__init__` (only when `ML_ENABLED=True`, D-16), `async_start` in `_async_setup`, monthly retrain timer (`async_track_time_interval` 30 days, D-9), `_async_maybe_retrain_ml` method, `async_shutdown` cleanup for both timers and estimator, ML correction hook (D-1) in `octopus_state_change_listener` after physics estimate. In `power_calculator.py`: added `physics_estimate` alias of `from_temp_and_time` for explicit use by `MLPowerEstimator` during training data construction.
- 2026-04-16: Tariff comparison integration layer. Created `tariff_comparison/` package (`__init__.py` = TariffComparisonCoordinator, `client.py` = TariffComparisonClient, `calculator.py` = calculate_tariff_cost, `cache.py` = JSON disk cache, `open_meteo_historical.py` = OpenMeteoHistoricalClient). Created `sensors/tariff_comparison.py` (TariffComparisonSensor). Added `TARIFF_COMPARISON_*` constants to `const.py`. Wired coordinator into `coordinators.py` (tariff_coordinator stub + `async_setup_tariff_coordinator` module-level function). Called from `__init__.py:async_setup_entry` before platform forward setup. Added `refresh_tariff_comparison` service. Added `async_step_tariff_comparison` to both config flow classes. Added translation strings to `strings.json` and `translations/en.json`.
