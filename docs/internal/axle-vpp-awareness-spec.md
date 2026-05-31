# Axle VPP Awareness Specification

## 1) Problem statement and expected user outcome

### Problem statement
The integration plans battery behavior using Octopus rates, weather, and site constraints, then dispatches inverter commands on a minute cadence. GivEnergy installations may also be controlled by Axle (VPP), which can impose temporary external control windows.

Without Axle awareness, local command dispatch can conflict with remote VPP control, creating unpredictable behavior and unclear user outcomes.

### Expected user outcome
When Axle remote control is active:
- Local charge/export command dispatch is paused safely.
- Users can see that dispatch is paused and why.
- Local scheduling resumes predictably after the window ends.

When Axle is disabled or unavailable, current non-Axle behavior remains unchanged by default.

## Implementation outcome (2026-05-28)

Implemented in current codebase:
- Axle-aware dispatch gating in `BatteryChargeCoordinator._async_update_data`.
- Active-window suppression of local `enableCharge` / `enableExport` dispatch.
- One-time neutralization on active-window entry when enabled.
- Active->inactive transition path that triggers immediate replanning with reason `axle_window_ended`.
- Fail-safe modes `open` (default) and `closed` for Axle source unavailable state.
- Diagnostic entity `sensor.axle_remote_control` with source/suppression/transition attributes.

Not implemented:
- No manual Axle refresh service.

## 2) Explicit assumptions and boundaries

### Current codebase realities (must remain true unless separately approved)
- Planning and command dispatch are separate concerns in `BatteryChargeCoordinator`.
- Planning runs via `octopus_state_change_listener`; command dispatch occurs in `_async_update_data` on a 1-minute update interval.
- Tariff comparison uses a separate coordinator (`TariffComparisonCoordinator`) and does not dispatch inverter commands.
- `simulate_only` prevents live MQTT command execution and must continue to do so.
- Config flow chain currently routes through `ml_settings`, then tariff comparison steps.

### In scope
- Optional Axle awareness for command suppression during remote-control windows.
- Axle window ingestion, normalization, freshness handling, and overlap evaluation.
- Minimal diagnostics (sensor state/attributes) and config options required for safe operation.
- Tests covering coordinator behavior, flow persistence, and regression safety.

### Non-goals
- No attempt to control Axle itself.
- No runtime dependency on `ha-axle-vpp`.
- No redesign of tariff comparison architecture.
- No multi-controller arbitration beyond Axle awareness.
- No change to planning optimization algorithm in MVP.

## 3) Proposed architecture and realistic file touchpoints

### Existing modules to extend
- `custom_components/battery_charge_calculator/const.py`
  - Add Axle option keys, defaults, source-status enums, suppression reason constants, and any new recalculation reason constants.
- `custom_components/battery_charge_calculator/config_schemas.py`
  - Add an Axle settings schema helper for both initial and options flow.
- `custom_components/battery_charge_calculator/config_flow.py`
  - Insert `axle_settings` step after `ml_settings` and before tariff comparison steps in both flows.
- `custom_components/battery_charge_calculator/coordinators.py`
  - Add Axle cache state, polling hooks, freshness evaluation, and pre-dispatch suppression gate in `_async_update_data`.
  - Keep tariff comparison wiring untouched.
- `custom_components/battery_charge_calculator/sensors/__init__.py`
  - Export any new Axle diagnostic sensor class.
- `custom_components/battery_charge_calculator/sensor.py`
  - Register Axle diagnostic sensor conditionally when Axle is enabled.
- `custom_components/battery_charge_calculator/strings.json`
  - Add strings for new flow step/fields and diagnostic sensor labels.
- `custom_components/battery_charge_calculator/translations/*.json`
  - Add corresponding translation keys for all newly introduced strings.
- `custom_components/battery_charge_calculator/services.yaml` (optional in MVP)
  - Only if adding explicit services like `refresh_axle_windows`.

### New modules proposed
- `custom_components/battery_charge_calculator/axle_client.py`
  - Thin HTTP client for fetching remote-control windows with timeout/retry behavior.
- `custom_components/battery_charge_calculator/axle_windows.py`
  - Window parsing, UTC normalization, merge, and overlap helpers.
- `custom_components/battery_charge_calculator/sensors/axle_remote_control.py`
  - Diagnostic sensor exposing active status and source health.

### Test modules likely touched
- `tests/unit/test_coordinator.py`
- `tests/unit/test_config_flow.py`
- New: `tests/unit/test_axle_windows.py`
- New: `tests/unit/test_axle_client.py`
- New: `tests/unit/test_coordinator_axle_awareness.py`
- Optional: `tests/integration/test_config_flow_integration.py`

## 4) Axle data acquisition strategy

### Inferred Axle API contract (from reference implementation)
Reference project `deanhalllincoln/ha-axle-vpp` currently uses:
- Method: `GET`
- Endpoint: `https://api.axle.energy/vpp/home-assistant/event`
- Auth header: `Authorization: Bearer <token>`
- Accept header: `Accept: application/json`

Returned JSON is treated as one current/upcoming event object (or no event). Fields consumed by the reference integration:
- `start_time` (required to treat response as an event)
- `end_time` (expected for valid window)
- `import_export` (control intent/type indicator from Axle)
- `updated_at` (source timestamp)

No-event behavior in the reference integration:
- If response is empty, null-like, or missing `start_time`, treat as "no active event".

For this repository, normalize Axle event data to an internal window model:
- `start` <- `start_time`
- `end` <- `end_time`
- `control_intent` <- `import_export` (pass-through, no enum coercion in MVP)
- `source_updated_at` <- `updated_at`

Integration logic must only depend on normalized fields, while storing raw intent value for diagnostics.

### Polling and retries
- Default poll interval: 60 seconds.
- Request timeout default: 10 seconds.
- Per-cycle retries: up to 2 retries with exponential backoff and jitter.
- During active window: keep 60-second polling to detect release quickly.
- On active->inactive transition, trigger an immediate coordinator refresh (do not wait for next minute tick).

### Cache and freshness
Coordinator runtime state should include:
- normalized window list
- last successful fetch timestamp (UTC)
- last error (redacted)
- source status enum: `fresh`, `stale`, `unavailable`

Freshness thresholds:
- `fresh`: success age <= 3 x poll interval
- `stale`: success age > 3 x poll interval and <= 30 minutes
- `unavailable`: success age > 30 minutes or no successful fetch yet

## 5) Window normalization and overlap semantics

### Normalization rules
- Convert all timestamps to timezone-aware UTC.
- Drop invalid windows where end <= start.
- Sort windows ascending by start time.
- Merge overlapping or near-adjacent windows from same source when gap <= 1 minute.

### Overlap rule
Suppression overlap uses half-open intervals:
- Slot interval: `[slot_start, slot_end)`
- Window overlaps slot when `window_start < slot_end` and `window_end > slot_start`

Boundary clarifications:
- Window ending exactly at slot start: not overlapping.
- Window starting exactly at slot end: not overlapping.

## 6) Coordinator behavior changes (dispatch gating)

### Current behavior summary
`_async_update_data` computes current active slot and sends GivEnergy MQTT commands (`enableCharge`, `enableExport`, or both disables) when not in simulate mode.

### MVP behavior
Add pre-dispatch gate in `_async_update_data`:
1. Evaluate remote-control-active from cached Axle windows at `now_utc`.
2. If Axle-aware mode is enabled and active:
   - Suppress local `enableCharge`/`enableExport` dispatch.
  - Default transition behavior: send one-time neutralization (`disableCharge` + `disableExport`) when entering active state.
   - Expose runtime flags/attributes indicating paused state and active window end.
3. If not active:
   - Follow existing dispatch path unchanged.
  - On window end, perform immediate refresh and resume dispatch path without waiting for next minute tick.

### Planning interaction
- Keep `octopus_state_change_listener` behavior unchanged in MVP.
- Do not block tariff-comparison coordinator updates.
- Emit an explicit recalculation/transition reason when resuming after Axle window end (for traceability in logs/diagnostics).

## 7) Fallback policy when Axle source degrades

### Default mode
- `open` (default, confirmed): if source is `unavailable`, continue local scheduling.
- While `stale`, cached overlapping windows still suppress dispatch.

### Strict mode
- `closed`: if source is `unavailable`, continue suppression for safety.

### Required visibility
Expose source health diagnostics:
- source status
- last success timestamp
- cache age seconds
- last error (sanitized)
- effective suppression reason

## 8) Configuration and UX impact

### New options (Axle step)
- `axle_enabled` (bool, default false)
- `axle_api_token` (text)
- `axle_poll_interval_seconds` (int, default 60, min 30, max 300)
- `axle_request_timeout_seconds` (int, default 10, min 3, max 30)
- `axle_fail_safe_mode` (`open` or `closed`, default `open`)
- `axle_neutralize_on_active_entry` (bool, default true)

Endpoint behavior:
- MVP default endpoint is fixed to `https://api.axle.energy/vpp/home-assistant/event` (inferred from reference integration).
- Optional base URL override is out of scope unless a concrete multi-environment requirement is provided.

Flow placement:
- `... -> ml_settings -> axle_settings -> tariff_comparison -> ...`

### Diagnostic sensor
Add one diagnostic entity for remote control state and health. Minimum attributes:
- active window start/end
- active window count
- next window start
- active control intent/type (from `import_export`, raw pass-through)
- source status
- last success
- last error
- suppression reason

## 9) Security and privacy

- Never log raw Axle token.
- Redact token from raised errors and diagnostics.
- Require HTTPS endpoints for token-bearing requests.
- Use Home Assistant aiohttp session and standard TLS defaults.
- Persist only data needed for operation/diagnostics (window times/status), not full raw payloads by default.

## 10) Acceptance criteria (implementation readiness)

1. With `axle_enabled = false`, command dispatch behavior is unchanged from current baseline.
2. With `axle_enabled = true` and active overlapping window, coordinator does not issue `enableCharge` or `enableExport`.
3. Transition into active window triggers neutralization by default, at most once per active period.
4. Transition out of active window triggers immediate refresh and resumes normal dispatch path without waiting for the next minute tick.
5. `simulate_only = true` still prevents live MQTT command dispatch in all Axle states.
6. `stale` state with cached overlap still suppresses dispatch and reports stale diagnostics.
7. `unavailable` state obeys configured fail-safe mode (`open` vs `closed`).
8. Tariff comparison coordinator behavior and refresh service remain unaffected by Axle feature enablement.
9. Config flow and options flow persist Axle settings and defaults without breaking existing entries.
10. All new strings/translation keys resolve without frontend placeholder errors.
11. Unit tests cover normalization, overlap boundaries, dispatch suppression, transitions, and fallback modes.
12. Regression tests verify no command-path changes for non-Axle users.

## 11) Rollout and safety controls

### Rollout plan
1. Phase 1 (MVP): config + client + window model + dispatch gate + diagnostics + core tests.
2. Phase 2 (hardening): optional manual refresh service, polling optimizations, additional translation/docs polish.

### Safety controls
- Feature is opt-in and defaults off.
- Behavior can be instantly reverted by toggling Axle feature off in options.
- Keep planning path untouched in MVP to reduce regression surface.
- Add explicit logging (info/debug) for state transitions without sensitive data.

## 12) Open decisions for confirmation

1. Confirm `import_export` value domain and semantics with Axle docs (for example: `import`/`export` strings vs numeric codes), while MVP keeps raw pass-through.
2. Confirm whether Axle may return multiple events/windows via a different endpoint or schema; current inferred contract assumes a single event response at `/vpp/home-assistant/event`.

## Reference
- Inspiration/reference implementation: https://github.com/deanhalllincoln/ha-axle-vpp
- This repository treats that project as design inspiration only, not a runtime dependency.

## Lead Review Notes

- Clarified coordinator boundaries so Axle gating applies only to command dispatch path, not planning or tariff comparison processing.
- Tightened file touchpoints to match actual repository layout (`sensors/__init__.py`, `sensor.py`, config flow ordering, separate tariff coordinator).
- Converted overlap/suppression behavior into explicit half-open interval semantics to avoid boundary ambiguity.
- Strengthened acceptance criteria around non-Axle regression safety, `simulate_only` behavior, and fail-safe mode handling.
- Added rollout safety controls and explicit open decisions requiring product-owner confirmation before implementation.
