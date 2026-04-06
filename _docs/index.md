# Home Assistant Battery Charge Calculator

The Battery Charge Calculator is a Home Assistant custom integration that automatically optimises your home battery's charge, discharge, and export schedule to minimise energy costs.

## How it works

1. **Fetches Octopus Energy import and export rates** for the current and next day
2. **Retrieves weather forecast data** to estimate the heating load of your house
3. **Adds your home's base load** and **solar gain** to build a full energy picture
4. **Runs a genetic algorithm** to calculate the optimal battery charge, discharge, and export shape for the day
5. **Automatically sends the schedule to GivEnergy** so your battery acts on it without manual intervention

## Requirements

- A [GivEnergy](https://givenergy.co.uk) battery system with [GivTCP](https://github.com/GivEnergy/giv_tcp) and an MQTT broker configured
- An [Octopus Energy](https://octopus.energy) account on an agile or time-of-use tariff (import and/or export)
- Home Assistant with the integration installed

## Quick start

See the [Setup guide](setup.md) to connect your accounts and get started.
