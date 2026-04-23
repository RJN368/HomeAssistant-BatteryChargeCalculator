# Setup

## Installation

### HACS (recommended)

1. Open HACS in your Home Assistant instance
2. Go to **Integrations** and click the menu in the top-right corner
3. Select **Custom repositories** and add:
   - **Repository:** `https://github.com/RJN368/HomeAssistant-BatteryChargeCalculator`
   - **Category:** Integration
4. Find **Battery Charge Calculator** in HACS and click **Download**
5. Restart Home Assistant

### Manual

1. Download the latest [release](https://github.com/RJN368/HomeAssistant-BatteryChargeCalculator/releases)
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

---

## Monthly Tariff Comparison (optional)

The Monthly Tariff Comparison feature shows you what your electricity would have cost over the last 12 months under different Octopus tariffs, using your real smart meter data.

To enable it, go to **Settings → Devices & Services → Battery Charge Calculator → Configure** and proceed to the **Monthly Tariff Comparison** step:

1. Toggle **Enable Monthly Tariff Comparison** on
2. On the next screen, a list of available Octopus tariffs is fetched automatically — your current tariff is pre-selected
3. Tick the tariffs you want to compare and click **Submit**

A new sensor `sensor.monthly_tariff_comparison` will be created. See [Monthly Tariff Comparison](tariff-comparison-guide.md) for dashboard setup and a full explanation of the results.

---

## ML Service setup (optional)

The ML service is a standalone Docker container that learns your home's real consumption patterns and improves power demand forecasts over time. It is entirely optional — the integration works fully without it.

### Prerequisites

- Docker installed on a machine on the same local network as Home Assistant
- The machine must be reachable by Home Assistant on your LAN (a Raspberry Pi, NAS, or the same host running HA all work)

### Step 1 — Get the container image onto your host

Each release automatically publishes a pre-built image to the GitHub Container Registry (GHCR). You can pull it directly, build from source, or transfer a locally-built image — whichever suits your setup.

#### Option A — Pull from GHCR (recommended)

The easiest option on any machine with internet access:

```bash
docker pull ghcr.io/rjn368/bcc-ml-service:latest
```

To pin to a specific release version (recommended for production):

```bash
docker pull ghcr.io/rjn368/bcc-ml-service:1.2.3
```

**Subsequent updates:**

```bash
docker pull ghcr.io/rjn368/bcc-ml-service:latest
docker compose up -d --force-recreate bcc-ml-service
```

The trained model in the `bcc-ml-data` volume is preserved across updates.

#### Option B — Build directly on the target machine

Clone the repo onto the machine where Docker is running, then build there:

```bash
git clone https://github.com/RJN368/HomeAssistant-BatteryChargeCalculator.git
cd HomeAssistant-BatteryChargeCalculator/ml-service
docker compose build bcc-ml-service
docker compose up -d bcc-ml-service
```

**Subsequent updates** — pull the latest code and rebuild in place:

```bash
cd HomeAssistant-BatteryChargeCalculator/ml-service
git pull
docker compose build bcc-ml-service
docker compose up -d --force-recreate bcc-ml-service
```

#### Option C — Build on your dev machine, ship as a tar file

If the HA host has no internet access, build the image locally and transfer it.

**Step-by-step via a file:**

```bash
# 1. Build on your dev machine
cd ml-service
docker compose build bcc-ml-service

# 2. Export to a compressed tar
docker save bcc-ml-service | gzip > bcc-ml-service.tar.gz

# 3. Copy to the HA host
scp bcc-ml-service.tar.gz user@ha-host:~

# 4. On the HA host — load the image
docker load < bcc-ml-service.tar.gz
```

**One-shot via SSH (no intermediate file):**

```bash
docker save bcc-ml-service | ssh user@ha-host docker load
```

After loading, copy `docker-compose.yml` to the HA host and continue from Step 2 below.

### Step 2 — Create the Docker network (first time only)

The service joins the same Docker network as your other HA-adjacent containers:

```bash
docker network create --subnet=172.19.0.0/24 ha-network
```

If the network already exists you can skip this step.

### Step 3 — Start the service

```bash
docker compose up -d bcc-ml-service
```

### Step 4 — Retrieve the API key and TLS fingerprint

On first start the service generates a random API key and a self-signed TLS certificate. Both are printed to the container log:

```bash
docker logs bcc-ml-service
```

Look for lines like:

```
[BCC ML Service] Generated API key: 3f8a2c…
[BCC ML Service] TLS certificate SHA-256 fingerprint: 4A:3B:…
```

Copy both values — you will need them in the next step.

### Step 5 — Enable ML in Home Assistant

1. Go to **Settings → Devices & Services → Battery Charge Calculator → Configure**
2. Scroll to the **Machine Learning** section and enable it
3. Enter the service URL (e.g. `https://192.168.1.50:8765`)
4. Paste the **API key** from the container log
5. Paste the **TLS fingerprint** from the container log (enables secure certificate pinning without a CA)
6. Click **Submit**

Home Assistant will immediately connect to the service, send your configuration, and queue the first training run.

!!! tip "Training takes a few minutes"
    The first training run fetches up to 90 days of historical consumption data from GivEnergy or Octopus. Check progress in **Settings → Devices & Services → Battery Charge Calculator** — the **ML Model Status** sensor will show `ready` when training completes.



