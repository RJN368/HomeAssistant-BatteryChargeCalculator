# Machine Learning

The ML service is an optional Docker container that learns your home's actual electricity consumption patterns from historical data and uses them to improve the power demand forecast fed into the battery optimiser.

Without the ML service, demand is estimated entirely from a physics model (heat loss calculation + base load). With the ML service, a trained machine-learning model corrects the physics estimate using real observations, producing a more accurate half-hourly demand profile, particularly for unusual consumption patterns the physics model cannot anticipate.

---

## Architecture overview

```
┌──────────────────────────────┐      HTTPS       ┌────────────────────────┐
│   Home Assistant             │ ◄──────────────► │   BCC ML Service       │
│   battery_charge_calculator  │   POST /configure │   (Docker container)   │
│                              │   POST /predict   │                        │
│   MLServiceClient            │   POST /retrain   │   FastAPI + uvicorn    │
│   (thin aiohttp client)      │   GET  /status    │   port 8765 (HTTPS)    │
└──────────────────────────────┘                   └────────────────────────┘
                                                           │
                                                    /data/model.pkl
                                                    /data/api_key
                                                    /data/certs/
```

The HA integration and the ML service communicate over HTTPS on your local network. The service uses a self-signed TLS certificate — HA verifies it using the SHA-256 fingerprint you paste into the configuration (certificate pinning), so no certificate authority is needed.

---

## Data sources

Before training, the service fetches up to 90 days of historical data from three sources:

| Source | What it provides | API |
|---|---|---|
| **GivEnergy Cloud** | Half-hourly household consumption (kWh/slot) | GivEnergy Cloud API |
| **Octopus Energy** | Half-hourly grid import (kWh/slot) | Octopus Energy API |
| **Open-Meteo** | Outdoor temperature history (°C, 30-min) | open-meteo.com (free, no key) |

You choose whether to use GivEnergy, Octopus, or both as the consumption source in the integration settings (`consumption_source`). Temperature always comes from Open-Meteo, using your Home Assistant location coordinates.

---

## Training pipeline

Training runs automatically on the first start and every 30 days. You can trigger it manually via **Developer Tools → Services → `battery_charge_calculator.trigger_ml_training`**.

### Stage 1 — Data alignment

All series are resampled to a 30-minute UTC grid and inner-joined on the time index. Timestamps with missing data in any mandatory series are dropped.

### Stage 2 — Anomaly removal

The pipeline removes slots that would corrupt the model:

- **Zero / NaN readings** where the physics model predicts meaningful heating demand (> 0.2 kWh/slot) — these indicate sensor outages, not genuine zero consumption
- **Outliers** above 20 kWh per 30-minute slot (exceeds a typical residential fuse limit)
- **EV and large-load blocks** — sustained high-power slots (> 1.5 kWh) are examined for temperature correlation. Slots with a heating signature (power anti-correlated with outdoor temperature) are kept; EV-like flat loads that are temperature-independent are excluded
- **Frozen-sensor runs** — 6 or more consecutive identical non-zero readings indicate a stuck sensor and are removed
- **Residual z-score fencing** — per time-of-day, slots more than 3.5 standard deviations from the mean residual are excluded

### Stage 3 — Quality gate

Training is rejected if:
- Fewer than 500 clean half-hour slots remain (roughly 10 days of data)
- The observed temperature range is less than 5 °C — insufficient range to fit a meaningful temperature response

### Stage 4 — Feature engineering

For each clean slot, 14–15 features are computed:

| Feature | Description |
|---|---|
| `outdoor_temp_c` | Outdoor temperature at the slot time |
| `physics_kwh` | Physics model prediction for the slot |
| `hour_sin`, `hour_cos` | Time of day (cyclic encoding) |
| `dow_sin`, `dow_cos` | Day of week (cyclic encoding) |
| `doy_sin`, `doy_cos` | Day of year / seasonal position (cyclic encoding) |
| `is_weekend` | 1.0 on Saturday / Sunday |
| `slot_index` | Slot number 0–47 (half-hour index within the day) |
| `temp_delta_1slot` | Temperature change since the previous half-hour |
| `temp_delta_24h` | Temperature change since the same time yesterday |
| `rolling_mean_6h` | 6-hour rolling mean of outdoor temperature |
| `physics_kwh_sq` | Square of the physics prediction (non-linear heating term) |
| `octopus_import_kwh` | *(when source = "both")* Grid import from Octopus |

### Stage 5 — Model training

The model is chosen based on the number of clean training samples:

| Samples | Model | Rationale |
|---|---|---|
| ≥ 500 | `HistGradientBoostingRegressor` (scikit-learn) | Native NaN handling; strong on tabular data; fast to train |
| < 500 | `Ridge` regression (mean-imputation pipeline) | Stable with limited data; less likely to overfit |

The model is trained on an 85/15 train/validation split. The validation RMSE (root mean squared error in kWh/slot) is recorded and used as the correction cap at inference time.

The model learns the **residual** — the difference between what actually happened and what the physics model predicted:

$$\delta = \text{actual\_kwh} - \text{physics\_kwh}$$

---

## Inference (prediction)

At planning time, for each half-hour slot the HA integration sends:

- The slot time
- The outdoor temperature forecast
- The physics model prediction

The service returns a **blended corrected kWh** value:

$$\hat{y} = \hat{y}_\text{physics} + w_\text{ML} \cdot \hat{\delta}_\text{ML}$$

where the **blend weight** $w_\text{ML}$ ramps linearly from 0 to 1 as training sample count grows from 500 to 2,500:

| Samples | Blend weight | Behaviour |
|---|---|---|
| < 500 | 0.0 | Pure physics — ML not yet trusted |
| 500 – 2,500 | 0.0 → 1.0 | Gradual transition |
| > 2,500 | 1.0 | Full ML correction |

The ML correction is also **capped at ±2 × training RMSE** to prevent wild extrapolation on slot conditions the model has never seen during training.

---

## Power surface

After training, the service computes a **power surface** — a 52 × 16 matrix of predicted daily kWh values sweeping every combination of:

- **Week of year** (ISO weeks 1–52, representing seasons)
- **Outdoor temperature** (−10 °C to +20 °C in 2 °C steps, 16 points)

Each cell is the blended model prediction for a representative Wednesday summed across all 48 half-hour slots of that day. The surface is exposed via the **ML Power Surface** diagnostic sensor and can be plotted as a 3-D Lovelace card to visualise the model's learned seasonal and temperature response.

---

## Model persistence

The trained model is saved to `/data/model.pkl` inside the Docker container (mapped to `./config/bcc-ml-data/` on the host). It survives container restarts and rebuilds. On restart, if an older model is loaded that was saved before the power surface feature existed, the surface is computed automatically on startup without requiring a full retrain.

---

## Security

- **TLS** — the service generates a self-signed RSA 2048 certificate on first start, valid for 10 years. HA verifies it using the SHA-256 fingerprint, not a CA — this is secure against man-in-the-middle on your LAN without needing a certificate authority.
- **API key** — a 64-character random hex API key is generated on first start and stored at `/data/api_key`. All endpoints require a `Bearer` token matching this key.
- **Secrets** — GivEnergy and Octopus credentials are only held in memory during training and never written to disk by the ML service.

---

## Diagnostic sensors

The ML service exposes four diagnostic sensors in Home Assistant. See [Sensors](sensors.md#ml-sensors) for details.
