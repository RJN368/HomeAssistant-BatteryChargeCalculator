# Axle VPP Awareness

Axle VPP awareness lets Battery Charge Calculator pause local battery command dispatch when Axle is actively controlling your system.

## What this does

When enabled, the integration checks Axle event state and:

- Suppresses local `enableCharge` and `enableExport` commands during active Axle windows.
- Optionally sends one-time neutralization (`disableCharge` + `disableExport`) when Axle control first becomes active.
- Resumes normal control as soon as Axle control ends.

This feature is opt-in. If not enabled, behavior is unchanged.

## Configuration options

You can configure Axle awareness in the integration options flow.

- `axle_enabled`: Turn Axle awareness on/off.
- `axle_api_token`: Axle API bearer token.
- `axle_poll_interval_seconds`: How often Axle state is refreshed.
- `axle_request_timeout_seconds`: API request timeout.
- `axle_fail_safe_mode`:
  - `open`: If Axle data is unavailable, continue normal scheduling.
  - `closed`: If Axle data is unavailable, continue suppression for safety.
- `axle_neutralize_on_active_entry`: Send one-time neutralization at active window start.

## Fail-safe behavior

When Axle data is stale or unavailable, behavior depends on mode:

- `open` mode (default): existing scheduling continues when Axle state is unavailable.
- `closed` mode: scheduling remains suppressed when Axle state is unavailable.

## Diagnostic sensor

When Axle awareness is enabled, the integration exposes a diagnostic sensor for status and troubleshooting.

Typical states:

- `active`
- `inactive`
- `unavailable`

Key attributes include:

- `source_status`
- `suppression_reason`
- `last_transition_reason`
- `active_window_start`
- `active_window_end`
- `cache_age_seconds`
- `fail_safe_mode`

## Notes

- Token values are never intended to be logged.
- This feature is awareness/suppression only. It does not control Axle itself.
- If you use `simulate_only`, live MQTT dispatch remains disabled as usual.
