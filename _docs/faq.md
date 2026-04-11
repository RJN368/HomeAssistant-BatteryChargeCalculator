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

## Machine Learning

### Do I need the ML service?

No. The ML service is entirely optional. Without it, the integration uses a physics-based demand model (heat loss + base load). The ML service improves accuracy of the demand forecast over time but is not required to use the integration.

### What does the ML service actually do?

It learns the difference between your home's real electricity consumption and the physics model's prediction, then applies that learned correction at forecast time. After enough data is collected (roughly 10 days), the model starts blending its correction into the demand estimate. With more than ~50 days of data, the ML correction carries full weight.

See [Machine Learning](machine-learning.md) for a detailed explanation.

### How long does training take?

Training typically completes in under 2 minutes even with 25,000+ data points. The service fetches historical data from GivEnergy or Octopus, cleans it, and fits the model in a background thread. Progress is visible in the **ML Model Status** sensor — it changes from `training` to `ready` when done.

### The ML Power Surface sensor shows "Surface data is missing"

This means the trained model was loaded from disk but the power surface has not been computed yet. This normally resolves itself automatically on the next container restart (the surface is computed as part of startup migration). If it persists:

1. Check the container logs for errors: `docker logs bcc-ml-service`
2. Trigger a fresh retrain from **Developer Tools → Services → `battery_charge_calculator.trigger_ml_training`**

### The ML Model Status shows `service_unreachable`

1. Confirm the container is running: `docker ps | grep bcc-ml-service`
2. Check the service URL in the integration settings — it must include the port, e.g. `https://192.168.1.50:8765`
3. Verify the TLS fingerprint in the integration settings matches what the container printed on startup (`docker logs bcc-ml-service | grep fingerprint`)
4. Ensure the machine running the container is reachable from Home Assistant on your LAN

### Can I use both GivEnergy and Octopus as consumption sources?

Yes. Setting `consumption_source = both` causes the training pipeline to use GivEnergy consumption as the primary series and Octopus grid import as an additional feature column. This gives the model more signal at the cost of requiring both data sources to be available.

### How do I update the ML service?

```bash
git pull
docker compose -f development/docker-compose.yml build bcc-ml-service
docker compose -f development/docker-compose.yml up -d --force-recreate bcc-ml-service
```

The trained model and credentials in `./config/bcc-ml-data/` are preserved.

---

## Credentials and security

### Where are my API keys stored?

Your Octopus Energy API key is stored in the Home Assistant config entry options, which are saved in the `home-assistant_v2.db` database on your Home Assistant instance. It is never sent anywhere other than the Octopus Energy API.

### What permissions does the Octopus API key need?

Read-only access to your account is sufficient. The integration only reads rates and account information.

### Does the integration need a GivEnergy API token?

No. The integration communicates with your GivEnergy inverter via **GivTCP over MQTT**. No GivEnergy API token is required. You only need your inverter serial number, which is used to construct the correct MQTT topics.

### Is the connection to the ML service secure?

Yes. The ML service uses HTTPS with a self-signed TLS certificate. Home Assistant verifies the certificate using its SHA-256 fingerprint (certificate pinning) rather than a certificate authority, which is secure on a local network without requiring a CA or domain name. All endpoints additionally require a Bearer token API key.

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
