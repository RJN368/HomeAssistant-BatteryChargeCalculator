# Axel VPP Awareness - Implementation Plan

Date: 2026-05-28
Owner: Keaton (Lead)
Source spec: `docs/internal/axel-vpp-awareness-spec.md`

## Current implementation snapshot (2026-05-28)

- Dispatch gating, stale/unavailable fallback handling, config flow/option flow wiring, and diagnostic sensor are implemented.
- `axel_settings` is inserted between `ml_settings` and `tariff_comparison` in both initial and options flows.
- Manual Axel refresh service is deferred and not implemented.

## Scope And Guardrails

This plan implements Axel-aware command suppression in the main coordinator while preserving current behavior for non-Axel users.

Non-negotiable contracts:
- [ ] `simulate_only` continues to block all live MQTT command dispatch.
- [ ] With `axel_enabled = false`, behavior is unchanged from baseline.
- [ ] Tariff comparison remains isolated (`TariffComparisonCoordinator` unchanged behavior).
- [ ] Planning path (`octopus_state_change_listener`) remains unchanged in MVP.

## Phase Plan (Ordered, With Dependencies)

## Phase 0 - Baseline Safety Harness (Complexity: S)

Goal: lock current behavior with regression tests before implementation.

Dependencies:
- None.

Implementation checklist:
- [ ] Add/refresh baseline tests for `_async_update_data` command dispatch (`charge`, `export`, `discharge`, `simulate_only`).
- [ ] Add explicit regression test asserting no tariff coordinator side effects from main coordinator refresh path.
- [ ] Snapshot current flow progression through `ml_settings -> tariff_comparison` to protect insertion point.

Test-first checkpoints:
- Before phase:
  - [ ] Run `pytest tests/unit/test_coordinator.py tests/unit/test_coordinator_debounce.py tests/unit/test_config_flow.py -q`
- After phase:
  - [ ] Confirm all baseline tests pass unchanged.

## Phase 1 - Axel Contracts And Data Layer (Complexity: M)

Goal: implement isolated Axel ingestion and window semantics without changing dispatch yet.

Dependencies:
- Phase 0 complete.

Implementation checklist:
- [ ] Add Axel constants/defaults/status enums in `const.py`.
- [ ] Create `axel_client.py` for API fetch, timeout, retry, and token redaction.
- [ ] Create `axel_windows.py` for parse/normalize/merge/overlap helpers (UTC-aware, half-open overlap).
- [ ] Add coordinator-private Axel cache shape and freshness evaluation helpers (no dispatch gating yet).

Test-first checkpoints:
- Before phase:
  - [ ] Add new tests for normalization and overlap boundaries in `tests/unit/test_axel_windows.py`.
  - [ ] Add new tests for API no-event handling, timeout, and redaction in `tests/unit/test_axel_client.py`.
- After phase:
  - [ ] Run `pytest tests/unit/test_axel_windows.py tests/unit/test_axel_client.py -q`.
  - [ ] Run baseline suite from Phase 0 to verify no regressions.

## Phase 2 - Coordinator Dispatch Gate And Transitions (Complexity: L)

Goal: enforce Axel-aware suppression in `_async_update_data` with safe transitions.

Dependencies:
- Phase 1 complete.

Implementation checklist:
- [ ] Wire Axel polling lifecycle into `BatteryChargeCoordinator` (startup + minute cadence refresh of source state).
- [ ] Add pre-dispatch gate in `_async_update_data`:
  - [ ] if Axel-active and enabled: suppress `enableCharge`/`enableExport`.
  - [ ] one-time neutralize (`disableCharge` + `disableExport`) on inactive->active transition when configured.
  - [ ] preserve `simulate_only` behavior.
- [ ] Implement active->inactive immediate resume refresh path.
- [ ] Add explicit recalculation/transition reason constant(s) for diagnostics/log traceability.
- [ ] Keep `TariffComparisonCoordinator` wiring untouched.

Test-first checkpoints:
- Before phase:
  - [ ] Add `tests/unit/test_coordinator_axel_awareness.py` covering:
    - active suppression,
    - neutralize-on-entry once-per-window,
    - stale/unavailable fail-open vs fail-closed,
    - immediate resume path,
    - simulate-only invariant.
- After phase:
  - [ ] Run `pytest tests/unit/test_coordinator_axel_awareness.py tests/unit/test_coordinator.py tests/unit/test_coordinator_debounce.py -q`.
  - [ ] Verify non-Axel tests remain green without fixture changes where possible.

## Phase 3 - Config Flow, Options Flow, And Diagnostics Sensor (Complexity: M)

Goal: expose Axel feature flags safely and surface state/health diagnostics.

Dependencies:
- Phase 2 complete.

Implementation checklist:
- [ ] Add Axel schema helper in `config_schemas.py`.
- [ ] Insert `axel_settings` step after `ml_settings` and before tariff comparison in both config and options flows.
- [ ] Persist defaults safely for existing entries.
- [ ] Add Axel diagnostic sensor class and register conditionally when `axel_enabled`.
- [ ] Update `strings.json` and `translations/en.json` for all new fields/messages.

Test-first checkpoints:
- Before phase:
  - [ ] Extend `tests/unit/test_config_flow.py` for step ordering and persistence.
  - [ ] Add/extend sensor registration tests (if absent, create focused unit test for conditional Axel sensor registration).
- After phase:
  - [ ] Run `pytest tests/unit/test_config_flow.py tests/integration/test_config_flow_integration.py -q`.
  - [ ] Run translation/string key sanity checks via existing CI/test path.

## Phase 4 - Hardening, Docs, And Release Readiness (Complexity: S)

Goal: production-hardening and rollout safety.

Dependencies:
- Phases 1-3 complete.

Implementation checklist:
- [ ] Add/verify sanitized logging around Axel fetch errors and state transitions.
- [ ] Validate fallback behavior (`stale`, `unavailable`) and diagnostics attributes end-to-end.
- [ ] Update docs (`_docs` and/or `README.md`) with configuration and fail-safe guidance.
- [ ] Optional MVP+ service entry only if manual Axel refresh is accepted (`services.yaml`).

Test-first checkpoints:
- Before phase:
  - [ ] Add regression tests for stale cache overlap and unavailable mode branching.
- After phase:
  - [ ] Run targeted full suite:
    - `pytest tests/unit/test_axel_windows.py tests/unit/test_axel_client.py tests/unit/test_coordinator_axel_awareness.py tests/unit/test_config_flow.py tests/unit/test_coordinator.py tests/unit/test_tariff_comparison_coordinator.py -q`

## File-By-File Task List (Likely Touched)

- [ ] `custom_components/battery_charge_calculator/const.py`
  - add Axel option keys/defaults, source statuses, suppression reasons, transition reason constants.
- [ ] `custom_components/battery_charge_calculator/config_schemas.py`
  - add `_axel_settings_schema(...)` helper.
- [ ] `custom_components/battery_charge_calculator/config_flow.py`
  - add `async_step_axel_settings`; wire order `ml_settings -> axel_settings -> tariff_comparison` for both initial and options flow handlers.
- [ ] `custom_components/battery_charge_calculator/coordinators.py`
  - add Axel cache/polling/freshness helpers and dispatch suppression gate in `_async_update_data`.
- [ ] `custom_components/battery_charge_calculator/axel_client.py` (new)
  - HTTP client for Axel event endpoint.
- [ ] `custom_components/battery_charge_calculator/axel_windows.py` (new)
  - normalize/merge/overlap semantics.
- [ ] `custom_components/battery_charge_calculator/sensors/axel_remote_control.py` (new)
  - diagnostic sensor entity.
- [ ] `custom_components/battery_charge_calculator/sensors/__init__.py`
  - export new Axel sensor class.
- [ ] `custom_components/battery_charge_calculator/sensor.py`
  - conditionally register Axel diagnostic sensor.
- [ ] `custom_components/battery_charge_calculator/strings.json`
  - flow strings and labels.
- [ ] `custom_components/battery_charge_calculator/translations/en.json`
  - mirrored translation keys.
- [ ] `custom_components/battery_charge_calculator/services.yaml` (optional, non-MVP)
  - only if adding manual Axel refresh service.
- [ ] `tests/unit/test_axel_windows.py` (new)
- [ ] `tests/unit/test_axel_client.py` (new)
- [ ] `tests/unit/test_coordinator_axel_awareness.py` (new)
- [ ] `tests/unit/test_coordinator.py`
  - extend baseline dispatch assertions for Axel-disabled path.
- [ ] `tests/unit/test_config_flow.py`
  - add flow-order and persistence assertions for Axel options.
- [ ] `tests/integration/test_config_flow_integration.py` (likely)
  - validate full-step progression including Axel step.

## Data And Behavior Contracts To Preserve

- [ ] Contract: `simulate_only`
  - no live MQTT dispatch in all modes (Axel active/inactive/stale/unavailable).
- [ ] Contract: non-Axel regressions
  - Axel defaults off; existing entries without Axel options keep identical runtime behavior.
- [ ] Contract: tariff comparison isolation
  - no Axel coupling in tariff coordinator classes, data structures, or refresh services.
- [ ] Contract: interval semantics
  - overlap evaluation remains half-open: `[slot_start, slot_end)`.
- [ ] Contract: security
  - never log/token-leak Axel API credentials.

## Risk Register (Mitigation + Rollback)

1. Risk: accidental suppression for non-Axel users.
   - Mitigation: feature default off; guard all gating with `axel_enabled`; regression tests for disabled path.
   - Rollback: set `axel_enabled` false in options; revert coordinator gate commit.

2. Risk: stale/unavailable handling suppresses too long (or not enough).
   - Mitigation: explicit freshness thresholds and fail-safe mode tests.
   - Rollback: switch fail-safe mode to `open`; disable feature.

3. Risk: token leakage in logs/exceptions.
   - Mitigation: centralized redaction helper in Axel client; test for redacted errors.
   - Rollback: disable Axel and rotate token; patch logging paths.

4. Risk: config flow regression due to new step insertion.
   - Mitigation: dedicated step-order tests in both config and options flows.
   - Rollback: feature-flag route around Axel step in flow; hotfix revert.

5. Risk: dispatch race around active/inactive transitions.
   - Mitigation: track prior active state, one-time neutralization flag, transition tests.
   - Rollback: temporarily disable neutralize-on-entry and rely on suppression-only.

6. Risk: accidental impact to tariff comparison.
   - Mitigation: no code sharing changes in tariff comparison package; explicit regression tests.
   - Rollback: revert any non-essential tariff file changes.

## Definition Of Done

- [ ] All acceptance criteria in `docs/internal/axel-vpp-awareness-spec.md` are met.
- [ ] New Axel feature is fully opt-in and defaults safe (`axel_enabled = false`).
- [ ] Unit tests added for client, window normalization, and coordinator gating.
- [ ] Existing coordinator/config/tariff tests pass unchanged or with intentional minimal updates.
- [ ] Strings and translations resolve with no placeholder/key errors.
- [ ] Logs contain transition/fallback context without sensitive data.
- [ ] Documentation updated for configuration and fail-safe behavior.

## Merge Readiness Checklist

- [ ] `pytest` targeted suites pass locally in the dev container.
- [ ] No unrelated file churn in diff.
- [ ] Config flow migration impact reviewed for existing entries.
- [ ] Reviewer walkthrough includes one active-window and one source-outage scenario.
- [ ] Rollback instructions verified (disable option + revert patch path).

## Deferred Stretch Tasks (Post-MVP)

- [ ] Manual `refresh_axel_windows` service.
- [ ] Adaptive polling optimization outside active windows.
- [ ] Multi-window/multi-source arbitration extension.
- [ ] Richer diagnostics entity set (separate health and state sensors).
- [ ] Optional endpoint override for non-production Axel environments.

## Recommended PR Slicing Strategy

Recommendation: multi-PR (4 PRs), not single PR.

Why:
- Isolates high-risk coordinator dispatch changes from lower-risk schema/UI work.
- Makes rollback and bisect faster if a regression appears in live dispatch.
- Enables earlier review on data contracts (window semantics) before behavior changes.

Suggested slices:
1. PR-1: Axel contracts + client + window utilities + unit tests (`axel_client`, `axel_windows`, `const` additions).
2. PR-2: Coordinator gating/transitions + coordinator Axel tests.
3. PR-3: Config and options flow (`axel_settings`) + strings/translations + flow tests.
4. PR-4: Diagnostics sensor wiring + docs polish + optional hardening changes.
