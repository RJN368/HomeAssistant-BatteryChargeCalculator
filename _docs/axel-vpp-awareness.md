# Axel VPP Awareness

Axel VPP awareness lets Battery Charge Calculator pause local battery command dispatch when Axel is actively controlling your system.

## What this does

When enabled, the integration checks Axel event state and:

- Suppresses local `enableCharge` and `enableExport` commands during active Axel windows.
- Optionally sends one-time neutralization (`disableCharge` + `disableExport`) when Axel control first becomes active.
- Resumes normal control as soon as Axel control ends.

This feature is opt-in. If not enabled, behavior is unchanged.

## Configuration options

You can configure Axel awareness in the integration options flow.

- `axel_enabled`: Turn Axel awareness on/off.
- `axel_api_token`: Axel API bearer token.
- `axel_poll_interval_seconds`: How often Axel state is refreshed.
- `axel_request_timeout_seconds`: API request timeout.
- `axel_fail_safe_mode`:
  - `open`: If Axel data is unavailable, continue normal scheduling.
  - `closed`: If Axel data is unavailable, continue suppression for safety.
- `axel_neutralize_on_active_entry`: Send one-time neutralization at active window start.

## Fail-safe behavior

When Axel data is stale or unavailable, behavior depends on mode:

- `open` mode (default): existing scheduling continues when Axel state is unavailable.
- `closed` mode: scheduling remains suppressed when Axel state is unavailable.

## Diagnostic sensor

When Axel awareness is enabled, the integration exposes a diagnostic sensor for status and troubleshooting.

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
- This feature is awareness/suppression only. It does not control Axel itself.
- If you use `simulate_only`, live MQTT dispatch remains disabled as usual.
