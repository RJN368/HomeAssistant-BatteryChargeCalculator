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

## Using the sensors in dashboards

You can use these sensors in Home Assistant dashboards with the **entities card** or **ApexCharts card** for a visual battery schedule overview. The projection sensor's attributes contain the full timeslot data which can be templated or graphed.

### Example template — next action

```yaml
{{ state_attr('sensor.battery_charge_slots', 'action') }}
```
