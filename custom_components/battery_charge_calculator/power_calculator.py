from datetime import datetime, timedelta
import logging
import numpy as np
import pandas as pd
from scipy.interpolate import interp1d

# Default known points used when no custom points are supplied
known_points_new = np.array([[-6, 60], [0, 45], [6, 20], [15, 0]])


class PowerCalulator:
    """
    PowerCalulator estimates home power consumption for a given time and temperature.
    Supports four modes:
    1. none: No electric heating; only base load is used.
    2. interpolation: User provides (temperature, energy use) points.
    3. electric: Direct electric heating using heat loss and COP=1.
    4. heatpump: Heat pump heating using heat loss and COP > 1.
    """

    def __init__(
        self,
        heating_type: str = "interpolation",  # 'none', 'interpolation', 'electric', 'heatpump'
        cop: float = 1.0,
        heat_loss: float = None,  # W/°C, only for 'electric', 'heatpump'
        indoor_temp: float = 20.0,  # °C
        heating_flow_temp: float = 45.0,  # °C — heat pump flow/delivery temperature
        known_points=None,  # list of (temp, energy_use) tuples
        base_load_kwh_30min: float = None,  # kWh per 30-min slot; None → built-in profile
    ):
        """
        Args:
            heating_type: Calculation mode.
            cop: Coefficient of Performance at rated conditions (outdoor 7°C / A7 test).
            heat_loss: Heat loss of the house (W/°C).
            indoor_temp: Target indoor temperature (°C).
            heating_flow_temp: Heat pump flow temperature in °C (e.g. 45 for radiators,
                35 for underfloor). Used to calculate temperature-dependent COP.
            known_points: List of (temperature, energy_use) tuples for interpolation mode.
            base_load_kwh_30min: Fixed base load per 30-min slot. When set this overrides
                the built-in time-of-day profile. When None the built-in profile is used.
        """
        self._logging = logging.getLogger(__name__)
        self.heating_type = heating_type
        self.cop = cop
        self.heat_loss = heat_loss
        self.indoor_temp = indoor_temp
        self.heating_flow_temp = heating_flow_temp

        # Base load — either a fixed value or the built-in time-of-day profile
        if base_load_kwh_30min is not None:
            self._base_consumption_30mins = [base_load_kwh_30min] * 48
        else:
            self._base_consumption_30mins = [0.250 for _ in range(30)]
            self._base_consumption_30mins.extend([0.500 for _ in range(18)])

        # Validate and set known points (only matters for interpolation mode)
        if known_points is None:
            self.known_points = np.array([[-6, 60], [0, 45], [6, 20], [15, 0]])
        else:
            arr = np.array(known_points)
            if arr.ndim != 2 or arr.shape[1] != 2:
                raise ValueError(
                    "known_points must be a list of (temp, energy_use) pairs"
                )
            self.known_points = arr

        self.curve_function = interp1d(
            self.known_points[:, 0],
            self.known_points[:, 1],
            kind="quadratic",
            fill_value="extrapolate",
        )

    def set_known_points(self, known_points):
        """Update the interpolation points at runtime."""
        arr = np.array(known_points)
        if arr.ndim != 2 or arr.shape[1] != 2:
            raise ValueError("known_points must be a list of (temp, energy_use) pairs")
        self.known_points = arr
        self.curve_function = interp1d(
            self.known_points[:, 0],
            self.known_points[:, 1],
            kind="quadratic",
            fill_value="extrapolate",
        )

    def _temp_to_power_interpolation(self, x):
        """Interpolate energy use for a given temperature using known points."""
        # Clamp: above the warmest known point → 0 kWh; below the coldest → use
        # the coldest point value (avoids scipy bounds error and prevents
        # negative extrapolation at extreme cold temperatures).
        if x > self.known_points[-1, 0]:
            return 0
        x_clamped = max(x, self.known_points[0, 0])
        return self.curve_function(x_clamped)

    def _effective_cop(self, outdoor_temp: float) -> float:
        """Return temperature-adjusted COP using a Carnot-based model.

        For COP=1 (direct electric resistance), returns 1.0 unchanged — the line
        is correctly straight because efficiency doesn't vary.

        For COP>1 (heat pump), COP drops as outdoor temperature falls:
            COP(T) = rated_cop × (T_flow - T_ref) / (T_flow - T_outdoor)
        where T_ref = 7°C is the standard A7 rated test condition.

        COP is clamped to a minimum of 1.0 (a heat pump never performs worse than
        direct electric) and a maximum of rated_cop × 3 (prevents unrealistic spikes
        when outdoor temp approaches flow temp).
        """
        if self.cop <= 1.0:
            return 1.0
        T_ref = 7.0  # A7 standard test condition
        delta_ref = self.heating_flow_temp - T_ref
        delta_now = self.heating_flow_temp - outdoor_temp
        if delta_now <= 0:
            # Outdoor temp at or above flow temp — very high COP, cap it
            return self.cop * 3.0
        return max(1.0, self.cop * delta_ref / delta_now)

    def _temp_to_power_heatloss(self, outdoor_temp):
        """Calculate energy use from heat loss and temperature-adjusted COP."""
        if self.heat_loss is None:
            self._logging.warning("Heat loss not set; returning 0.")
            return 0
        delta_t = self.indoor_temp - outdoor_temp
        if delta_t <= 0:
            return 0
        heat_load = self.heat_loss * delta_t  # Watts
        kwh_30min = (heat_load / 1000) * 0.5
        return kwh_30min / self._effective_cop(outdoor_temp)

    def heating_kwh_for_temp(self, outdoor_temp: float) -> float:
        """Return the heating-only kWh for a 30-min slot at the given outdoor temperature.

        Does not include base load.  Used to build the power-vs-temperature curve.
        """
        if self.heating_type == "interpolation":
            return max(0.0, float(self._temp_to_power_interpolation(outdoor_temp)) / 24)
        if self.heating_type in ("electric", "heatpump"):
            return max(0.0, float(self._temp_to_power_heatloss(outdoor_temp)))
        return 0.0

    def power_curve(
        self, temp_min: float = -20.0, temp_max: float = 20.0, step: float = 1.0
    ) -> list[dict]:
        """Return a list of {temp, kwh_heating, kwh_total} dicts for the curve.

        kwh_total uses the midday base load (slot 24, i.e. 12:00) as a representative
        constant so the chart shows a meaningful absolute level without varying by time.
        """
        midday = datetime(2000, 1, 1, 12, 0)
        base_midday = self._base_consumption_30mins[24]
        result = []
        temp = temp_min
        while temp <= temp_max + 1e-9:
            heating = self.heating_kwh_for_temp(temp)
            result.append(
                {
                    "temp": round(temp, 1),
                    "kwh_heating": round(heating, 4),
                    "kwh_total": round(heating + base_midday, 4),
                }
            )
            temp += step
        return result

    def from_temp_and_time(self, current_time: datetime, tempdata: float):
        """
        Estimate total power consumption for a given time and temperature.
        Args:
            current_time: datetime object for the time slot.
            tempdata: Outdoor temperature (°C).
        Returns:
            Estimated kWh for the 30-minute slot.
        """
        if tempdata is None:
            return 0

        base_time_index = current_time.hour * 2
        if current_time.minute > 30:
            base_time_index += 1
        base_consumption = self._base_consumption_30mins[base_time_index]

        # Choose calculation method
        if self.heating_type == "interpolation":
            heating = self._temp_to_power_interpolation(tempdata) / 24  # legacy scaling
        elif self.heating_type in ("electric", "heatpump"):
            heating = self._temp_to_power_heatloss(tempdata)
        else:
            # 'none' or any unrecognised type — no heating contribution
            heating = 0

        return heating + base_consumption

    def physics_estimate(self, current_time: datetime, tempdata: float) -> float:
        """Return physics-based power estimate for a 30-min slot.

        Identical to from_temp_and_time() — provided as a named alias so that
        MLPowerEstimator can explicitly request the physics-only estimate when
        building training data, making the distinction from ML-corrected
        estimates clear at the call site.
        """
        return self.from_temp_and_time(current_time, tempdata)
