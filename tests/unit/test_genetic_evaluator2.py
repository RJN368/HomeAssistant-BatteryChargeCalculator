import unittest
from custom_components.battery_charge_calculator.genetic_evaluator import (
    GeneticEvaluator,
    Timeslot,
)


class TestGeneticEvaluator(unittest.TestCase):
    def test_evaluate_single_slot_handles_none_battery(self):
        evaluator = GeneticEvaluator(battery_start=5.0, standing_charge=0.1)
        ts = Timeslot("2024-04-04T00:00:00", 0.1, 0.1, 1.0, 1.0)
        # Should not raise TypeError if battery is None
        try:
            evaluator._evaluate_single_slot(ts, "charge", None)
        except TypeError as e:
            self.fail(f"_evaluate_single_slot raised TypeError: {e}")

    def test_evaluate_single_slot_handles_none_battery_and_zero(self):
        evaluator = GeneticEvaluator(battery_start=5.0, standing_charge=0.1)
        ts = Timeslot("2024-04-04T00:00:00", 0.1, 0.1, 1.0, 1.0)
        # Should not raise TypeError if battery is 0
        try:
            evaluator._evaluate_single_slot(ts, "charge", 0)
        except TypeError as e:
            self.fail(f"_evaluate_single_slot raised TypeError: {e}")


if __name__ == "__main__":
    unittest.main()
