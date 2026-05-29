"""Unit tests for tariff comparison simulator rate lookup behavior."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from custom_components.battery_charge_calculator.tariff_comparison.simulator import (
    TariffSimulator,
)

UTC = timezone.utc


class _DummyPowerCalculator:
    def from_temp_and_time(self, current_time: datetime, tempdata: float) -> float:
        _ = (current_time, tempdata)
        return 0.0


class _FakeTimeslot:
    def __init__(self, cost: float) -> None:
        self.cost = cost


class _FakeEvaluator:
    def __init__(
        self,
        battery_start: float,
        standing_charge: float,
        inverter_size_kw: float = 3.6,
        inverter_efficiency: float = 0.9,
        battery_capacity_kwh: float = 9.0,
    ) -> None:
        _ = (
            battery_start,
            standing_charge,
            inverter_size_kw,
            inverter_efficiency,
            battery_capacity_kwh,
        )
        self._slots: list[_FakeTimeslot] = []

    def add_data(
        self,
        start_datetime: datetime,
        import_price: float,
        export_price: float,
        demand_in: float,
        solar_in: float,
    ) -> None:
        _ = (start_datetime, export_price, demand_in, solar_in)
        # Cost is set to import price so total equals the sum of all looked-up rates.
        self._slots.append(_FakeTimeslot(cost=float(import_price)))

    def evaluate(self):
        return self._slots, sum(s.cost for s in self._slots)


@pytest.fixture
def _patch_fake_evaluator(monkeypatch):
    monkeypatch.setattr(
        "custom_components.battery_charge_calculator.genetic_evaluator.GeneticEvaluator",
        _FakeEvaluator,
    )


def test_simulator_forward_fills_sparse_import_rate_map(_patch_fake_evaluator):
    """Sparse rate maps should not collapse missing slots to 0p."""
    simulator = TariffSimulator()
    run_date = date(2026, 4, 1)

    # Provide only one rate key in the day map (00:30). The simulator should
    # still use a non-zero rate for all slots by nearest-known fallback.
    sparse_map = {
        datetime(2026, 4, 1, 0, 30, tzinfo=UTC): 20.0,
    }

    result = simulator.simulate_day(
        date_obj=run_date,
        hourly_temps=[10.0] * 24,
        rate_map_import=sparse_map,
        rate_map_export=None,
        power_calculator=_DummyPowerCalculator(),
        inverter_size_kw=3.6,
        inverter_efficiency=0.9,
        battery_capacity_kwh=9.0,
        battery_start_kwh=4.5,
    )

    # 48 slots * 20p per slot = 960p
    assert result["import_cost_pence"] == pytest.approx(960.0)
    assert result["export_earnings_pence"] == pytest.approx(0.0)
