# FAQ

## General

### Does this integration work with any energy supplier?

No. The integration currently requires an **Octopus Energy** account, as it uses the Octopus Energy API to retrieve half-hourly import and export rates. It is intended for UK customers on agile or time-of-use tariffs.

### Does it work with any battery?

The integration is built specifically for **GivEnergy** battery systems. It uses the GivEnergy API to push the calculated charge/discharge schedule to your inverter.

### Does it work without solar panels?

Yes. Solar gain is one input to the calculation, but if you do not have solar panels the integration will simply use a solar gain of zero. The optimisation will still work using the heating load, base load, and Octopus rates.

---

## Schedule and optimisation

### How often does the schedule update?

The schedule recalculates daily and also updates when new rate data or forecast data becomes available. The coordinator uses debouncing to avoid excessive recalculations.

### What does "Simulate Only" mode do?

When **Simulate Only** is enabled, the integration calculates the optimal schedule and exposes it as Home Assistant sensor data, but does **not** send the schedule to GivEnergy. This is useful for:

- Testing the integration before going live
- Monitoring what the algorithm would do without committing to it
- Debugging unexpected schedule decisions

### Can I override the schedule manually?

Yes. You can use the GivEnergy app or portal to override individual charge/discharge slots at any time. The integration will push a new schedule on its next cycle, so manual overrides will be replaced at the next update.

---

## Credentials and security

### Where are my API keys stored?

Your Octopus Energy API key is stored in the Home Assistant config entry options, which are saved in the `home-assistant_v2.db` database on your Home Assistant instance. It is never sent anywhere other than the Octopus Energy API.

### What permissions does the Octopus API key need?

Read-only access to your account is sufficient. The integration only reads rates and account information.

### Does the integration need a GivEnergy API token?

No. The integration communicates with your GivEnergy inverter via **GivTCP over MQTT**. No GivEnergy API token is required. You only need your inverter serial number, which is used to construct the correct MQTT topics.

---

## Troubleshooting

### The integration is not sending schedules to GivEnergy

1. Check that **Simulate Only** is not enabled in the integration settings
2. Verify that GivTCP is running and connected to your MQTT broker
3. Confirm the MQTT integration is set up in Home Assistant and the broker is reachable
4. Check that the inverter serial number in the integration settings matches the one in GivTCP
5. Check the Home Assistant logs for errors from `battery_charge_calculator`

### The sensors show `unavailable`

This usually means the coordinator has not yet completed its first successful data fetch. Check your Octopus API key and account number, and review the Home Assistant logs for connection errors.

### Where can I get help?

Open an issue on the [GitHub repository](https://github.com/RJN368/HomeAsssitant-BatteryChargeCalculator/issues).
