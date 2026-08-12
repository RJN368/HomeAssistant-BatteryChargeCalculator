"""Unit tests verifying battery drains correctly during axle export events.

The bug: when an axle export window is active the engine adds
``axle_adjustment_kwh`` (= inverter_size_kw * overlap_hours) to the slot's
demand.  In the evaluator ``export`` branch:

    discharge_amount = max(0.0, min(max_discharge - net_demand, battery))

If the inflated ``net_demand`` exceeds ``max_discharge`` the clamped result is
negative, ``max(0.0, …)`` forces it to zero, and the battery stays constant
even though the inverter is actively discharging to the grid.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_charge_calculator.axle_windows import AxleWindow
from custom_components.battery_charge_calculator.genetic_evaluator import (
    GeneticEvaluator,
    Timeslot,
)
from custom_components.battery_charge_calculator.planning.providers.axle.axle_state import (
    AxleStateManager,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INVERTER_KW = 3.6
_EFFICIENCY = 0.9
_MAX_PER_SLOT = (_INVERTER_KW * _EFFICIENCY) / 2  # 1.62 kWh


def _evaluator(battery_start: float = 5.0) -> GeneticEvaluator:
    return GeneticEvaluator(
        battery_start=battery_start,
        standing_charge=0.0,
        inverter_size_kw=_INVERTER_KW,
        inverter_efficiency=_EFFICIENCY,
        battery_capacity_kwh=9.0,
    )


def _slot(demand: float, solar: float = 0.0) -> Timeslot:
    return Timeslot(
        "2026-08-12T18:00:00+00:00",
        import_price=0.25,
        export_price=0.15,
        demand_in=demand,
        solar_in=solar,
    )


def _axle_state_with_export_window(
    window_start: datetime,
    window_end: datetime,
) -> AxleStateManager:
    """Return an AxleStateManager with a single export-intent window cached."""
    from unittest.mock import MagicMock

    config_entry = MagicMock()
    config_entry.options = {}
    hass = MagicMock()
    manager = AxleStateManager(config_entry=config_entry, hass=hass)
    manager._cache["windows"] = [
        AxleWindow(start=window_start, end=window_end, control_intent="export")
    ]
    return manager


# ---------------------------------------------------------------------------
# AxleStateManager – slot_export_adjustment_kwh
# ---------------------------------------------------------------------------


class TestAxleExportAdjustment:
    """Verify that export windows produce a positive kWh adjustment per slot."""

    def test_full_overlap_returns_inverter_half_hour_energy(self) -> None:
        """A fully-overlapping 30-min export window should equal inverter_kw * 0.5."""
        start = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)
        end = datetime(2026, 8, 12, 18, 30, tzinfo=timezone.utc)
        manager = _axle_state_with_export_window(start, end)

        adjustment = manager.slot_export_adjustment_kwh(
            slot_start=start,
            slot_end=end,
            inverter_size_kw=_INVERTER_KW,
        )

        assert adjustment == pytest.approx(_INVERTER_KW * 0.5)

    def test_no_overlap_returns_zero(self) -> None:
        """A window that doesn't overlap the slot returns zero."""
        window_start = datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc)
        window_end = datetime(2026, 8, 12, 21, 0, tzinfo=timezone.utc)
        manager = _axle_state_with_export_window(window_start, window_end)

        slot_start = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)
        slot_end = datetime(2026, 8, 12, 18, 30, tzinfo=timezone.utc)
        adjustment = manager.slot_export_adjustment_kwh(
            slot_start=slot_start,
            slot_end=slot_end,
            inverter_size_kw=_INVERTER_KW,
        )

        assert adjustment == 0.0

    def test_partial_overlap_is_proportional(self) -> None:
        """15-minute overlap with a 30-minute slot returns half the full adjustment."""
        slot_start = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)
        slot_end = datetime(2026, 8, 12, 18, 30, tzinfo=timezone.utc)
        # Window starts 15 min into the slot
        window_start = datetime(2026, 8, 12, 18, 15, tzinfo=timezone.utc)
        window_end = datetime(2026, 8, 12, 19, 0, tzinfo=timezone.utc)
        manager = _axle_state_with_export_window(window_start, window_end)

        adjustment = manager.slot_export_adjustment_kwh(
            slot_start=slot_start,
            slot_end=slot_end,
            inverter_size_kw=_INVERTER_KW,
        )

        expected = _INVERTER_KW * (15 / 60)
        assert adjustment == pytest.approx(expected)

    def test_non_export_window_returns_zero(self) -> None:
        """Windows with non-export intent must not affect the adjustment."""
        from unittest.mock import MagicMock

        config_entry = MagicMock()
        config_entry.options = {}
        hass = MagicMock()
        manager = AxleStateManager(config_entry=config_entry, hass=hass)

        slot_start = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)
        slot_end = datetime(2026, 8, 12, 18, 30, tzinfo=timezone.utc)
        manager._cache["windows"] = [
            AxleWindow(start=slot_start, end=slot_end, control_intent="import")
        ]

        adjustment = manager.slot_export_adjustment_kwh(
            slot_start=slot_start,
            slot_end=slot_end,
            inverter_size_kw=_INVERTER_KW,
        )
        assert adjustment == 0.0


# ---------------------------------------------------------------------------
# GeneticEvaluator – export slot battery drain
# ---------------------------------------------------------------------------


class TestExportSlotBatteryDrain:
    """Battery must decrease when a slot is forced to export.

    These tests expose the bug where axle-inflated demand causes net_demand to
    exceed max_discharge, making discharge_amount clamp to zero.
    """

    def test_export_with_normal_demand_drains_battery(self) -> None:
        """Baseline: export with low demand (no axle inflation) drains battery."""
        ev = _evaluator(battery_start=5.0)
        ts = _slot(demand=0.2)  # typical home demand, no axle inflation

        battery_after = ev._evaluate_single_slot(ts, "export", 5.0)

        assert battery_after < 5.0, (
            f"Battery should drain during export but stayed at {battery_after:.4f} kWh"
        )

    def test_export_with_axle_inflated_demand_still_drains_battery(self) -> None:
        """Bug case: axle adjustment inflates demand beyond max_discharge.

        axle_adjustment = inverter_kw * 0.5h = 3.6 * 0.5 = 1.8 kWh
        normal demand ≈ 0.2 kWh → inflated demand = 2.0 kWh
        max_discharge = 1.62 kWh
        net_demand = 2.0 → max_discharge - net_demand = -0.38 → discharge = 0 (BUG)
        """
        axle_adjustment = _INVERTER_KW * 0.5  # 1.8 kWh
        normal_demand = 0.2
        inflated_demand = normal_demand + axle_adjustment  # 2.0 kWh

        ev = _evaluator(battery_start=5.0)
        ts = _slot(demand=inflated_demand)

        battery_after = ev._evaluate_single_slot(ts, "export", 5.0)

        assert battery_after < 5.0, (
            f"Battery should drain during axle export but stayed at {battery_after:.4f} kWh. "
            f"net_demand ({inflated_demand:.2f}) exceeded max_discharge ({_MAX_PER_SLOT:.2f}), "
            "causing discharge_amount to clamp to zero."
        )

    def test_export_drains_up_to_max_discharge_per_slot(self) -> None:
        """Battery drain should not exceed max_discharge regardless of demand."""
        ev = _evaluator(battery_start=5.0)
        # Very high inflated demand
        ts = _slot(demand=5.0)

        battery_after = ev._evaluate_single_slot(ts, "export", 5.0)
        drained = 5.0 - battery_after

        # Drain must be non-negative (battery should actually decrease)
        assert drained >= 0.0, (
            f"Battery increased during export: was 5.0, now {battery_after:.4f}"
        )
        # And must not exceed inverter capability
        assert drained <= _MAX_PER_SLOT + 1e-6, (
            f"Drain {drained:.4f} kWh exceeded inverter max {_MAX_PER_SLOT:.4f} kWh"
        )

    def test_export_schedule_with_forced_action_drains_battery_across_slots(self) -> None:
        """evaluate_schedule with forced export slots must produce a lower final battery.

        Simulates two consecutive axle export slots where demand is inflated by the
        axle adjustment, then verifies the battery has actually decreased.
        """
        axle_adjustment = _INVERTER_KW * 0.5  # 1.8 kWh per slot
        normal_demand = 0.2
        inflated_demand = normal_demand + axle_adjustment  # 2.0 kWh

        battery_start = 5.0
        ev = GeneticEvaluator(
            battery_start=battery_start,
            standing_charge=0.0,
            inverter_size_kw=_INVERTER_KW,
            inverter_efficiency=_EFFICIENCY,
            battery_capacity_kwh=9.0,
        )

        slot_time = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)
        for i in range(2):
            ev.add_data(
                slot_time + timedelta(minutes=30 * i),
                import_price=0.25,
                export_price=0.15,
                demand_in=inflated_demand,
                solar_in=0.0,
            )

        ev.set_forced_actions(["export", "export"])
        schedule = ["export", "export"]
        ev.evaluate_schedule(schedule)

        # After two export slots the battery must be lower than the start
        final_battery = ev.timeslots[-1].initial_power
        assert final_battery < battery_start, (
            f"Battery did not drain across axle export slots: "
            f"started at {battery_start:.2f} kWh, ended at {final_battery:.2f} kWh"
        )
