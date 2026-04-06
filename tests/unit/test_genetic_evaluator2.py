import unittest
from custom_components.battery_charge_calculator.genetic_evaluator import (
    GeneticEvaluator,
    Timeslot,
)


def _evaluator():
    return GeneticEvaluator(battery_start=5.0, standing_charge=0.1)


def _slot(import_price, export_price, demand, solar):
    return Timeslot("2024-04-04T00:00:00", import_price, export_price, demand, solar)


class TestEvaluateSingleSlotMiscellaneous(unittest.TestCase):
    def test_evaluate_single_slot_handles_none_battery(self):
        ts = _slot(0.1, 0.1, 1.0, 1.0)
        try:
            _evaluator()._evaluate_single_slot(ts, "charge", None)
        except TypeError as e:
            self.fail(f"_evaluate_single_slot raised TypeError: {e}")

    def test_evaluate_single_slot_handles_none_battery_and_zero(self):
        ts = _slot(0.1, 0.1, 1.0, 1.0)
        try:
            _evaluator()._evaluate_single_slot(ts, "charge", 0)
        except TypeError as e:
            self.fail(f"_evaluate_single_slot raised TypeError: {e}")


class TestDischargeFormula(unittest.TestCase):
    """Discharge slots should follow: next_battery = battery - demand + solar (capped to max_capacity)."""

    def test_discharge_normal_demand_exceeds_solar(self):
        """Battery drops by net demand when demand > solar."""
        ev = _evaluator()
        ts = _slot(import_price=0.32, export_price=0.13, demand=1.19, solar=0.42)
        # battery - demand + solar = 3.69 - 1.19 + 0.42 = 2.92
        next_battery = ev._evaluate_single_slot(ts, "discharge", 3.69)
        self.assertAlmostEqual(next_battery, 2.92, places=2)
        self.assertAlmostEqual(ts.cost, 0.0, places=4)

    def test_discharge_solar_exceeds_demand_battery_below_max(self):
        """Solar surplus charges the battery when there is headroom below max capacity."""
        ev = _evaluator()
        # battery=2.19, demand=0.65, solar=1.28 → 2.19 - 0.65 + 1.28 = 2.82
        ts = _slot(import_price=0.32, export_price=0.08, demand=0.65, solar=1.28)
        next_battery = ev._evaluate_single_slot(ts, "discharge", 2.19)
        self.assertAlmostEqual(next_battery, 2.82, places=2)
        self.assertAlmostEqual(ts.cost, 0.0, places=4)

    def test_discharge_solar_surplus_overflows_max_capacity(self):
        """When solar surplus pushes battery past max_capacity, overflow is exported.

        From the log: 06/04 12:30 — battery=8.21, demand=0.43, solar=2.44
          net_demand = 0.43 - 2.44 = -2.01
          raw_battery = 8.21 - (-2.01) = 10.22  (exceeds max 9)
          overflow = 10.22 - 9 = 1.22 kWh exported
          expected cost = export_price * overflow * -1 = 0.0676 * 1.22 * -1 ≈ -0.0825
        """
        ev = _evaluator()
        ts = _slot(import_price=0.3231, export_price=0.0676, demand=0.43, solar=2.44)
        next_battery = ev._evaluate_single_slot(ts, "discharge", 8.21)
        self.assertAlmostEqual(next_battery, 9.0, places=2)
        self.assertAlmostEqual(ts.cost, -0.0825, places=2)


class TestChargeSolarSurplusFullBattery(unittest.TestCase):
    """Charge slots when solar > demand and battery is at or near max_capacity.

    From the log: 06/04 13:00 — battery=9.00, demand=0.37, solar=2.58
      net_demand = 0.37 - 2.58 = -2.21 (surplus)
      Battery is already full (9.00 kWh).
      Expected behaviour:
        - Battery stays at 9.00 (or possibly lower if solar exported)
        - Battery should NOT decrease due to a negative charge_amount
        - Cost should NOT be negative import cost
    """

    def test_charge_full_battery_solar_surplus_battery_does_not_decrease(self):
        """Battery must not fall when in charge mode with a solar surplus and full battery."""
        ev = _evaluator()
        ts = _slot(import_price=0.3231, export_price=0.0688, demand=0.37, solar=2.58)
        next_battery = ev._evaluate_single_slot(ts, "charge", 9.0)
        self.assertAlmostEqual(
            next_battery,
            9.0,
            places=2,
            msg=f"Battery changed to {next_battery:.2f} — should stay at max capacity",
        )

    def test_charge_full_battery_solar_surplus_cost_is_export_credit(self):
        """When battery is full and solar surplus exists, cost should be an export credit.

        From the log: 06/04 13:00 — battery=9.00, demand=0.37, solar=2.58
          solar_surplus = 2.58 - 0.37 = 2.21 kWh exported
          expected cost = -export_price * 2.21 = -0.0688 * 2.21 ≈ -0.1521
        The buggy value was -0.7135 (phantom import credit at full import rate).
        """
        ev = _evaluator()
        ts = _slot(import_price=0.3231, export_price=0.0688, demand=0.37, solar=2.58)
        ev._evaluate_single_slot(ts, "charge", 9.0)
        expected_cost = -(0.0688 * 2.21)
        self.assertAlmostEqual(
            ts.cost,
            expected_cost,
            places=2,
            msg=f"Expected export credit ≈{expected_cost:.4f}, got {ts.cost:.4f}",
        )


if __name__ == "__main__":
    unittest.main()
