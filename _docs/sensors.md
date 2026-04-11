# Sensors

The integration creates the following sensors in Home Assistant:

---

## Battery Charge Projection

**Entity:** `sensor.battery_charge_projection`

Provides a forward-looking view of the calculated battery state over the scheduled time slots. The sensor's primary value is the projected initial battery power (kWh) for the first upcoming slot.

The sensor also exposes detailed attributes containing all scheduled time slots, including:

- `start_datetime` — start time of the slot
- `end_datetime` — end time of the slot
- `action` — charge, discharge, or export
- `power` — power level for the slot (kW)
- `cost` — estimated cost or saving for the slot (£)
- `initial_power` — battery charge level at the start of the slot (kWh)

**Unit:** kWh
**Device class:** Energy

---

## Cost Prediction

**Entity:** `sensor.cost_prediction`

Shows the estimated total energy cost for the current schedule, based on the Octopus Energy import/export rates and the calculated battery schedule.

**Unit:** £ (GBP)

---

## Battery Charge Slots

**Entity:** `sensor.battery_charge_slots`

Represents the currently active or next upcoming charge/discharge slot as determined by the schedule. Useful for automations and dashboards to show what the battery is doing right now.

---

## ML sensors

The following sensors are only created when **ML is enabled** in the integration settings.

### ML Model Status

**Entity:** `sensor.ml_model_status`

Reports the current state of the ML service and the trained model.

**State values:**

| State | Meaning |
|---|---|
| `not_configured` | ML enabled but service URL or API key not set |
| `configured` | Service reachable, no model trained yet |
| `training` | Training run in progress |
| `ready` | Model trained and available |
| `service_unreachable` | HA cannot reach the ML service |

**Attributes:**

| Attribute | Description |
|---|---|
| `is_ready` | `true` when a trained model is available |
| `is_training` | `true` while a training run is in progress |
| `model_trained_at` | ISO-8601 UTC timestamp of the last successful training run |
| `model_n_training_samples` | Number of clean half-hour slots used for training |
| `model_val_rmse` | Validation RMSE (kWh/slot) from the last training run |
| `model_type` | `hist_gbr` (HistGradientBoostingRegressor) or `ridge` |
| `blend_weight` | Current ML blend weight (0 = pure physics, 1 = full ML) |
| `doy_daily_kwh` | 366-entry list of mean daily consumption by day-of-year |

---

### ML Power Surface

**Entity:** `sensor.ml_power_surface`

**State:** Total number of cells in the power surface (normally 832 = 16 temperatures × 52 weeks). A non-zero value confirms the surface has been computed.

Exposes the learned week × temperature → daily-kWh power surface as sensor attributes. The surface shows how the model predicts your home's total daily electricity consumption across the full range of temperatures and seasons, based on your actual historical data.

**Attributes:**

| Attribute | Description |
|---|---|
| `temps` | List of 16 temperature values (−10 °C to +20 °C in 2 °C steps) |
| `weeks` | List of 52 ISO week numbers (1–52) |
| `z` | 52 × 16 matrix of blended (ML + physics) daily kWh predictions |
| `z_physics` | 52 × 16 matrix of physics-only daily kWh predictions (when a physics model is configured) |
| `blend_weight` | ML blend weight at the time the surface was computed |
| `generated_at` | ISO-8601 UTC timestamp of the training run that produced this surface |

**Lovelace 3-D chart** (requires the [Plotly Graph Card](https://github.com/dbuezas/lovelace-plotly-graph-card)):

```yaml
type: custom:plotly-graph
title: ML power surface — week × temperature
raw_plotly_config: true
layout:
  scene:
    xaxis:
      title: Temperature (°C)
    yaxis:
      title: Week of year
    zaxis:
      title: Daily kWh
  showlegend: true
config:
  displayModeBar: false
entities:
  - entity: sensor.ml_power_surface
    show_value: false
data:
  - type: surface
    name: ML blended
    colorscale: Blues
    x: $ex hass.states['sensor.ml_power_surface'].attributes.temps
    y: $ex hass.states['sensor.ml_power_surface'].attributes.weeks
    z: $ex hass.states['sensor.ml_power_surface'].attributes.z
  - type: surface
    name: Physics only
    colorscale: Oranges
    opacity: 0.5
    x: $ex hass.states['sensor.ml_power_surface'].attributes.temps
    y: $ex hass.states['sensor.ml_power_surface'].attributes.weeks
    z: $ex hass.states['sensor.ml_power_surface'].attributes.z_physics
```

---

### ML Annual Forecast

**Entity:** `sensor.ml_annual_forecast`

Shows the model's predicted annual electricity consumption profile by day of year, as a 366-entry list stored in sensor attributes. Useful for building a year-at-a-glance bar chart showing expected seasonal variation in your energy use.

---

## Using the sensors in dashboards

You can use these sensors in Home Assistant dashboards with the **entities card** or **ApexCharts card** for a visual battery schedule overview. The projection sensor's attributes contain the full timeslot data which can be templated or graphed.

### Example template — next action

```yaml
{{ state_attr('sensor.battery_charge_slots', 'action') }}
```
