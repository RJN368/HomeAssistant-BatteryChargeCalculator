
# Home Assistant Battery Charge Calculator

This Home Assistant addon connects to Octopus Energy to fetch import and export rates, uses weather data to estimate your home's heating load, adds your base load, and includes solar gain. It then calculates the optimal battery charge, discharge, and export schedule for the day, and automatically sends the schedule to your GivEnergy system.

## Features

- Connects to Octopus Energy to retrieve import/export rates
- Uses weather data to estimate heating load for your house
- Adds base load and solar gain to the calculation
- Calculates the best charge, discharge, and export schedule for your battery
- Automatically schedules updates in GivEnergy
- Optimizes for cost savings and energy efficiency
- **Monthly Tariff Comparison** — see what you would have paid on Agile, Go, Flux, or any other Octopus tariff over the last 12 months, using real smart meter data

## Machine Learning Power Estimation

The ML power estimation feature runs as a separate Docker container (`bcc-ml-service`), completely independent of Home Assistant's Python environment. This means there are no Python version constraints — it works with any HA version.

The ML service learns your household's energy consumption patterns from historical GivEnergy data and produces per-slot power demand forecasts alongside the physics-based estimates.

See the [setup documentation](https://rjn368.github.io/HomeAssistant-batterychargecalculator/setup/) for instructions on deploying the ML service.

## Axle VPP Awareness

Axle awareness is an opt-in safety layer for dispatch control. When enabled, the coordinator checks Axle remote-control windows and suppresses local inverter dispatch (`enableCharge` / `enableExport`) while a window is active.

Key options:
- `axle_enabled`: turns Axle awareness on/off (default off)
- `axle_api_token`: bearer token used to fetch Axle event data
- `axle_poll_interval_seconds`: Axle poll cadence
- `axle_request_timeout_seconds`: request timeout for Axle API calls
- `axle_fail_safe_mode`: behavior when Axle source is unavailable (`open` or `closed`)
- `axle_neutralize_on_active_entry`: on window entry, send one-time neutralize (`disableCharge` + `disableExport`)

Fail-safe behavior:
- `open` (default): if Axle source is unavailable, local schedule dispatch continues
- `closed`: if Axle source is unavailable, dispatch is suppressed for safety
- If source is stale but cached windows still overlap now, dispatch remains suppressed

Diagnostic entity:
- `sensor.axle_remote_control` reports `active`, `inactive`, or `unavailable`
- Key attributes include `source_status`, `suppression_reason`, `last_transition_reason`, `last_error`, `active_window_start`, `active_window_end`, `cache_age_seconds`, `fail_safe_mode`, `neutralize_on_active_entry`, `poll_interval_seconds`, and `request_timeout_seconds`


## Installation

You can install this integration via [HACS](https://github.com/hacs/integration) or manually:

### HACS

1. Add this repository to HACS as a custom integration
2. Click `Download` in HACS

### Manual

1. Download the latest [release](https://github.com/rjn368/HomeAssistant-BatteryChargeCalculator/releases)
2. Copy the contents of `custom_components` into your Home Assistant `<config directory>/custom_components` folder
3. Restart Home Assistant

## Setup

After installation, follow the setup instructions in the [documentation](https://rjn368.github.io/HomeAssistant-batterychargecalculator/) to connect your Octopus Energy account and configure your GivEnergy system.

## Documentation

Full documentation is available at: [https://rjn368.github.io/HomeAssistant-batterychargecalculator/](https://rjn368.github.io/HomeAssistant-batterychargecalculator/)

## FAQ & Support

For frequently asked questions and support, please refer to the [docs FAQ section](https://rjn368.github.io/HomeAssistant-batterychargecalculator/faq/) or open an issue on GitHub.

## Sponsorship

If you find this addon useful, consider [sponsoring the developer](https://github.com/sponsors/rjn368)!

## FAQ

Before raising anything, please read through the [faq](https://rjn368.github.io/HomeAssistant-batterychargecalculator/faq). If you have questions, then you can raise a [discussion](https://github.com/rjn368/HomeAssistant-BatteryChargeCalculator/discussions). If you have found a bug or have a feature request please [raise it](https://github.com/rjn368/HomeAssistant-BatteryChargeCalculator/issues) using the appropriate report template.
