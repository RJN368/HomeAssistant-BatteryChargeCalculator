# Monthly Tariff Comparison

The **Monthly Tariff Comparison** feature shows you what your electricity would have cost over the last 12 months under different Octopus Energy tariffs — using your real half-hourly smart meter data.

It answers the question: *"Would I have saved money on Octopus Agile vs. my current tariff?"*

---

## What it does

- Fetches your actual import and export readings from the Octopus Energy API (1 months of half-hourly meter data)
- Reprices those readings against the historical unit rates of each tariff you choose to compare
- Produces a monthly breakdown — import cost, export earnings, standing charges, and net cost — for each tariff
- Exposes everything as a Home Assistant sensor so you can build a dashboard chart

**Two calculation methods run in sequence:**

| Method | When available | Accuracy |
|--------|---------------|----------|
| **Naive replay** | Immediately (minutes) | Good for fixed/TOU tariffs. Slightly overestimates savings on cheap overnight tariffs because your real consumption was shaped by your current tariff's prices |
| **Simulation** | Background (hours, runs overnight) | More accurate — uses the genetic algorithm to re-optimise your battery schedule day-by-day under each alternative tariff |

The `comparison_method` attribute on each tariff entry tells you which method produced that result.

---

## Setting it up

### Step 1 — Open the integration settings

Go to **Settings → Devices & Services**, find **Battery Charge Calculator**, and click **Configure**.

Navigate to the **Monthly Tariff Comparison** step (it appears after the ML settings step).

### Step 2 — Enable the feature

Toggle **Enable Monthly Tariff Comparison** on.

### Step 3 — Pick your tariffs

A list of available Octopus import tariffs is fetched from the Octopus API automatically. Your current tariff is pre-selected.

Tick the tariffs you want to compare. You can select up to 6. Good candidates:

- Your current tariff (always include this — it's your baseline)
- **Agile Octopus** — variable half-hourly rates, often cheapest for battery owners
- **Octopus Go** / **Intelligent Octopus Go** — cheap overnight window
- **Octopus Flux** — import cheap at night, export at a premium during the day

Click **Submit** to save.

!!! tip "Region"
    The tariff list is filtered to your meter's regional suffix (e.g. East Midlands = B). The region is detected automatically from your live tariff code.

---

## The sensor

**Entity:** `sensor.monthly_tariff_comparison`

**State:** Net annual cost (£) for the first tariff in your selection (your baseline).

**Attributes:** Full monthly breakdown for every selected tariff. Example structure:

```yaml
tariffs:
  - label: "Agile Octopus"
    import_tariff_code: "E-1R-AGILE-FLEX-22-11-25-B"
    comparison_method: "naive_replay"    # or "simulation", "simulation_in_progress"
    simulation_progress_pct: 0          # 0–100, updates as simulation runs
    data_quality_notes: "Coverage: 98.5%"
    annual:
      import_cost_gbp: 842.30
      export_earnings_gbp: 124.10
      standing_charges_gbp: 95.45
      net_cost_gbp: 813.65
    monthly:
      - month: "2025-04"
        import_cost_gbp: 68.20
        export_earnings_gbp: 9.80
        standing_charge_gbp: 7.95
        net_cost_gbp: 66.35
      # ... 11 more months
```

The `comparison_method` field progresses as background simulation completes:

| Value | Meaning |
|-------|---------|
| `real_meter_reads` | Current tariff — costs from actual reads |
| `naive_replay` | Reads repriced against alternative tariff's historical rates |
| `simulation_in_progress` | Background simulation running; naive result shown for now |
| `simulation` | Full optimised simulation complete |

---

## Dashboard chart

Install the [ApexCharts card](https://github.com/RomRider/apexcharts-card) from HACS, then add this to your dashboard:

```yaml
type: custom:apexcharts-card
header:
  title: Monthly Tariff Comparison
  show: true
graph_span: 12month
series_in_graph: true
all_series_config:
  type: bar
series:
  - entity: sensor.monthly_tariff_comparison
    name: Net monthly cost
    data_generator: |
      return entity.attributes.tariffs.flatMap(t =>
        t.monthly.map(m => ({
          name: t.label,
          data: [[m.month + '-01', m.net_cost_gbp]]
        }))
      );
```

For a grouped bar chart comparing all tariffs side-by-side:

```yaml
type: custom:apexcharts-card
header:
  title: "What would I have paid? (last 12 months)"
  show: true
apex_config:
  chart:
    type: bar
  plotOptions:
    bar:
      columnWidth: 70%
      grouped: true
data_generator: |
  const tariffs = entity.attributes.tariffs || [];
  return tariffs.map(t => ({
    name: t.label,
    data: (t.monthly || []).map(m => [new Date(m.month + '-01').getTime(), m.net_cost_gbp])
  }));
series:
  - entity: sensor.monthly_tariff_comparison
    data_generator: "return [];"
```

---

## Refreshing manually

To force an immediate recalculation (e.g. after adding a new tariff):

**Developer Tools → Services → `battery_charge_calculator.refresh_tariff_comparison`**

Or in a YAML automation/script:

```yaml
service: battery_charge_calculator.refresh_tariff_comparison
```

The comparison normally updates automatically once a week.

---

## Notes and limitations

- **UK only** — requires an Octopus Energy account with smart meter half-hourly reads available via the Octopus API
- **12-month rolling window** — the comparison uses the most recent 12 months of meter data
- **Import only by default** — export comparison is available but requires your export MPAN and serial number to be configured
- **Standing charges** — included in the net cost calculation. The standing charge for each tariff is fetched from the Octopus API
- **Simulation accuracy** — the background simulation assumes zero solar generation (future improvement: use Open-Meteo historical solar irradiance). For solar-heavy homes, naive replay may actually be more representative in summer months
- **Data coverage** — the `data_quality_notes` attribute reports what percentage of slots had a direct rate lookup vs. forward-fill. Coverage below 90% may indicate a gap in the Octopus rate history for that tariff
