from datetime import datetime, timedelta
import logging
import numpy as np
import pandas as pd
from scipy.interpolate import interp1d

# Redefining the known points for the new interpolation
known_points_new = np.array([[-6, 60], [0, 45], [6, 20], [15, 0]])


class PowerCalulator:
    def __init__(self):
        self._logging = logging.getLogger(__name__)
        self._base_consumption_30mins = [
            0.250 for i in range(30)
        ]  ##morning to afternoon 250 watts
        self._base_consumption_30mins.extend(
            [0.500 for i in range(18)]
        )  ## evening 500 watts

        # Creating a quadratic interpolation function based on the new known points
        self.curve_function = interp1d(
            known_points_new[:, 0],
            known_points_new[:, 1],
            kind="quadratic",
            fill_value="extrapolate",
        )

    def _temp_to_power(self, x):
        if x > 15:
            return 0

        return self.curve_function(x)

    def from_temp_and_time(self, current_time: datetime, tempdata: float):
        if tempdata is None:
            return 0

        base_time_index = current_time.hour * 2
        if current_time.minute > 30:
            base_time_index = base_time_index + 1

        base_consumption = self._base_consumption_30mins[base_time_index]

        ## now add on the amounnt of consumption based on temp
        return self._temp_to_power(tempdata) / 24 + base_consumption
