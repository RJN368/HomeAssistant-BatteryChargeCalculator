# Session Log — 2026-04-10 — ML Final Plan

**Date:** 2026-04-10
**Session type:** Final architectural close-out
**Scribe:** Scribe

---

## Summary

This session closed all remaining open items in the ML Power Estimation feature plan. The plan is ready for Robert's review.

---

## What happened

Keaton, Hockney, and Fenster each delivered their final specs via the inbox mechanism.

**Keaton (keaton-decisions-final.md):**
- Closed D-4 (model file path → `hass.config.path(...)`)
- Closed D-6 (model choice → HistGBR primary + Ridge cold-start fallback)
- Closed D-7 (feature set → Hockney's full 15-feature vector + `octopus_import_kwh` as feature #16 in "both" mode)
- Closed D-8 blend weight (Hockney's 0→1 ramp over N_clean 500–2500)
- Closed D-9 (retrain schedule → monthly + RMSE-triggered)
- Closed D-10 (training gate → N_clean ≥ 500 AND temp_range ≥ 5°C)
- Closed D-13 Q7–Q11 (all Robert-confirmed answers incorporated)
- Added D-16 (ML feature fully opt-in; graceful degradation architecture)
- Added D-17 placeholder delegated to Hockney (EV auto-detection)

**Hockney (hockney-ev-detection.md):**
- Delivered EV / large-load auto-detection algorithm spec (Hybrid D: residual-IQR + persistence gate + absolute floor)
- Fills D-17 — algorithm is parameter-free with documented derivation of all constants
- Extended D-12 with step 6 (EV block exclusion before z-score fencing)
- Extended D-11 (MLModelStatusSensor) with EV detection attributes

**Fenster (fenster-both-mode-spec.md):**
- Delivered implementation spec for `OCTOPUS_METER_SERIAL` config field (optional; auto-discoverable via Octopus API)
- Delivered "both" source mode spec: `octopus_import_kwh` as conditional feature #16; DST-safe UTC normalisation; inference-time slot-mean imputation; model compatibility validation
- Added D-18 to decisions.md capturing these decisions

---

## Plan status

All D-1 through D-18 are now CLOSED except:
- D-17 has 3 minor open questions for Robert (immersion heater / DHW exemption; audit log file; 22 kW charger — the last is already resolved as no special treatment needed)
- D-18 has 3 minor open questions for Robert (auto-fill UX; multiple meters selector; export meter for v2)

**The core implementation plan is complete. Recommending Robert review decisions.md and confirm the two short open-question lists before implementation begins.**

---

## Decisions merged

All three inbox files merged into `.squad/decisions.md` and deleted.
