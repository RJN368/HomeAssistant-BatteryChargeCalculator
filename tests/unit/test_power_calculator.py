"""Unit tests for the power_calculator module."""

from datetime import datetime, timezone

import pytest

from custom_components.battery_charge_calculator.power_calculator import PowerCalulator


@pytest.fixture
def calc() -> PowerCalulator:
    return PowerCalulator()


class TestTempToPower:
    def test_above_15_returns_zero(self, calc):
        assert calc._temp_to_power(16) == 0
        assert calc._temp_to_power(20) == 0
        assert calc._temp_to_power(15.1) == 0

    def test_exactly_15_returns_zero(self, calc):
        assert calc._temp_to_power(15) == 0

    def test_cold_returns_positive_power(self, calc):
        power_0c = calc._temp_to_power(0)
        assert power_0c > 0

    def test_colder_needs_more_power(self, calc):
        """Power demand should increase as temperature drops."""
        assert calc._temp_to_power(-5) > calc._temp_to_power(5)
        assert calc._temp_to_power(5) > calc._temp_to_power(10)

    def test_known_point_at_0c(self, calc):
        """Interpolation is anchored at 0°C → ~45 W."""
        power = calc._temp_to_power(0)
        assert 40 < power < 55

    def test_known_point_at_6c(self, calc):
        """Interpolation is anchored at 6°C → ~20 W."""
        power = calc._temp_to_power(6)
        assert 15 < power < 30


class TestFromTempAndTime:
    def _dt(self, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 4, 4, hour, minute, 0, tzinfo=timezone.utc)

    def test_returns_zero_for_none_temp(self, calc):
        result = calc.from_temp_and_time(self._dt(12), None)
        assert result == 0

    def test_warm_weather_returns_base_consumption_only(self, calc):
        """At 20°C, heating demand is zero, so result == base consumption for that slot."""
        result = calc.from_temp_and_time(self._dt(12), 20.0)
        # Midday slot — base is 0.250 kWh per 30 mins
        assert abs(result - 0.250) < 0.01

    def test_cold_weather_adds_to_base(self, calc):
        """At 0°C, result should exceed the warm-weather value."""
        warm = calc.from_temp_and_time(self._dt(12), 20.0)
        cold = calc.from_temp_and_time(self._dt(12), 0.0)
        assert cold > warm

    def test_evening_base_higher_than_daytime(self, calc):
        """Evening slots (18:00+) have 0.500 kWh base vs 0.250 kWh daytime."""
        daytime = calc.from_temp_and_time(self._dt(10), 20.0)
        evening = calc.from_temp_and_time(self._dt(20), 20.0)
        assert evening > daytime

    def test_minute_above_30_uses_second_slot_of_hour(self, calc):
        """The :31 minute mark should use slot index hour*2+1."""
        result_00 = calc.from_temp_and_time(self._dt(10, 0), 20.0)
        result_31 = calc.from_temp_and_time(self._dt(10, 31), 20.0)
        # Both are in the morning base band, so values should be equal
        assert result_00 == pytest.approx(result_31)

    def test_base_consumption_array_has_48_entries(self, calc):
        """30 + 18 = 48 half-hour slots per day."""
        assert len(calc._base_consumption_30mins) == 48

    def test_all_hours_return_positive_value_at_cold_temp(self, calc):
        for hour in range(24):
            val = calc.from_temp_and_time(self._dt(hour), 0.0)
            assert val > 0, f"Expected positive value at hour {hour}"
