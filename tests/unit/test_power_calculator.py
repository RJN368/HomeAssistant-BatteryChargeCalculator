"""Unit tests for the power_calculator module."""

from datetime import datetime, timezone

import pytest

from custom_components.battery_charge_calculator.power_calculator import PowerCalulator


@pytest.fixture
def calc() -> PowerCalulator:
    return PowerCalulator()


@pytest.fixture
def electric_calc() -> PowerCalulator:
    """Electric resistance heater, heat loss 100 W/°C, indoor 20°C, COP 1.0."""
    return PowerCalulator(
        heating_type="electric", cop=1.0, heat_loss=100.0, indoor_temp=20.0
    )


@pytest.fixture
def heatpump_calc() -> PowerCalulator:
    """Heat pump, heat loss 100 W/°C, indoor 20°C, COP 3.0."""
    return PowerCalulator(
        heating_type="heatpump", cop=3.0, heat_loss=100.0, indoor_temp=20.0
    )


class TestTempToPowerInterpolation:
    def test_above_15_returns_zero(self, calc):
        assert calc._temp_to_power_interpolation(16) == 0
        assert calc._temp_to_power_interpolation(20) == 0
        assert calc._temp_to_power_interpolation(15.1) == 0

    def test_exactly_15_returns_zero(self, calc):
        assert calc._temp_to_power_interpolation(15) == 0

    def test_cold_returns_positive_power(self, calc):
        power_0c = calc._temp_to_power_interpolation(0)
        assert power_0c > 0

    def test_colder_needs_more_power(self, calc):
        """Power demand should increase as temperature drops."""
        assert calc._temp_to_power_interpolation(
            -5
        ) > calc._temp_to_power_interpolation(5)
        assert calc._temp_to_power_interpolation(5) > calc._temp_to_power_interpolation(
            10
        )

    def test_known_point_at_0c(self, calc):
        """Interpolation is anchored at 0°C → ~45 W."""
        power = calc._temp_to_power_interpolation(0)
        assert 40 < power < 55

    def test_known_point_at_6c(self, calc):
        """Interpolation is anchored at 6°C → ~20 W."""
        power = calc._temp_to_power_interpolation(6)
        assert 15 < power < 30


class TestHeatLossCalculation:
    def test_electric_zero_delta_returns_zero(self, electric_calc):
        """No temperature difference = no heating needed."""
        assert electric_calc._temp_to_power_heatloss(20.0) == 0

    def test_electric_warm_outside_returns_zero(self, electric_calc):
        """Warmer outside than inside = no heating."""
        assert electric_calc._temp_to_power_heatloss(25.0) == 0

    def test_electric_cold_returns_positive(self, electric_calc):
        result = electric_calc._temp_to_power_heatloss(0.0)
        assert result > 0

    def test_electric_heat_load_formula(self, electric_calc):
        """heat_loss=100 W/°C, delta_t=10°C → 1000 W → 0.5 kWh/hr → 0.5 kWh per 30min slot, COP=1."""
        result = electric_calc._temp_to_power_heatloss(10.0)
        # delta_t = 20 - 10 = 10, heat_load = 100*10 = 1000W, kwh_30min = (1000/1000)*0.5 = 0.5
        assert abs(result - 0.5) < 0.001

    def test_heatpump_cop_divides_consumption(self, electric_calc, heatpump_calc):
        """At the A7 rated outdoor temp (7°C) effective COP equals the rated COP,
        so the heat pump should use exactly 1/3 the electricity of direct electric."""
        elec = electric_calc._temp_to_power_heatloss(7.0)
        pump = heatpump_calc._temp_to_power_heatloss(7.0)
        assert abs(pump - elec / 3) < 0.001

    def test_no_heat_loss_set_returns_zero(self):
        calc = PowerCalulator(heating_type="electric", cop=1.0, heat_loss=None)
        assert calc._temp_to_power_heatloss(0.0) == 0

    def test_colder_outdoor_needs_more_power(self, electric_calc):
        assert electric_calc._temp_to_power_heatloss(
            -5.0
        ) > electric_calc._temp_to_power_heatloss(10.0)


class TestCustomKnownPoints:
    def test_custom_points_used_for_interpolation(self):
        custom_points = [[-10, 80], [0, 40], [10, 10], [15, 0]]
        calc = PowerCalulator(known_points=custom_points)
        assert calc._temp_to_power_interpolation(0) == pytest.approx(40, abs=5)

    def test_set_known_points_updates_curve(self, calc):
        new_points = [[-5, 100], [0, 50], [10, 5], [15, 0]]
        calc.set_known_points(new_points)
        result = calc._temp_to_power_interpolation(0)
        assert result == pytest.approx(50, abs=5)

    def test_invalid_known_points_raises(self):
        with pytest.raises(ValueError):
            PowerCalulator(known_points=[[1, 2, 3], [4, 5, 6]])

    def test_set_known_points_invalid_raises(self, calc):
        with pytest.raises(ValueError):
            calc.set_known_points([[1, 2, 3]])


class TestFromTempAndTime:
    def _dt(self, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 4, 4, hour, minute, 0, tzinfo=timezone.utc)

    def test_returns_zero_for_none_temp(self, calc):
        result = calc.from_temp_and_time(self._dt(12), None)
        assert result == 0

    def test_warm_weather_returns_base_consumption_only(self, calc):
        """At 20°C, heating demand is zero, so result == base consumption for that slot."""
        result = calc.from_temp_and_time(self._dt(12), 20.0)
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
        assert result_00 == pytest.approx(result_31)

    def test_base_consumption_array_has_48_entries(self, calc):
        """30 + 18 = 48 half-hour slots per day."""
        assert len(calc._base_consumption_30mins) == 48

    def test_all_hours_return_positive_value_at_cold_temp(self, calc):
        for hour in range(24):
            val = calc.from_temp_and_time(self._dt(hour), 0.0)
            assert val > 0, f"Expected positive value at hour {hour}"

    def test_electric_mode_cold_weather(self, electric_calc):
        """In electric mode, cold weather should add more than base."""
        warm = electric_calc.from_temp_and_time(self._dt(12), 25.0)
        cold = electric_calc.from_temp_and_time(self._dt(12), 0.0)
        assert cold > warm

    def test_heatpump_uses_less_electricity_than_electric(
        self, electric_calc, heatpump_calc
    ):
        """At same temperature, heat pump uses less electricity."""
        elec = electric_calc.from_temp_and_time(self._dt(12), 0.0)
        pump = heatpump_calc.from_temp_and_time(self._dt(12), 0.0)
        assert pump < elec

    def test_unknown_heating_type_returns_base_only(self):
        calc = PowerCalulator(heating_type="none")
        result = calc.from_temp_and_time(self._dt(12), 0.0)
        assert abs(result - 0.250) < 0.01


# ---------------------------------------------------------------------------
# PowerCurve
# ---------------------------------------------------------------------------


class TestPowerCurve:
    def test_curve_length_is_41_for_default_range(self):
        calc = PowerCalulator(heating_type="electric", cop=1.0, heat_loss=100.0)
        curve = calc.power_curve(-20, 20, 1)
        assert len(curve) == 41

    def test_curve_keys_present(self):
        calc = PowerCalulator(heating_type="electric", cop=1.0, heat_loss=100.0)
        point = calc.power_curve(-20, 20, 1)[0]
        assert "temp" in point
        assert "kwh_heating" in point
        assert "kwh_total" in point

    def test_curve_temp_range(self):
        calc = PowerCalulator(heating_type="electric", cop=1.0, heat_loss=100.0)
        curve = calc.power_curve(-20, 20, 1)
        assert curve[0]["temp"] == -20.0
        assert curve[-1]["temp"] == 20.0

    def test_heating_decreases_as_temp_increases(self):
        calc = PowerCalulator(
            heating_type="electric", cop=1.0, heat_loss=100.0, indoor_temp=20.0
        )
        curve = calc.power_curve(-10, 19, 1)
        heatings = [p["kwh_heating"] for p in curve]
        assert heatings == sorted(heatings, reverse=True)

    def test_none_type_heating_always_zero(self):
        calc = PowerCalulator(heating_type="none")
        curve = calc.power_curve(-20, 20, 1)
        assert all(p["kwh_heating"] == 0.0 for p in curve)

    def test_kwh_total_equals_heating_plus_base(self):
        calc = PowerCalulator(heating_type="electric", cop=1.0, heat_loss=100.0)
        curve = calc.power_curve(-5, -5, 1)  # single point
        point = curve[0]
        assert (
            abs(
                point["kwh_total"]
                - (point["kwh_heating"] + calc._base_consumption_30mins[24])
            )
            < 1e-6
        )

    def test_heatpump_cop_reduces_heating_kwh(self):
        # At the rated reference temp (7°C outdoor) effective COP == rated COP
        electric = PowerCalulator(heating_type="electric", cop=1.0, heat_loss=100.0)
        pump = PowerCalulator(heating_type="electric", cop=3.0, heat_loss=100.0)
        e_curve = electric.power_curve(7, 7, 1)[0]
        p_curve = pump.power_curve(7, 7, 1)[0]
        assert p_curve["kwh_heating"] < e_curve["kwh_heating"]

    def test_variable_cop_makes_curve_nonlinear(self):
        # With COP > 1, electrical demand should curve upward at cold temps faster
        # than a straight line — COP at -10°C must be lower than COP at +7°C
        pump = PowerCalulator(
            heating_type="electric", cop=3.0, heat_loss=100.0, heating_flow_temp=45.0
        )
        cop_cold = pump._effective_cop(-10.0)
        cop_rated = pump._effective_cop(7.0)
        cop_mild = pump._effective_cop(15.0)
        assert cop_cold < cop_rated < cop_mild

    def test_effective_cop_equals_1_for_direct_electric(self):
        calc = PowerCalulator(heating_type="electric", cop=1.0, heat_loss=100.0)
        assert calc._effective_cop(-20.0) == 1.0
        assert calc._effective_cop(0.0) == 1.0
        assert calc._effective_cop(15.0) == 1.0

    def test_effective_cop_at_rated_conditions(self):
        # At 7°C outdoor (A7 reference), effective COP must equal the rated COP
        pump = PowerCalulator(
            heating_type="electric", cop=3.0, heat_loss=100.0, heating_flow_temp=45.0
        )
        assert abs(pump._effective_cop(7.0) - 3.0) < 1e-9

    def test_heating_kwh_for_temp_matches_curve(self):
        calc = PowerCalulator(heating_type="electric", cop=1.0, heat_loss=100.0)
        assert (
            calc.heating_kwh_for_temp(0.0)
            == calc.power_curve(0, 0, 1)[0]["kwh_heating"]
        )
