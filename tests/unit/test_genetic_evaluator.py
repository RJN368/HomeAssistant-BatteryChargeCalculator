import unittest
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


if __name__ == "__main__":
    unittest.main()
