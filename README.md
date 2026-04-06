
# Home Assistant Battery Charge Calculator

This Home Assistant addon connects to Octopus Energy to fetch import and export rates, uses weather data to estimate your home's heating load, adds your base load, and includes solar gain. It then calculates the optimal battery charge, discharge, and export schedule for the day, and automatically sends the schedule to your GivEnergy system.

## Features

- Connects to Octopus Energy to retrieve import/export rates
- Uses weather data to estimate heating load for your house
- Adds base load and solar gain to the calculation
- Calculates the best charge, discharge, and export schedule for your battery
- Automatically schedules updates in GivEnergy
- Optimizes for cost savings and energy efficiency

## Installation

You can install this integration via [HACS](https://github.com/hacs/integration) or manually:

### HACS

1. Add this repository to HACS as a custom integration
2. Click `Download` in HACS

### Manual

1. Download the latest [release](https://github.com/rjn368/HomeAsssitant-BatteryChargeCalculator/releases)
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

Before raising anything, please read through the [faq](https://rjn368.github.io/HomeAssistant-batterychargecalculator/faq). If you have questions, then you can raise a [discussion](https://github.com/rjn368/HomeAsssitant-BatteryChargeCalculator/discussions). If you have found a bug or have a feature request please [raise it](https://github.com/rjn368/HomeAsssitant-BatteryChargeCalculator/issues) using the appropriate report template.
