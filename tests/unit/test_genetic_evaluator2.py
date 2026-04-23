import unittest
from unittest.mock import patch
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


class TestExportModeRegression(unittest.TestCase):
    def test_export_mode_does_not_negative_discharge_when_demand_high(self):
        """Export mode must not increase battery due to negative discharge amounts."""
        ev = _evaluator()
        ts = _slot(import_price=0.32, export_price=0.08, demand=5.0, solar=0.0)
        start_battery = 3.0

        next_battery = ev._evaluate_single_slot(ts, "export", start_battery)

        self.assertLessEqual(next_battery, start_battery)


class TestEvaluateFinalSelectionRegression(unittest.TestCase):
    def test_evaluate_selects_best_from_final_generation(self):
        """Final selected schedule must be the best child, not just the first child generated."""
        ev = _evaluator()
        ev.population_size = 4
        ev.generations = 1
        ev.timeslots = [
            _slot(0.2, 0.1, 0.0, 0.0),
            _slot(0.2, 0.1, 0.0, 0.0),
            _slot(0.2, 0.1, 0.0, 0.0),
        ]
        ev.num_slots = len(ev.timeslots)

        best_parent = ["export", "export", "export"]
        second_parent = ["charge", "charge", "charge"]
        base_population = [
            best_parent,
            second_parent,
            ["discharge", "discharge", "discharge"],
            ["charge", "export", "discharge"],
        ]

        score_map = {
            tuple(best_parent): 0.0,
            tuple(second_parent): 10.0,
            ("discharge", "discharge", "discharge"): 50.0,
            ("charge", "export", "discharge"): 20.0,
            ("charge", "charge", "export"): 9.0,
            ("export", "charge", "charge"): 1.0,
        }

        sample_sequence = [
            [second_parent, best_parent],
            [best_parent, second_parent],
            [second_parent, best_parent],
            [best_parent, second_parent],
        ]
        sample_iter = iter(sample_sequence)

        with (
            patch.object(ev, "create_population", return_value=base_population),
            patch.object(
                ev, "evaluate_schedule", side_effect=lambda s: score_map[tuple(s)]
            ),
            patch.object(ev, "_log_schedule"),
            patch(
                "custom_components.battery_charge_calculator.genetic_evaluator.random.sample",
                side_effect=lambda _parents, _k: next(sample_iter),
            ),
            patch(
                "custom_components.battery_charge_calculator.genetic_evaluator.random.randint",
                side_effect=[2, 1, 2, 1],
            ),
            patch(
                "custom_components.battery_charge_calculator.genetic_evaluator.random.random",
                return_value=1.0,
            ),
        ):
            _timeslots, optimal_cost = ev.evaluate()

        self.assertEqual(optimal_cost, 1.0)


class TestExportFocusedScheduling(unittest.TestCase):
    def test_create_export_ideal_schedule_prefers_export_then_charge_then_discharge(
        self,
    ):
        ev = _evaluator()
        ev.timeslots = [
            _slot(import_price=0.10, export_price=0.30, demand=0.0, solar=0.0),
            _slot(import_price=-0.01, export_price=0.05, demand=0.0, solar=0.0),
            _slot(import_price=0.25, export_price=0.10, demand=0.0, solar=0.0),
        ]
        ev.num_slots = len(ev.timeslots)

        schedule = ev.create_export_ideal_schedule()

        self.assertEqual(schedule, ["export", "charge", "discharge"])

    def test_export_seed_can_outperform_import_focused_seed(self):
        """When export prices dominate and battery has energy, export schedule should win."""
        ev = GeneticEvaluator(battery_start=9.0, standing_charge=0.0)
        ev.timeslots = [
            _slot(import_price=0.05, export_price=0.40, demand=0.0, solar=0.0),
            _slot(import_price=0.05, export_price=0.40, demand=0.0, solar=0.0),
            _slot(import_price=0.05, export_price=0.40, demand=0.0, solar=0.0),
        ]
        ev.num_slots = len(ev.timeslots)

        export_schedule = ev.create_export_ideal_schedule()
        import_focused_schedule = ev.create_ideal_schedule()

        export_cost = ev.evaluate_schedule(export_schedule)
        import_focused_cost = ev.evaluate_schedule(import_focused_schedule)

        self.assertLess(
            export_cost,
            import_focused_cost,
            msg=(
                f"Expected export-focused schedule to be cheaper. "
                f"export_cost={export_cost:.4f}, import_focused_cost={import_focused_cost:.4f}"
            ),
        )


class TestEvaluateExportsInHighExportWindow(unittest.TestCase):
    def test_evaluate_marks_export_action_for_best_plan(self):
        """End-to-end evaluate should pick a plan with export actions when export rates dominate."""
        ev = GeneticEvaluator(battery_start=9.0, standing_charge=0.0)
        ev.generations = 0
        ev.timeslots = [
            _slot(import_price=0.05, export_price=0.40, demand=0.0, solar=0.0),
            _slot(import_price=0.06, export_price=0.35, demand=0.0, solar=0.0),
            _slot(import_price=0.05, export_price=0.30, demand=0.0, solar=0.0),
        ]
        ev.num_slots = len(ev.timeslots)

        export_plan = ["export", "export", "discharge"]
        non_export_plan = ["charge", "discharge", "charge"]

        with patch.object(
            ev, "create_population", return_value=[non_export_plan, export_plan]
        ):
            timeslots, _optimal_cost = ev.evaluate()

        self.assertIsNotNone(timeslots)
        self.assertIn("export", [slot.charge_option for slot in timeslots])


if __name__ == "__main__":
    unittest.main()
