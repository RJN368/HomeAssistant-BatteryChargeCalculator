# Setup

## Installation

### HACS (recommended)

1. Open HACS in your Home Assistant instance
2. Go to **Integrations** and click the menu in the top-right corner
3. Select **Custom repositories** and add:
   - **Repository:** `https://github.com/RJN368/HomeAsssitant-BatteryChargeCalculator`
   - **Category:** Integration
4. Find **Battery Charge Calculator** in HACS and click **Download**
5. Restart Home Assistant

### Manual

1. Download the latest [release](https://github.com/RJN368/HomeAsssitant-BatteryChargeCalculator/releases)
2. Copy the `custom_components/battery_charge_calculator` folder into your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

---

## Configuration

After installation, add the integration via the Home Assistant UI:

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Battery Charge Calculator**
3. Fill in the required fields:

| Field | Description |
|---|---|
| **GivEnergy Serial Number** | The serial number of your GivEnergy inverter (used to address MQTT topics via GivTCP) |
| **Octopus Account Number** | Your Octopus Energy account number (e.g. `A-XXXXXXXX`) |
| **Octopus API Key** | Your Octopus Energy API key (from [octopus.energy/dashboard](https://octopus.energy/dashboard)) |
| **Simulate Only** | If enabled, the schedule is calculated but **not** sent to GivEnergy — useful for testing |

!!! note "GivTCP and MQTT required"
    This integration communicates with your GivEnergy inverter via **GivTCP** over **MQTT** — no GivEnergy API token is needed. You must have [GivTCP](https://github.com/GivEnergy/giv_tcp) running and an MQTT broker (e.g. Mosquitto) set up and connected to Home Assistant before adding this integration.

---

## Getting your API credentials

### GivEnergy

No API token is required. The integration communicates with your inverter via **GivTCP** over **MQTT**.

1. Install and configure [GivTCP](https://github.com/GivEnergy/giv_tcp) (available as a Home Assistant add-on or Docker container)
2. Ensure GivTCP is connected to an MQTT broker (e.g. the Mosquitto add-on in Home Assistant)
3. Your inverter serial number is shown in the GivTCP configuration or on the GivEnergy portal under **My Devices**

### Octopus Energy

1. Log in to [octopus.energy](https://octopus.energy)
2. Go to **Account → API Access** to find your API key and account number
