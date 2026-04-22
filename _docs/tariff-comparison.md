# Monthly Tariff Comparison Visualisation — Feature Specification

**Status:** Approved by robert.nash (2026-04-13) — ready for implementation
**Author:** Keaton (Lead Architect)
**Date:** 2026-04-13
**Implements:** Feature request by robert.nash

---

## Table of Contents

1. [Feature Overview](#1-feature-overview)
2. [User Stories](#2-user-stories)
3. [Data Sources](#3-data-sources)
4. [Tariff Configuration](#4-tariff-configuration)
5. [Architecture](#5-architecture)
6. [Cost Calculation Logic](#6-cost-calculation-logic)
7. [Sensor Schema](#7-sensor-schema)
8. [Lovelace Visualisation](#8-lovelace-visualisation)
9. [Out of Scope](#9-out-of-scope)
10. [Open Questions](#10-open-questions)

---

## 1. Feature Overview

The Monthly Tariff Comparison Visualisation allows Robert to see what his electricity costs **would have been** over the previous 12 months under different Octopus Energy import and export tariff combinations, using **real half-hourly meter consumption data** from the Octopus API.

Costs are calculated retrospectively: actual recorded grid import and export readings are replayed against the historical unit rates of each configured tariff. The result — a monthly breakdown per tariff — is exposed as a Home Assistant sensor with rich `extra_state_attributes`, designed for rendering as a stacked or grouped bar chart in Lovelace using the [ApexCharts card](https://github.com/RomRider/apexcharts-card).

**Why this matters:** UK electricity tariffs vary significantly. Agile Octopus, Intelligent Octopus Go, and standard variable tariffs have radically different unit rate profiles. A household with a solar + battery system may save hundreds of pounds per year by choosing the optimal tariff — but verifying this requires replaying 17,520 half-hourly readings against each tariff's full year of rates. This feature automates that entirely.

---

## ⚠️ Consumption Data Validity — The Tariff Optimisation Problem

> **Design issue raised by robert.nash — 2026-04-13**

The "replay actual meter reads against each tariff" approach described in §1 has a fundamental validity problem when the user's battery system is actively optimised for the current tariff.

### The problem

The battery charge/discharge/export schedule is recalculated **every 30 minutes** by `GeneticEvaluator.run()` specifically for the active tariff's rate profile. On Agile, this means the system charges during the cheapest half-hour slots (often 2–5 p/kWh at 2–4 am) and exports during the most expensive slots (30–50 p/kWh at peak). The Octopus import and export meter reads therefore reflect **Agile-optimised behaviour**.

If those same meter reads are replayed against Octopus Go rates (e.g. 7 p/kWh during the fixed 23:30–05:30 overnight window, 24 p/kWh flat outside), the comparison is invalid:

- On Go, the system would have charged during the **fixed overnight window** — not Agile's dynamic cheap slots, which may fall at different times or overlap only partially.
- Discharge and export timing would be different because the rate incentive profile differs.
- Result: replaying Agile-optimised meter reads against Go rates **misrepresents what Go would actually cost**, biasing the comparison in favour of the current tariff.

> *Robert's words (2026-04-13):*
> "When comparing tariffs we want the real energy use but ironically this will be polluted by the fact that the system is tuned to be optimised for the tariff that it is on. Therefore we may need to use predicted use for the non active tariff.
> ie comparing last years agile with charge, discharge and export periods with Octopus Go would not be accurate as the charge, discharge and export periods would be different."

### Three viable approaches

| Approach | Description | Accuracy | Complexity |
|---|---|---|---|
| **A — Simulation** | For each non-current tariff: fetch that day's historical rates + Open-Meteo weather → run `PowerCalculator.calculate()` → run `GeneticEvaluator.run()` → derive simulated grid import/export → apply tariff rates | High | High (365 × genetic evaluator runs per non-current tariff) |
| **B — Base-load isolation** | Decompose meter reads into tariff-independent base load + battery arbitrage component; re-simulate only the arbitrage for each non-current tariff's rates | Medium | Medium (requires estimating base load from combined meter + battery history data) |
| **C — Naive replay with disclosure** | Use real meter reads for all tariffs; prominently flag that non-current tariff costs reflect current-tariff-optimised behaviour | Low (but transparent) | Minimal |

**Selected approach (confirmed by robert.nash, 2026-04-13): A + C combined.** Approach C (naive replay with disclosure) delivers immediate results from day one. Approach A (full `GeneticEvaluator` simulation using Open-Meteo historical weather) runs as a background task and progressively replaces naive replay data per non-current tariff as simulation completes. See [§6.7](#67-consumption-data-model) for the full pipeline and state-transition model.

---

## 2. User Stories

### US-1 — Baseline tariff comparison
> As Robert, I want to see what my actual import and export costs would have been over the last 12 months under several different Octopus tariff combinations, broken down monthly, so I can make an evidence-based decision about switching.

### US-2 — Current tariff as baseline
> As Robert, I want my currently active import and export tariffs to always appear as the first entry in the comparison (labelled "Current"), so I have a clear baseline to compare alternatives against.

### US-3 — Export tariff effect
> As Robert, I want to include different export tariff options in the comparison — for example comparing Agile Outgoing against Outgoing Fixed — so I can see whether switching export tariff changes the net picture materially.

### US-4 — Seasonal insight
> As Robert, I want to see the comparison broken down by calendar month so I can identify whether Agile is worse in winter (high volatile prices) and better in summer (cheap off-peak charging), or vice versa.

### US-5 — Add a new tariff without restarting HA
> As Robert, I want to be able to add a new tariff code to the comparison list via the options flow at any time, and see the updated results on my dashboard without needing to restart Home Assistant.

### US-6 — On-demand refresh
> As Robert, I want a Home Assistant service call (`battery_charge_calculator.refresh_tariff_comparison`) that triggers an immediate fresh data fetch and recalculation, rather than waiting for the next scheduled refresh.

### US-7 — Data validity disclosure
> As Robert, I want the sensor to clearly indicate whether non-current tariff costs are based on real meter reads, simulated schedules, or base-load isolation — and where those costs may be unreliable due to tariff-optimised consumption — so I know how much to trust the comparison and can make an informed tariff switching decision.

---

## 3. Data Sources

### 3.7 Tariff Start Date Adjustment (Fair Comparison Window)

When comparing tariffs, if the current tariff started after the beginning of the analysis period, the comparison window is adjusted:

- The start date for all tariff calculations is set to the later of the default window start and the current tariff's `valid_from` date (agreement start).
- This ensures that all tariffs are compared fairly, only over the period when the current tariff was actually active.
- Example: If the analysis window is 1 March–31 March 2026, but the current tariff started on 17 March 2026, the comparison for all tariffs will use 17 March–31 March 2026.

This logic is implemented in the coordinator and applies to all tariff calculations and reporting.

All data is fetched from the Octopus Energy REST API. Existing API credentials (`OCTOPUS_APIKEY`, `OCTOPUS_ACCOUNT_NUMBER`, `OCTOPUS_MPN`, `OCTOPUS_EXPORT_MPN`) are reused from the existing config entry — **no new credential fields**.

### 3.1 Import Consumption — half-hourly meter reads

**Endpoint:**
```
GET https://api.octopus.energy/v1/electricity-meter-points/{mpan}/meters/{serial}/consumption/
    ?period_from={YYYY-MM-DDTHH:MM:SSZ}
    &period_to={YYYY-MM-DDTHH:MM:SSZ}
    &group_by=half-hour
    &order_by=period
    &page_size=25000
```

**Authentication:** HTTP Basic Auth — API key as username, empty password.

**Parameters:**
- `mpan`: `OCTOPUS_MPN` from config entry
- `serial`: `OCTOPUS_METER_SERIAL` from config entry (added in D-18)
- `period_from`: 12 months ago from today, midnight UTC (e.g. `2025-04-01T00:00:00Z`)
- `period_to`: start of the current calendar month, midnight UTC (e.g. `2026-04-01T00:00:00Z`)
- `group_by=half-hour`: returns 30-minute interval rows
- `page_size=25000`: captures a full year in a single paginated fetch (a year = 17,520 slots)

**Pagination:** Follow the `next` cursor in the response until `next` is `null`.

**Response fields used:**
| Field | Type | Notes |
|---|---|---|
| `interval_start` | ISO-8601 string (UTC) | Start of the 30-min window |
| `interval_end` | ISO-8601 string (UTC) | End of the 30-min window |
| `consumption` | float | kWh imported during the slot |

**Date range:** Rolling 12-month window ending at the **start of the current calendar month**. Incomplete current month is excluded to avoid partial-month distortion in the comparison.

> **Example:** If today is 13 April 2026, fetch `2025-04-01T00:00:00Z` → `2026-04-01T00:00:00Z`.

### 3.2 Export Consumption — half-hourly export reads (optional)

**Endpoint:** Identical structure to 3.1, but using `OCTOPUS_EXPORT_MPN` and export meter serial.

Export meter serial is not yet in the existing config (see [Open Question OQ-1](#10-open-questions)). If not configured, export earnings are computed as zero and a warning attribute is set on the sensor.

### 3.3 Historical Unit Rates — for any tariff

**Endpoint:**
```
GET https://api.octopus.energy/v1/products/{product_code}/electricity-tariffs/{tariff_code}/standard-unit-rates/
    ?period_from={YYYY-MM-DDTHH:MM:SSZ}
    &period_to={YYYY-MM-DDTHH:MM:SSZ}
    &page_size=25000
```

**Authentication:** Public endpoint — no auth required. (Auth may still be passed; it is harmlessly ignored.)

**Parameters:**
- `product_code`: derived from `tariff_code` using `_product_code_from_tariff_code()` (existing function in `octopus_agile.py`)
- `tariff_code`: e.g. `E-1R-AGILE-FLEX-22-11-25-B`
- `period_from` / `period_to`: same 12-month range as consumption data

**Response fields used:**
| Field | Type | Notes |
|---|---|---|
| `valid_from` | ISO-8601 string (UTC) | Start of rate validity window |
| `valid_to` | ISO-8601 string (UTC) | End of rate validity window (null = open-ended) |
| `value_inc_vat` | float | Pence per kWh (inclusive of VAT) |

**Behaviour by tariff type:**

| Tariff type | Rate profile | Slots returned |
|---|---|---|
| Agile | 30-min changing rates | Up to 17,520 rows/year |
| Intelligent Go / TOU | Daily repeating bands (e.g. off-peak 00:30–04:30) | Typically 2–4 rows |
| Standard Variable / Fixed | Single rate, valid_from=product launch, valid_to=null | 1–2 rows |

All three are normalised to a 30-minute grid by `_build_historical_rate_map()` (see §5.3).

### 3.4 Standing Charges

**Endpoint:**
```
GET https://api.octopus.energy/v1/products/{product_code}/electricity-tariffs/{tariff_code}/standing-charges/
    ?period_from={YYYY-MM-DDTHH:MM:SSZ}
    &period_to={YYYY-MM-DDTHH:MM:SSZ}
```

**Response fields used:**
| Field | Type | Notes |
|---|---|---|
| `valid_from` | ISO-8601 string (UTC) | Start of standing charge validity |
| `valid_to` | ISO-8601 string (UTC) | null = present |
| `value_inc_vat` | float | Pence per day (inclusive of VAT) |

Standing charges can change mid-year (e.g. fixed-term tariff rollover). The calculation applies each standing charge rate only for the days it was valid within the comparison window.

**Export tariffs:** Export tariffs do not have standing charges. Standing charges are only fetched for import tariffs.

### 3.5 Open-Meteo Historical Weather API

Used by the Approach A simulation pipeline (§6.7) to source historical hourly temperatures for each day in the comparison window.

**Endpoint:**
```
GET https://archive-api.open-meteo.com/v1/archive
    ?latitude={hass.config.latitude}
    &longitude={hass.config.longitude}
    &start_date={YYYY-MM-DD}
    &end_date={YYYY-MM-DD}
    &hourly=temperature_2m
    &timezone=UTC
```

**Authentication:** None required. Free public API, no key needed.

**Fetch strategy:** Request the full 12-month window in a single API call. Response contains ~8,760 hourly rows per year — lightweight and fast (< 5 seconds typical).

**Response fields used:**

| Field | Type | Notes |
|---|---|---|
| `hourly.time` | list of ISO-8601 strings | Hourly timestamps (UTC) |
| `hourly.temperature_2m` | list of floats | °C; one value per hour |

**Resampling to 30-minute slots:** Each hourly value is duplicated into both 30-minute slots within that hour before passing to `PowerCalculator.calculate()`. E.g. `temperature_2m[i]` → slots at `HH:00` and `HH:30`.

**Cache key:** `open_meteo_{period_from_date}_{period_to_date}`. Stored in the same JSON cache file as tariff rates (§5.5). Not re-fetched unless the rolling window advances past the cached range.

**`OpenMeteoHistoricalClient`:** New thin client in `tariff_comparison/open_meteo_historical.py`. Accepts `lat`/`lon` from `hass.config.latitude`/`hass.config.longitude`; returns a `dict[date, list[float]]` mapping each date to its 24 hourly temperatures.

---

### 3.6 API Rate Limits and Call Volume

For a 12-month comparison with N configured tariffs:

| Fetch operation | Calls | Notes |
|---|---|---|
| Import consumption (all tariffs share one meter) | 1 | Paginated; ~1–2 API calls at `page_size=25000` |
| Export consumption | 1 | As above; 0 if no export serial |
| Import unit rates per tariff | N | 1–2 calls per tariff (pagination) |
| Export unit rates per tariff | N | As above; 0 if no export tariffs |
| Import standing charges per tariff | N | Typically 1 call per tariff |
| Open-Meteo historical temperature (full year) | 1 | Free; no auth; cached after first fetch |
| **Total for N=4 tariff pairs** | **~12–15** | Well within Octopus API limits |

After initial fetch, all data is cached to disk (§5.5) and is **not re-fetched on every coordinator update**.

---

## 4. Tariff Configuration

### 4.1 Where it lives

Tariff comparison configuration is stored as part of the existing config entry `options` dict. A new `step_tariff_comparison` step is added to the existing options flow in `config_flow.py`.

### 4.2 Data format

Stored under key `TARIFF_COMPARISON_TARIFFS` as a JSON string (list of objects):

```json
[
  {
    "name": "Current (Agile)",
    "import_tariff_code": "E-1R-AGILE-FLEX-22-11-25-B",
    "export_tariff_code": "E-1R-AGILE-OUTGOING-19-05-13-B",
    "include_standing_charges": true,
    "is_current": true
  },
  {
    "name": "Intelligent Go",
    "import_tariff_code": "E-1R-INTELLI-VAR-22-10-01-B",
    "export_tariff_code": "E-1R-AGILE-OUTGOING-19-05-13-B",
    "include_standing_charges": true,
    "is_current": false
  },
  {
    "name": "Cosy Octopus",
    "import_tariff_code": "E-1R-COSY-22-12-08-B",
    "export_tariff_code": null,
    "include_standing_charges": true,
    "is_current": false
  }
]
```

**Field definitions:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | ✔ | User-chosen display label for charts |
| `import_tariff_code` | string | ✔ | Full Octopus tariff code, e.g. `E-1R-AGILE-FLEX-22-11-25-B` |
| `export_tariff_code` | string \| null | ✔ | null = no export comparison for this row |
| `include_standing_charges` | bool | ✔ | Add import standing charges to monthly cost |
| `is_current` | bool | ✔ | True for the auto-populated current tariff |

**Constraints:**
- Maximum 6 tariff entries (enforced in config flow validation).
- `import_tariff_code` must match the regex `^E-[12]R-[A-Z0-9\-]+-[A-Z]$`. Basic format check only — the API call will fail at runtime with a clear error if the code is invalid.
- The "current" tariff (`is_current=true`) is **auto-populated** on first setup from `OctopusAgileRatesClient.import_tariff_code` and `export_tariff_code`. The user may rename it but cannot delete it.
- At least 1 tariff entry must exist (the current tariff).

### 4.3 Options flow UX

The options flow step presents:

1. A read-only text display of the currently active tariffs (auto-populated).
2. A multi-line text area where Robert can add/edit tariff entries as JSON. (v1 approach — avoids implementing a complex dynamic form.)
3. A checkbox: "Include standing charges in comparison" (applies globally as a default; can be overridden per entry).
4. A link to the Octopus tariff comparison tool in the help text: `https://octopus.energy/tariffs/`

> **Implementer note for Dallas:** Consider a future v2 UX that fetches `/v1/products/?is_variable=true&is_green=true&available_at=now` for a dropdown of live tariff names. For v1, JSON text input is simpler and reliable.

### 4.4 Region code

Tariff codes include a single-letter region suffix (e.g. `-B` for Eastern England). The region in the existing current tariff code is used as the default region for all new tariffs. Implementers should extract the region letter from `import_tariff_code` and document it in the help text so Robert knows which suffix to use.

```python
def _region_from_tariff_code(tariff_code: str) -> str:
    """Extract the single-letter region suffix from a tariff code."""
    return tariff_code.split("-")[-1]   # 'B' from 'E-1R-AGILE-FLEX-22-11-25-B'
```

---

## 5. Architecture

### 5.1 New files

```
custom_components/battery_charge_calculator/
├── tariff_comparison/
│   ├── __init__.py              — package init; exports TariffComparisonCoordinator
│   ├── client.py                — TariffComparisonClient (Octopus historical data)
│   ├── calculator.py            — cost calculation; returns monthly breakdown dicts
│   ├── cache.py                 — JSON disk cache with atomic write
│   └── open_meteo_historical.py — OpenMeteoHistoricalClient (historical weather, free, no auth)
└── sensors/
    └── tariff_comparison.py     — TariffComparisonSensor
```

### 5.2 Modified files

| File | Change |
|---|---|
| `const.py` | Add tariff comparison constants (§5.6) |
| `config_flow.py` | Add `step_tariff_comparison` to options flow |
| `coordinators.py` | Instantiate `TariffComparisonCoordinator`; register service `refresh_tariff_comparison` |
| `sensor.py` | Register `TariffComparisonSensor` when tariff comparison is configured |
| `services.yaml` | Add `refresh_tariff_comparison` service definition |

### 5.3 `TariffComparisonClient` (`tariff_comparison/client.py`)

Responsible for all Octopus API calls needed by this feature. Follows the existing pattern in `OctopusAgileRatesClient`: caller passes an `aiohttp.ClientSession`; client does not create sessions (consistent with D-14).

```python
class TariffComparisonClient:
    """Fetches historical consumption and tariff rate data from the Octopus API."""

    def __init__(self, api_key: str, mpan: str, meter_serial: str,
                 export_mpan: str | None = None,
                 export_meter_serial: str | None = None) -> None: ...

    async def fetch_consumption(
        self,
        session: aiohttp.ClientSession,
        period_from: datetime,
        period_to: datetime,
        export: bool = False,
    ) -> list[dict]:
        """
        Fetch half-hourly consumption readings for the import (or export) meter.

        Returns list of dicts: {"interval_start": datetime, "consumption": float}
        sorted ascending by interval_start.

        Paginates automatically by following 'next' cursor until None.
        Raises ValueError if export=True and no export MPAN/serial configured.
        """

    async def fetch_unit_rates(
        self,
        session: aiohttp.ClientSession,
        tariff_code: str,
        period_from: datetime,
        period_to: datetime,
    ) -> list[dict]:
        """
        Fetch historical unit rates for any tariff.

        Returns list of dicts: {"valid_from": datetime, "valid_to": datetime | None,
                                "value_inc_vat": float}  (pence/kWh, inc VAT).
        Sorted ascending by valid_from.
        """

    async def fetch_standing_charges(
        self,
        session: aiohttp.ClientSession,
        tariff_code: str,
        period_from: datetime,
        period_to: datetime,
    ) -> list[dict]:
        """
        Fetch historical standing charges for an import tariff.

        Returns list of dicts: {"valid_from": datetime, "valid_to": datetime | None,
                                "value_inc_vat": float}  (pence/day, inc VAT).
        """
```

**Internal helper — `_build_historical_rate_map`:**

Converts a raw rates list (any tariff type) into a `dict[datetime, float]` mapping each UTC 30-minute slot start to its rate in pence/kWh. This is the lookup table used in the calculator.

```python
def _build_historical_rate_map(
    raw_rates: list[dict],
    period_from: datetime,
    period_to: datetime,
) -> dict[datetime, float]:
    """
    Expand raw rate bands into a {slot_start: rate_p_per_kwh} dict.

    Works for all tariff types:
    - Agile: rates already at 30-min resolution
    - Standard/Fixed: single rate covering the full period
    - TOU (Intelligent Go etc.): daily-repeating bands

    Slot starts are UTC, rounded to 30-min boundaries.
    Missing slots fall back to the most recent known rate (forward-fill).
    """
    # <!-- Hockney: Rate-before-window seed — IMPORTANT. The Octopus API filters
    # unit-rate rows by valid_from >= period_from. For a Standard/Fixed tariff whose
    # single rate started months/years before period_from, the query returns ZERO rows,
    # and forward-fill has nothing to seed from — silently producing a 0 p/kWh rate
    # for the entire window. The caller (TariffComparisonClient.fetch_unit_rates) MUST
    # issue a second request:
    #   GET .../standard-unit-rates/?period_to={period_from}&page_size=1
    # to retrieve the rate in force at period_from, and prepend it to raw_rates with
    # valid_from = period_from before passing to this function. TOU tariffs (Intelligent
    # Go) have the same exposure. Agile is unaffected (rates always generated within the
    # window). -->
```

This mirrors the logic in the existing `_expand_to_30min_slots()` but works over an arbitrary historical date range rather than a 2-day forward window.

### 5.4 `TariffComparisonCoordinator` (`tariff_comparison/__init__.py`)

A lightweight `DataUpdateCoordinator` separate from `BatteryChargeCoordinator`.

**Rationale for separation:** `BatteryChargeCoordinator` updates every minute for real-time battery scheduling. The tariff comparison requires no intra-day updates — a weekly refresh is sufficient. Decoupling avoids slowing the hot path.

```python
class TariffComparisonCoordinator(DataUpdateCoordinator):
    """Coordinator for Monthly Tariff Comparison data.

    Update interval: weekly (configurable via TARIFF_COMPARISON_UPDATE_INTERVAL_DAYS).
    On first load: immediately fetch data if cache is absent or stale.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None: ...

    async def _async_update_data(self) -> dict:
        """
        Orchestrate full fetch-calculate-cache cycle.

        Returns the computed tariff_comparison dict (same shape as sensor attributes).
        Raises UpdateFailed if ALL tariffs fail to compute (partial failure is tolerated).
        """

    async def _fetch_and_calculate(self, session: aiohttp.ClientSession) -> dict:
        """Core logic: fetch consumption + rates, run calculator, return result."""

    async def async_refresh_now(self) -> None:
        """Force immediate refresh; called by the service handler."""
```

**Lifecycle:**
1. Created in `coordinators.py` alongside `BatteryChargeCoordinator` when tariff comparison is configured.
2. Uses the same `aiohttp.ClientSession` as the main coordinator (passed in from `async_get_clientsession(hass)`).
3. Cache is loaded at startup; if cache is fresh (< `TARIFF_COMPARISON_CACHE_MAX_AGE_DAYS`, default 7 days), the coordinator skips the API fetch and returns cached data immediately.
4. If the `comparison_data_year` in the cache no longer matches the target year, cache is invalidated and re-fetched.

### 5.5 Cache (`tariff_comparison/cache.py`)

Historical meter consumption and tariff rates do not change — once a period has passed, the data is fixed. The cache avoids re-fetching 17,520 rows per tariff on every weekly update.

**Cache file location:**
```
{hass.config.path("battery_charge_calculator_tariff_cache.json")}
```
(Consistent with D-4: HA config directory survives HACS updates.)

**Cache structure:**

```json
{
  "schema_version": 1,
  "generated_at": "2026-04-13T10:00:00Z",
  "data_year": "2025-04",
  "consumption": {
    "import": [
      {"interval_start": "2025-04-01T00:00:00+00:00", "consumption": 0.234},
      "..."
    ],
    "export": []
  },
  "tariff_rates": {
    "E-1R-AGILE-FLEX-22-11-25-B": {
      "unit_rates": [
        {"valid_from": "2025-04-01T00:00:00+00:00", "valid_to": "2025-04-01T00:30:00+00:00", "value_inc_vat": 24.5},
        "..."
      ],
      "standing_charges": [
        {"valid_from": "2025-04-01T00:00:00+00:00", "valid_to": null, "value_inc_vat": 46.36}
      ]
    }
  }
}
```

**Cache write:** Atomic POSIX rename (same pattern as D-4 model persistence):
```python
fd, tmp_path = tempfile.mkstemp(dir=config_dir, suffix=".tmp")
json.dump(cache_data, fp, default=str)
os.replace(tmp_path, cache_path)
```

**Cache invalidation rules:**

| Condition | Action |
|---|---|
| Cache file missing | Full re-fetch |
| `data_year` in cache ≠ current target year | Full re-fetch |
| `generated_at` > `TARIFF_COMPARISON_CACHE_MAX_AGE_DAYS` (default 7) | Full re-fetch |
| New tariff code in config not present in `tariff_rates` | Partial re-fetch (rates only for new tariff) |
| Service `refresh_tariff_comparison` called | Full re-fetch (force flag) |

**Reading cached data:** The coordinator reads the cache synchronously at startup inside `hass.async_add_executor_job()` (blocking I/O, consistent with D-5).

### 5.6 New constants (`const.py` additions)

```python
# Tariff comparison feature
TARIFF_COMPARISON_TARIFFS              = "tariff_comparison_tariffs"          # JSON string
TARIFF_COMPARISON_ENABLED             = "tariff_comparison_enabled"
TARIFF_COMPARISON_UPDATE_INTERVAL_DAYS = "tariff_comparison_update_interval_days"
TARIFF_COMPARISON_CACHE_MAX_AGE_DAYS  = "tariff_comparison_cache_max_age_days"
TARIFF_COMPARISON_INCLUDE_EXPORT      = "tariff_comparison_include_export"

# Defaults
DEFAULT_TARIFF_COMPARISON_UPDATE_INTERVAL_DAYS = 7
DEFAULT_TARIFF_COMPARISON_CACHE_MAX_AGE_DAYS   = 7
DEFAULT_TARIFF_COMPARISON_INCLUDE_EXPORT       = False   # until OQ-1 resolved

# Sensor
TARIFF_COMPARISON_SENSOR              = "battery_charge_calculator.tariff_comparison"
TARIFF_COMPARISON_SENSOR_NAME         = "Monthly Tariff Comparison"

# Export meter serial (new; added alongside OCTOPUS_METER_SERIAL from D-18)
OCTOPUS_EXPORT_METER_SERIAL           = "octopus_export_meter_serial"
```

### 5.7 `TariffComparisonSensor` (`sensors/tariff_comparison.py`)

```python
class TariffComparisonSensor(CoordinatorEntity, SensorEntity):
    """Sensor exposing Monthly Tariff Comparison as monthly breakdown attributes."""

    _attr_should_poll = False
    _attr_name = const.TARIFF_COMPARISON_SENSOR_NAME
    _attr_unique_id = const.TARIFF_COMPARISON_SENSOR
    _attr_native_unit_of_measurement = "GBP"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:currency-gbp"
```

**State value:** Net annual cost (import cost − export earnings + standing charges) for the **first** tariff in the configured list (i.e. the "current" tariff). This gives a quick glance at the headline number.

**Attributes:** Full comparison data (see §7 for exact schema).

---

## 6. Cost Calculation Logic

All calculation logic lives in `tariff_comparison/calculator.py`. No HA imports; pure Python.

### 6.1 Overview

```
fetch_consumption(period_from, period_to)
    → list of 30-min import slots: [{interval_start, consumption_kwh}]

fetch_consumption(export=True, period_from, period_to)
    → list of 30-min export slots: [{interval_start, consumption_kwh}]

for each tariff:
    fetch_unit_rates(tariff_code, period_from, period_to)  → rate_map
    fetch_standing_charges(import_tariff_code, period_from, period_to)  → sc_list

    calculate_tariff_cost(import_slots, export_slots, rate_map, export_rate_map, sc_list)
        → list of monthly dicts

aggregate to sensor attributes
```

### 6.2 Per-slot cost formula

<!-- Hockney: Slot iteration domain — the loop must iterate over the UNION of all distinct
interval_start timestamps from both the import_slots and export_slots lists, not just
the import list. Pure-export slots (interval_start present in export data but absent
from import data) would be silently dropped if you iterate import_slots only, understating
export_earnings. For each slot in the union: import_kwh defaults to 0 if the slot is
absent from import_slots; export_kwh defaults to 0 if absent from export_slots. -->

<!-- Hockney: Timezone-aware datetime keys — BOTH the rate_map dict keys and the
consumption slot interval_start values MUST be timezone-aware UTC datetime objects
(e.g. datetime(2025, 4, 1, 0, 0, tzinfo=timezone.utc)). A naive datetime and a
timezone-aware datetime never compare equal as dict keys, causing every lookup to miss
and forward-fill to silently pad the entire year with the last known rate. Enforce this
at the boundary where ISO-8601 strings are parsed from the API or from the cache. -->

For each 30-minute slot with start time $t_i$ (drawn from the union of import and export slot timestamps):

$$\text{import\_cost}_i \;[\text{p}] = \text{consumption\_kwh}_i \times \text{import\_rate}(t_i) \;[\text{p/kWh}]$$

$$\text{export\_earnings}_i \;[\text{p}] = \text{export\_kwh}_i \times \text{export\_rate}(t_i) \;[\text{p/kWh}]$$

where `import_rate(t)` and `export_rate(t)` look up the rate in the pre-built `dict[datetime, float]` rate map. `consumption_kwh_i = 0` if the slot is absent from import data; `export_kwh_i = 0` if absent from export data.

**Unit note:** `consumption_kwh_i` is already total energy for the 30-min window (kWh, not kW). No 0.5 multiplier is required. The product with a rate in p/kWh directly yields pence. <!-- Hockney: Explicit unit confirmation — the API returns consumption in kWh per interval, not average power in kW, so the formula is dimensionally correct without a ×0.5 factor. -->

**Rate lookup miss:** If a slot has no matching entry in the rate map (API gap), use the **most recently known rate** (forward-fill). Log a `DEBUG` message per gap run; do not fail the calculation. Forward-fill applies to both import and export rate maps.

### 6.3 Monthly aggregation

Group all slots by calendar month (UTC). For month $m$:

$$\text{import\_cost\_gbp}(m) = \frac{\sum_{i \in m} \text{import\_cost}_i}{100}$$

$$\text{export\_earnings\_gbp}(m) = \frac{\sum_{i \in m} \text{export\_earnings}_i}{100}$$

$$\text{standing\_charge\_gbp}(m) = \frac{\text{standing\_charge\_p\_per\_day}(m) \times \text{days}(m)}{100}$$

Where `standing_charge_p_per_day(m)` is the weighted average standing charge over the month if the rate changed mid-month:

$$\text{sc\_weighted}(m) = \frac{\sum_j \text{sc}_j \times \text{days\_active}_j(m)}{\text{days}(m)}$$

<!-- Hockney: standing_charge_gbp simplification — the weighted-average intermediate is
unnecessary. Substituting sc_weighted back into the GBP formula gives:
  standing_charge_gbp(m) = Σ_j (sc_j × days_active_j(m)) / 100
This direct sum is equivalent and avoids the divide-then-multiply round-trip. Recommend
the implementation use this form.

Also: days(m) must be counted as calendar days in the applicable timezone (UK local / IANA
"Europe/London"), NOT as slot_count / 48. DST spring-forward has 46 UTC slots (23 h) and
fall-back has 50 UTC slots (25 h) — dividing slot count by 48 would give 0.958 and 1.042
days respectively, corrupting the standing charge for those months by ~1 day's worth (~46p).
Use calendar.monthrange or datetime.date arithmetic to obtain integer day counts. -->

$$\text{net\_cost\_gbp}(m) = \text{import\_cost\_gbp}(m) - \text{export\_earnings\_gbp}(m) + \text{standing\_charge\_gbp}(m) \times [\text{include\_standing\_charges}]$$

<!-- Hockney: include_standing_charges conditional — the original formula always added the
standing charge. The function signature accepts include_standing_charges: bool and the
config supports per-tariff override (§4.2). When include_standing_charges=False, the term
must be zeroed. The Iverson-bracket notation [include_standing_charges] (= 1 if True, 0 if
False) makes this conditional explicit in the formula. -->

### 6.4 Annual totals

$$\text{annual\_import\_cost} = \sum_{m=1}^{12} \text{import\_cost\_gbp}(m)$$

$$\text{annual\_export\_earnings} = \sum_{m=1}^{12} \text{export\_earnings\_gbp}(m)$$

$$\text{annual\_standing\_charges} = \sum_{m=1}^{12} \text{standing\_charge\_gbp}(m)$$

$$\text{annual\_net\_cost} = \text{annual\_import\_cost} - \text{annual\_export\_earnings} + \text{annual\_standing\_charges}$$

### 6.5 Calculator function signature

```python
def calculate_tariff_cost(
    import_slots: list[dict],          # [{interval_start: datetime, consumption: float}]
    import_rate_map: dict[datetime, float],   # pence/kWh per slot
    standing_charges: list[dict],       # [{valid_from, valid_to, value_inc_vat (p/day)}]
    export_slots: list[dict] | None,    # None if export not configured
    export_rate_map: dict[datetime, float] | None,
    include_standing_charges: bool = True,
) -> dict:
    """
    Calculate monthly and annual costs for one tariff against actual consumption.

    Returns:
        {
          "monthly": [{"month": "YYYY-MM", "import_cost_gbp": float,
                       "export_earnings_gbp": float, "standing_charge_gbp": float,
                       "net_cost_gbp": float}, ...],
          "annual": {"import_cost_gbp": float, "export_earnings_gbp": float,
                     "standing_charges_gbp": float, "net_cost_gbp": float},
          "coverage_pct": float,   # % of import slots that had a direct rate-map hit BEFORE forward-fill (data quality indicator)
          # <!-- Hockney: coverage_pct must measure pre-forward-fill rate-map hits on the import rate map only.
          # If measured after forward-fill it would always be 100% and the field would be meaningless.
          # Export rate map gaps are not separately tracked in v1; a future coverage_export_pct could be added. -->
          "slot_count": int,
        }
    """
```

### 6.6 Worked example (one month)

Assume January 2025 (31 days, 1,488 half-hourly slots):

- Agile import: avg rate 18.5 p/kWh; total monthly import = 285 kWh
- Outgoing Agile export: avg rate 12.3 p/kWh; total monthly export = 47 kWh
- Standing charge: 46.36 p/day

$$\text{import\_cost} = \frac{285 \times 18.5}{100} = £52.73$$

$$\text{export\_earnings} = \frac{47 \times 12.3}{100} = £5.78$$

$$\text{standing\_charge} = \frac{46.36 \times 31}{100} = £14.37$$

$$\text{net\_cost} = 52.73 - 5.78 + 14.37 = £61.32$$

<!-- Hockney: Worked example is pedagogically correct but uses a SIMPLIFICATION.
"avg rate × total consumption" equals Σ(slot_kwh_i × rate_i) only when consumption
is uniformly distributed across all rate intervals — not the case in practice, and
especially not on Agile where smart behaviour shifts load to cheap slots. The actual
implementation MUST compute Σ(slot_kwh_i × rate_i) per-slot, not avg_rate × Σslot_kwh.
For a user actively shifting load on Agile, the per-slot formula may yield import_cost
10–20% lower than the avg_rate×total approximation would suggest. The example is fine
for illustrating the unit conversion and net formula; it should not be used as a
validation check against the per-slot implementation. -->

### 6.7 Consumption data model

This section formalises how consumption data is sourced per tariff using the **A+C combined pipeline**, as directed by robert.nash.

#### Current tariff (`is_current: true`)

Always uses **real half-hourly meter reads** from the Octopus API (§3.1). No simulation or approximation applied. `comparison_method = "real_meter_reads"`. Unchanged.

#### Non-current tariffs — the A+C combined pipeline

Non-current tariffs automatically progress through three phases.

**Phase 1 — Approach C (immediate, always available)**

Computed on the first coordinator update after data is fetched. Real meter reads are replayed against each non-current tariff's historical rates. No additional data dependencies. Results are immediately available in the sensor.

```
comparison_method = "naive_replay"
simulation_progress_pct = 0.0
data_quality_notes = ["Consumption data reflects system behaviour optimised for the
  current tariff. Non-current tariff costs may be understated or overstated compared
  to what they would actually be on this tariff. Simulation in progress."]
```

**Phase 2 — Approach A simulation (background task, progressive)**

Immediately after Phase 1 completes, the coordinator queues a simulation job for every non-current tariff that does not have a completed `"simulation"` result in the cache. Each job runs entirely in background and never blocks the event loop.

Per-job steps:

1. **Fetch Open-Meteo historical temperatures** (§3.5) for the full 12-month window in one API call. Result cached as `open_meteo_{period_from}_{period_to}`. Subsequent tariffs reuse the same cached temperature data — only one Open-Meteo fetch per year window regardless of N tariffs.

2. **Iterate day by day** (up to 365 iterations) through the comparison window. For each day:

   a. Slice 24 hourly temperatures into 48 × 30-min slots (duplicate each hourly value).

   b. Look up that day's historical unit rates from the rate map already in cache (§5.5) — no additional API call.

   c. Run `PowerCalculator.calculate(temp_slots)` → half-hourly household power demand. Uses the same `PowerCalculator` instance as the main coordinator, with the same heat loss / heating config.

   d. Run `GeneticEvaluator.run(demand_forecast, tariff_rates, battery_config)` → optimised `timeslots` for that day under that tariff's rates.

   e. Derive simulated grid import/export from the returned `timeslots`:
      - `import_kwh += slot.kwh` for each slot with `charge_option == 'charge'`
      - `export_kwh += slot.export_kwh` for each slot with `charge_option == 'export'`
      - Discharge/idle slots: household demand served from battery; grid import ≈ `base_load_kwh_per_30min` from config.

   f. Apply that day's tariff rates to simulated import/export values → accumulate into monthly cost buckets.

   All `PowerCalculator.calculate()` and `GeneticEvaluator.run()` calls are wrapped in `hass.async_add_executor_job()` (D-5 compliance — CPU-bound work off the event loop). Days are processed sequentially within a tariff to avoid thread contention.

3. **Progress reporting:** After every 30 days of simulation, the coordinator calls `async_set_updated_data()` to push a partial update to the sensor. `simulation_progress_pct` advances from 0 → 100 in ~12 steps.

   ```
   comparison_method = "simulation_in_progress"
   simulation_progress_pct = 42.0   (example: 153 of 365 days complete)
   data_quality_notes = ["Simulation 42% complete. Showing naive replay data until
     simulation finishes."]
   ```

4. **Completion:** When all 365 days for a tariff are simulated, write results to cache (new `simulation_results` section — see below), update the tariff's sensor data to Approach A values, and fire a final coordinator update.

   ```
   comparison_method = "simulation"
   simulation_progress_pct = 100.0
   data_quality_notes = []
   ```

**Phase 3 — Simulation complete (subsequent restarts)**

Once simulation is cached for a `(tariff_code, data_year)` key, coordinator restarts load from cache instantly. No re-simulation unless:
- Service `refresh_tariff_comparison` is called (force flag).
- The rolling `data_year` in the cache no longer matches the current target window.
- The tariff configuration changes (new tariff code added or modified).

#### Performance expectations

| Task | Expected duration | Runs on |
|---|---|---|
| Phase 1 — Approach C (all tariffs) | < 10 seconds | First coordinator update |
| Open-Meteo fetch (full year, once) | < 5 seconds | Async; shared across all tariffs |
| `GeneticEvaluator.run()` per day | 100–500 ms (existing) | Executor thread, per D-5 |
| Full simulation for one non-current tariff (365 days) | ~1–3 minutes | Background; non-blocking |
| Full simulation for N=4 non-current tariffs (sequential) | ~4–12 minutes total | Background; non-blocking |

Robert sees Approach C data within seconds. Approach A data replaces it progressively over ~4–12 minutes. The sensor is useful from the moment the integration first loads.

#### Cache additions for simulation results

New `simulation_results` section in the cache JSON (§5.5), keyed by tariff code:

```json
"simulation_results": {
  "E-1R-INTELLI-VAR-22-10-01-B": {
    "status": "complete",
    "completed_at": "2026-04-13T10:15:00Z",
    "data_year": "2025-04",
    "monthly": [
      {
        "month": "2025-04",
        "import_kwh_simulated": 210.5,
        "export_kwh_simulated": 52.3,
        "import_cost_gbp": 38.20,
        "export_earnings_gbp": 11.40,
        "standing_charge_gbp": 13.91,
        "net_cost_gbp": 40.71
      }
    ],
    "annual": {
      "import_cost_gbp": 443.10,
      "export_earnings_gbp": 143.20,
      "standing_charges_gbp": 169.21,
      "net_cost_gbp": 469.11
    }
  }
}
```

`status` values: `"in_progress"`, `"complete"`.

---

## 7. Sensor Schema

**Entity ID:** `sensor.monthly_tariff_comparison`
**State:** Net annual cost of the first (current) tariff in £ (e.g. `"724.50"`)
**Unit:** `GBP`
**Device class:** `monetary`

### 7.1 `extra_state_attributes` (full schema)

```json
{
  "generated_at": "2026-04-13T10:00:00+00:00",
  "data_period": {
    "from": "2025-04-01",
    "to": "2026-04-01"
  },
  "coverage_warning": false,
  "tariffs": [
    {
      "name": "Current (Agile)",
      "import_tariff_code": "E-1R-AGILE-FLEX-22-11-25-B",
      "export_tariff_code": "E-1R-AGILE-OUTGOING-19-05-13-B",
      "is_current": true,
      "include_standing_charges": true,
      "comparison_method": "real_meter_reads",
      "data_quality_notes": [],
      "coverage_pct": 99.7,
      "monthly": [
        {
          "month": "2025-04",
          "import_cost_gbp": 48.32,
          "export_earnings_gbp": 9.14,
          "standing_charge_gbp": 13.91,
          "net_cost_gbp": 53.09
        },
        {
          "month": "2025-05",
          "import_cost_gbp": 35.60,
          "export_earnings_gbp": 18.22,
          "standing_charge_gbp": 14.37,
          "net_cost_gbp": 31.75
        }
      ],
      "annual": {
        "import_cost_gbp": 512.40,
        "export_earnings_gbp": 143.20,
        "standing_charges_gbp": 169.21,
        "net_cost_gbp": 538.41
      }
    },
    {
      "name": "Intelligent Go",
      "import_tariff_code": "E-1R-INTELLI-VAR-22-10-01-B",
      "export_tariff_code": "E-1R-AGILE-OUTGOING-19-05-13-B",
      "is_current": false,
      "include_standing_charges": true,
      "comparison_method": "simulation_in_progress",
      "simulation_progress_pct": 42.0,
      "data_quality_notes": [
        "Simulation 42% complete. Currently showing naive replay data. Values will update as simulation finishes."
      ],
      "coverage_pct": 100.0,
      "monthly": [ "..." ],
      "annual": {
        "import_cost_gbp": 443.10,
        "export_earnings_gbp": 143.20,
        "standing_charges_gbp": 169.21,
        "net_cost_gbp": 469.11
      }
    }
  ],
  "export_configured": true,
  "export_meter_serial_missing": false
}
```

### 7.2 Schema constraints

- `tariffs` array has 1–6 entries (matches config limit).
- `monthly` array always has exactly 12 entries, in ascending calendar month order.
- All monetary values are rounded to 2 decimal places (`round(value, 2)`).
- `coverage_pct` is `float` in range `[0.0, 100.0]`. Values below 95.0 trigger `coverage_warning: true` at the top level.
- `comparison_method` values per tariff entry:
  - `"real_meter_reads"` — current tariff; actual meter data.
  - `"naive_replay"` — non-current tariff; Phase 1 (Approach C); simulation not yet started.
  - `"simulation_in_progress"` — non-current tariff; Phase 2 (Approach A running); naive replay data shown; `simulation_progress_pct` is 0–100.
  - `"simulation"` — non-current tariff; Phase 3 (Approach A complete); accurate simulated data; `data_quality_notes` is empty.
- `simulation_progress_pct` is `float` in range `[0.0, 100.0]`. Present on all non-current tariff entries. `0.0` when in `naive_replay` state; `100.0` when in `simulation` state.
- `data_quality_notes` is an array of zero or more strings per tariff entry. Empty (`[]`) for the current tariff and for fully-simulated non-current tariffs. Non-empty during Phase 1 and Phase 2. The Lovelace card **must** render these notes visibly (e.g. as a footnote or tooltip on the comparison chart) when non-empty.
- `export_meter_serial_missing: true` when export comparison was requested but no export meter serial is configured. Export earnings will be 0.0 for all tariffs in this case.

---

## 8. Lovelace Visualisation

The suggested Lovelace card uses the **ApexCharts Card** (HACS) with a grouped bar chart — one bar group per calendar month, one bar per tariff, showing net cost.

### 8.1 Suggested ApexCharts YAML

```yaml
type: custom:apexcharts-card
header:
  title: Monthly Tariff Comparison — Net Cost (£)
  show: true
graph_span: 12months
apex_config:
  chart:
    type: bar
  plotOptions:
    bar:
      columnWidth: 75%
      grouped: true
  xaxis:
    type: category
    labels:
      rotate: -45
  yaxis:
    title:
      text: "Net Cost (£)"
  tooltip:
    shared: true
    intersect: false
series:
  - entity: sensor.monthly_tariff_comparison
    name: Current (Agile)
    data_generator: |
      return entity.attributes.tariffs
        .find(t => t.is_current)
        .monthly
        .map(m => ({ x: m.month, y: m.net_cost_gbp }));
  - entity: sensor.monthly_tariff_comparison
    name: Intelligent Go
    data_generator: |
      return entity.attributes.tariffs
        .find(t => t.name === 'Intelligent Go')
        .monthly
        .map(m => ({ x: m.month, y: m.net_cost_gbp }));
  - entity: sensor.monthly_tariff_comparison
    name: Cosy Octopus
    data_generator: |
      return entity.attributes.tariffs
        .find(t => t.name === 'Cosy Octopus')
        .monthly
        .map(m => ({ x: m.month, y: m.net_cost_gbp }));
```

> **Note for Dallas:** The `data_generator` JavaScript array uses `entity.attributes.tariffs.find(t => t.name === 'Tariff Name')`. Duplicate tariff names in config will cause the first match to be used. Enforce unique names in the options flow validator.

### 8.2 Alternative — stacked bar (import vs export vs standing charge for one tariff)

To visualise cost components for a single tariff:

```yaml
type: custom:apexcharts-card
header:
  title: Current Tariff — Monthly Cost Breakdown (£)
  show: true
apex_config:
  chart:
    type: bar
    stacked: true
  plotOptions:
    bar:
      columnWidth: 60%
series:
  - entity: sensor.monthly_tariff_comparison
    name: Import Cost
    color: "#FF5733"
    data_generator: |
      return entity.attributes.tariffs
        .find(t => t.is_current)
        .monthly
        .map(m => ({ x: m.month, y: m.import_cost_gbp }));
  - entity: sensor.monthly_tariff_comparison
    name: Export Earnings (negative)
    color: "#28B463"
    data_generator: |
      return entity.attributes.tariffs
        .find(t => t.is_current)
        .monthly
        .map(m => ({ x: m.month, y: -m.export_earnings_gbp }));
  - entity: sensor.monthly_tariff_comparison
    name: Standing Charges
    color: "#5DADE2"
    data_generator: |
      return entity.attributes.tariffs
        .find(t => t.is_current)
        .monthly
        .map(m => ({ x: m.month, y: m.standing_charge_gbp }));
```

> The export series uses a negative `y` value so it renders below the zero line, visually offsetting the import cost bars.

---

## 9. Out of Scope

The following are explicitly excluded from v1:

| Item | Rationale |
|---|---|
| Gas tariff comparison | Integration focuses on electricity; gas consumption data not available via Octopus API in the same format |
| Future tariff price projections | Spec requires real historical data only; forecasting tariff rates is speculative |
| Non-Octopus tariffs | No API access for third-party suppliers (Bulb, EDF, E.ON etc.) |
| Multi-property accounts | Spec targets a single MPAN; multi-property handling is deferred |
| Integration with GivEnergy solar/battery self-consumption | The consumption data from Octopus reflects **grid import only**. Solar self-consumption is not reflected in either import or export meter reads. Comparison is therefore of grid costs only, not total energy costs. This is a fundamental limitation of meter-read-based data. |
| Real-time (sub-daily) tariff switching simulation | Only full-tariff comparisons are supported; hybrid/partial-year switches are out of scope |
| Dynamic tariff catalogue / discovery dropdown in UI | v1 uses manual tariff code entry; product list dropdown deferred to v2 |
| Automatic tariff recommendation engine | Robert chooses which tariffs to compare; no automated "best tariff" ranking logic |
| Standing charge comparison for export tariffs | Export tariffs do not have standing charges; no action required |
| Retroactive comparison for years prior to 12 months ago | Only the most recent 12 complete months are supported per refresh |
| Approach B — base-load isolation | Requires estimating base load from combined meter + battery SOC history; accuracy gain over Approach C is marginal if battery SOC data is incomplete. Dropped in favour of full Approach A simulation (§6.7). |

---

## 10. Open Questions

The following require Robert's input before implementation begins.

### OQ-1 — Export meter serial number

**Question:** Does Robert have a Smart Export Guarantee (SEG) meter with Octopus, with a separate meter serial number for export reads? If yes, what is it, or should it be added to the config flow?

**Impact:** Without an export meter serial, export earnings cannot be calculated from actual meter data. The feature will still work for import cost comparison only. Export earnings will be shown as `0.00` with a `export_meter_serial_missing: true` flag in the sensor attributes.

**Options:**
- A) Add `OCTOPUS_EXPORT_METER_SERIAL` as a new optional field in the config flow options step alongside the tariff comparison configuration.
- B) Auto-discover the export meter serial from the Octopus account API (`/v1/accounts/{account_number}/`) — the same discovery mechanism used for the import serial in D-18.
- C) Defer export earnings entirely for v1; compare import costs only.

**Recommendation:** Option B (auto-discover) using the same `is_export=True` meter filter already implemented in `OctopusAgileRatesClient._find_current_tariffs()`.

---

### OQ-2 — Target year: rolling 12 months vs. calendar year

**Question:** Should the comparison window be a rolling 12 months (always ending at the start of the current month) or a fixed calendar year (Jan–Dec of the most recently completed year)?

**Impact:** Rolling 12 months means the comparison updates monthly and always reflects recent behaviour. Calendar year means the comparison is stable from January to January and easier for Robert to reason about ("my 2025 costs").

**Recommendation:** Rolling 12 months (ending at the start of the current calendar month). This keeps the data current and aligns with the approach used elsewhere in the integration.

---

### OQ-3 — Current tariff: always-included or user-removable?

**Question:** Should the "current" tariff (auto-populated from the active Octopus agreement) be forcibly included in the comparison and pinned as the first entry, or should Robert be able to remove it?

**Impact:** Keeping it pinned ensures a clear baseline. Allowing removal gives flexibility (e.g. if Robert has already switched and wants to compare two future options).

**Recommendation:** Pin the current tariff as an immutable first entry. This prevents accidental misconfiguration and ensures the state value of the sensor (the current tariff's annual cost) is always meaningful.

---

### OQ-4 — Standing charges: compare including or excluding?

**Question:** Should standing charges be included in the comparison by default? Some users prefer to see unit-rate-only costs (the part they can influence through usage behaviour), treating standing charges as fixed overhead.

**Impact:** Affects whether `net_cost_gbp` in the sensor includes standing charges. Both import and export tariffs (v1: import only) have standing charges.

**Recommendation:** Include standing charges by default (`include_standing_charges: true` globally), with a per-tariff override available. This gives the most accurate total cost picture. A toggle in the options flow allows Robert to switch to unit-rate-only if preferred.

---

### OQ-5 — Update schedule: weekly or monthly?

**Question:** Should the coordinator refresh its data weekly or monthly? Historical data never changes, but the rolling 12-month window advances by one month when the calendar month turns.

**Impact:** Weekly is more responsive if Robert adds a new tariff to compare; the results appear within a week. Monthly means a new entry might wait up to 4 weeks.

**Recommendation:** Weekly update (every 7 days), but with cache-aware logic so the API is only re-called when new data is available (i.e. when the target `data_year` changes vs the cached value). The `refresh_tariff_comparison` service covers immediate updates.

---

### OQ-6 — Partial month at the boundary: exclude or prorate?

**Question:** The current month is excluded from comparison (incomplete data). But what about the final month in the window — e.g. on 13 April 2026, April 2025 has a full month of data. Does Robert want exactly 12 full calendar months, or should edge months be prorated if they appear to be incomplete?

**Impact:** Minor. The rolling window approach (always ending at the first of the current month) ensures all 12 months in the window are complete. This is the recommended approach with no proration needed.

**Recommendation:** Use the rolling window approach as specced. Mark this question as resolved unless Robert has a specific preference for calendar-year alignment.

---

### OQ-7 — Consumption data model approach ✅ RESOLVED

**Raised by:** robert.nash (2026-04-13)
**Resolved by:** robert.nash (2026-04-13) — *"let's focus the feature on Approach A and C combined"*

**Decision:** Use the **A+C combined pipeline** as fully specified in §6.7.

- **Approach C** (naive replay) delivers immediate results from day one — no waiting, no additional complexity.
- **Approach A** (full `GeneticEvaluator` simulation using Open-Meteo historical weather) runs as a background task, progressively replacing naive replay data per non-current tariff as simulation completes (~4–12 minutes for a full year of N=4 tariffs).
- **Approach B** (base-load isolation) is explicitly dropped.
- `comparison_method` transitions per tariff: `naive_replay` → `simulation_in_progress` → `simulation`.

No further action required. Implementation can proceed on both phases simultaneously.

---

*End of specification.*

*Implementers: Fenster (Python / coordinator / calculator), Dallas (HA sensor, config flow, Lovelace YAML)*
*Review: Keaton (architecture), Robert Nash (product sign-off)*
