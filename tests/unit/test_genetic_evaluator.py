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


if __name__ == "__main__":
    unittest.main()
