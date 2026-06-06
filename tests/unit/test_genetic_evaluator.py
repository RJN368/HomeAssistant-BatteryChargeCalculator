import unittest
from unittest.mock import patch

from custom_components.battery_charge_calculator.genetic_evaluator import (
    GeneticEvaluator,
    Timeslot,
)


class TestGeneticEvaluator(unittest.TestCase):
    def test_calculate_batterystate_from_index_handles_none(self):
        # Setup: battery_start is None, should be float
        evaluator = GeneticEvaluator(battery_start=None, standing_charge=0.1)
        # Add a timeslot with valid values
        evaluator.timeslots = [Timeslot("2024-04-04T00:00:00", 0.1, 0.1, 1.0, 1.0)]
        # Should not raise TypeError
        try:
            evaluator._calculate_batterystate_from_index(0, evaluator.battery_start)
        except TypeError as e:
            self.fail(f"_calculate_batterystate_from_index raised TypeError: {e}")

    def test_calculate_batterystate_from_index_handles_timeslot_none(self):
        # Setup: battery_start is float, but timeslot demand/solar is None
        evaluator = GeneticEvaluator(battery_start=5.0, standing_charge=0.1)
        # Add a timeslot with None for demand and solar
        evaluator.timeslots = [Timeslot("2024-04-04T00:00:00", 0.1, 0.1, None, None)]
        # Should not raise TypeError
        try:
            evaluator._calculate_batterystate_from_index(0, evaluator.battery_start)
        except TypeError as e:
            self.fail(f"_calculate_batterystate_from_index raised TypeError: {e}")


class TestGeneticEvaluatorInverterParams(unittest.TestCase):
    """Tests for inverter size and efficiency configuration."""

    def test_defaults_match_expected_slot_capacity(self):
        """Default 3.6 kW inverter at 90 % efficiency → 1.62 kWh per slot."""
        evaluator = GeneticEvaluator(battery_start=5.0, standing_charge=0.1)
        expected = (3.6 * 0.9) / 2
        self.assertAlmostEqual(evaluator.max_charge_per_slot, expected, places=6)
        self.assertAlmostEqual(evaluator.max_discharge, expected, places=6)

    def test_custom_inverter_size_scales_slot_capacity(self):
        """5 kW inverter at 90 % efficiency → 2.25 kWh per slot."""
        evaluator = GeneticEvaluator(
            battery_start=5.0,
            standing_charge=0.1,
            inverter_size_kw=5.0,
            inverter_efficiency=0.9,
        )
        expected = (5.0 * 0.9) / 2
        self.assertAlmostEqual(evaluator.max_charge_per_slot, expected, places=6)
        self.assertAlmostEqual(evaluator.max_discharge, expected, places=6)

    def test_charge_and_discharge_limits_are_equal(self):
        """max_charge_per_slot and max_discharge are always derived from the same formula."""
        evaluator = GeneticEvaluator(
            battery_start=5.0,
            standing_charge=0.1,
            inverter_size_kw=6.0,
            inverter_efficiency=0.85,
        )
        self.assertEqual(evaluator.max_charge_per_slot, evaluator.max_discharge)

    def test_low_efficiency_reduces_slot_capacity(self):
        """Lower efficiency directly reduces per-slot capacity."""
        evaluator_high = GeneticEvaluator(
            battery_start=5.0,
            standing_charge=0.1,
            inverter_size_kw=5.0,
            inverter_efficiency=0.95,
        )
        evaluator_low = GeneticEvaluator(
            battery_start=5.0,
            standing_charge=0.1,
            inverter_size_kw=5.0,
            inverter_efficiency=0.80,
        )
        self.assertGreater(
            evaluator_high.max_charge_per_slot,
            evaluator_low.max_charge_per_slot,
        )

    def test_larger_inverter_increases_slot_capacity(self):
        """Larger inverter size directly increases per-slot capacity."""
        evaluator_small = GeneticEvaluator(
            battery_start=5.0,
            standing_charge=0.1,
            inverter_size_kw=3.0,
            inverter_efficiency=0.9,
        )
        evaluator_large = GeneticEvaluator(
            battery_start=5.0,
            standing_charge=0.1,
            inverter_size_kw=6.0,
            inverter_efficiency=0.9,
        )
        self.assertGreater(
            evaluator_large.max_charge_per_slot,
            evaluator_small.max_charge_per_slot,
        )


class TestGeneticEvaluatorBatteryCapacity(unittest.TestCase):
    """Tests for battery capacity configuration."""

    def test_default_capacity_is_9_kwh(self):
        """When no battery_capacity_kwh is supplied the default is 9.0 kWh."""
        evaluator = GeneticEvaluator(battery_start=5.0, standing_charge=0.1)
        self.assertEqual(evaluator.max_battery_capacity, 9.0)

    def test_custom_capacity_is_applied(self):
        """Supplying battery_capacity_kwh sets max_battery_capacity accordingly."""
        evaluator = GeneticEvaluator(
            battery_start=5.0,
            standing_charge=0.1,
            battery_capacity_kwh=12.0,
        )
        self.assertEqual(evaluator.max_battery_capacity, 12.0)

    def test_charge_does_not_exceed_custom_capacity(self):
        """A charge action must not push the battery above the configured capacity."""
        capacity = 6.0
        evaluator = GeneticEvaluator(
            battery_start=5.5,
            standing_charge=0.1,
            inverter_size_kw=5.0,
            inverter_efficiency=0.9,
            battery_capacity_kwh=capacity,
        )
        timeslot = Timeslot(
            "2026-04-04T12:00:00",
            import_price=0.10,
            export_price=0.05,
            demand_in=0.0,
            solar_in=0.0,
        )
        battery_after = evaluator._evaluate_single_slot(timeslot, "charge", 5.5)
        self.assertLessEqual(battery_after, capacity)


class TestGeneticEvaluatorForcedActions(unittest.TestCase):
    def test_evaluate_schedule_respects_forced_export(self):
        evaluator = GeneticEvaluator(battery_start=2.0, standing_charge=0.0)
        evaluator.add_data(
            "2026-04-04T12:00:00",
            import_price=0.20,
            export_price=0.30,
            demand_in=0.0,
            solar_in=0.0,
        )
        evaluator.set_forced_actions(["export"])

        cost = evaluator.evaluate_schedule(["charge"])

        self.assertEqual(evaluator.timeslots[0].charge_option, "export")
        self.assertLess(cost, 0.0)

    def test_create_population_keeps_forced_slots_fixed(self):
        evaluator = GeneticEvaluator(battery_start=2.0, standing_charge=0.0)
        evaluator.population_size = 20
        evaluator.add_data("2026-04-04T00:00:00", 0.20, 0.10, 0.3, 0.0)
        evaluator.add_data("2026-04-04T00:30:00", 0.20, 0.30, 0.3, 0.0)
        evaluator.add_data("2026-04-04T01:00:00", 0.20, 0.10, 0.3, 0.0)
        evaluator.set_forced_actions([None, "export", None])

        population = evaluator.create_population()

        self.assertTrue(population)
        self.assertTrue(all(schedule[1] == "export" for schedule in population))

    def test_unconstrained_slots_still_vary_with_forced_constraints(self):
        evaluator = GeneticEvaluator(battery_start=2.0, standing_charge=0.0)
        evaluator.population_size = 40
        evaluator.add_data("2026-04-04T00:00:00", 0.20, 0.10, 0.3, 0.0)
        evaluator.add_data("2026-04-04T00:30:00", 0.20, 0.10, 0.3, 0.0)
        evaluator.add_data("2026-04-04T01:00:00", 0.20, 0.10, 0.3, 0.0)
        evaluator.set_forced_actions(["export", None, None])

        population = evaluator.create_population()
        unconstrained_actions = {schedule[1] for schedule in population}

        self.assertTrue(all(schedule[0] == "export" for schedule in population))
        self.assertGreater(len(unconstrained_actions), 1)

    def test_mutation_avoids_forced_slot(self):
        evaluator = GeneticEvaluator(battery_start=2.0, standing_charge=0.0)
        evaluator.population_size = 4
        evaluator.generations = 1
        evaluator.add_data("2026-04-04T00:00:00", 0.20, 0.10, 0.3, 0.0)
        evaluator.add_data("2026-04-04T00:30:00", 0.20, 0.10, 0.3, 0.0)
        evaluator.add_data("2026-04-04T01:00:00", 0.20, 0.10, 0.3, 0.0)
        evaluator.set_forced_actions(["export", None, None])

        with (
            patch.object(
                evaluator,
                "create_population",
                return_value=[
                    ["discharge", "discharge", "discharge"],
                    ["discharge", "charge", "discharge"],
                    ["discharge", "discharge", "charge"],
                    ["discharge", "charge", "charge"],
                ],
            ),
            patch(
                "custom_components.battery_charge_calculator.genetic_evaluator.random.sample",
                return_value=(
                    ["discharge", "discharge", "discharge"],
                    ["discharge", "charge", "charge"],
                ),
            ),
            patch(
                "custom_components.battery_charge_calculator.genetic_evaluator.random.randint",
                return_value=1,
            ),
            patch(
                "custom_components.battery_charge_calculator.genetic_evaluator.random.random",
                return_value=0.0,
            ),
            patch(
                "custom_components.battery_charge_calculator.genetic_evaluator.random.choice",
                side_effect=[1, "charge", 1, "charge", 1, "charge", 1, "charge"],
            ),
        ):
            timeslots, _ = evaluator.evaluate()

        self.assertIsNotNone(timeslots)
        self.assertEqual(timeslots[0].charge_option, "export")


if __name__ == "__main__":
    unittest.main()
