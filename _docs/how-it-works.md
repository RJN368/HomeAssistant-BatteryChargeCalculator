# How it works

The Battery Charge Calculator runs a daily optimisation cycle to find the most cost-effective way to use your home battery. Here is a step-by-step explanation of what happens.

---

## 1. Fetch Octopus Energy rates

The integration connects to the Octopus Energy API and retrieves the half-hourly **import rates** and **export rates** for the current and next day. These rates are the primary driver of the optimisation — the goal is to charge cheaply and export or discharge when rates are high.

---

## 2. Calculate energy demand

The integration builds a demand forecast for the day by combining three components:

### Heating load
Weather forecast data is used to estimate how much energy your home needs for heating. On cold days the heating load is higher; on warm days it will be lower or zero. The calculation takes into account outdoor temperature and a model of your home's thermal behaviour.

### Base load
Your home's baseline electricity consumption (appliances, lighting, hot water, etc.) is added as a constant or time-varying load on top of the heating load.

### Solar gain
If you have solar panels, the expected solar generation for the day is subtracted from the demand. Solar gain is estimated from the weather forecast (irradiance/cloud cover).

The result is a net demand profile — a half-hourly forecast of how much energy your home will need from the battery or grid at each point in the day.

---

## 3. Run the genetic algorithm

A **genetic algorithm** searches for the optimal battery schedule. It evolves a population of candidate schedules over multiple generations, selecting and combining the best-performing ones.

Each candidate schedule defines, for every half-hour slot:
- Whether to **charge** the battery (from the grid)
- Whether to **discharge** the battery (to meet home demand)
- Whether to **export** to the grid

The algorithm evaluates each schedule by simulating the battery state through the day, calculating the total grid import cost minus export earnings, subject to battery capacity and charge rate constraints. The schedule with the lowest net cost wins.

---

## 4. Send the schedule to GivEnergy via MQTT

Once the best schedule is found, it is sent to your GivEnergy inverter via **MQTT** using [GivTCP](https://github.com/GivEnergy/giv_tcp). The integration publishes charge, discharge, and export commands to GivTCP MQTT topics addressed by your inverter serial number. GivTCP then configures the inverter directly, so no manual intervention is required and no GivEnergy API token is needed.

If **Simulate Only** mode is enabled in the integration settings, the schedule is calculated and exposed as sensor data in Home Assistant but is **not** sent to GivEnergy. This is useful for testing and monitoring without affecting your battery.

---

## 5. Schedule refresh

The integration checks every hour whether the current plan should be replaced. Checking happens on a fixed hourly schedule, but the plan is **only recalculated** when at least one of the following conditions is true:

- **No plan exists yet** — a fresh plan is always generated on startup.
- **Battery level has drifted** — the actual battery state of charge (read from GivTCP via MQTT) differs from the level that was projected by the plan by more than **10 % of the battery's maximum capacity**. A larger-than-expected deviation means real-world conditions (solar generation, heating demand, etc.) have diverged from the forecast, and a new plan is likely to perform better.
- **Plan is nearly exhausted** — fewer than **2 hours** remain on the current plan. Because Octopus Agile rates for the following period may now be available, re-planning ensures the schedule extends far enough into the future.

When none of these conditions is met, the existing plan is left in place and the inverter continues to follow it unchanged. This avoids unnecessary recalculations and prevents the inverter schedule from being disrupted while conditions are still on track.
