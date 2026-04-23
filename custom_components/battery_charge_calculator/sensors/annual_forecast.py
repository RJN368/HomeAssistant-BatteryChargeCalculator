"""Annual Energy Forecast sensor.

Exposes the ML model's per-day-of-year average consumption as a 365-point
forecast attribute, suitable for rendering a full-year bar chart in Lovelace
using the ApexCharts card.

Only registered when ML_ENABLED = True in config options.

State value:
    Today's forecast daily kWh (float) — or None when model not ready.

Attributes:
    forecast (list): 365 dicts ``{"date": "YYYY-MM-DD", "kwh": float}``
        starting from today, using day-of-year averages learned at training time.
    generated_at (str): ISO-8601 UTC timestamp of the last successful training run.
    training_samples (int): number of clean 30-min slots used as training data.
    model_type (str): ``"hist_gbr"`` or ``"ridge"``.
    needs_retrain (bool): True when ``doy_daily_kwh`` is missing (model trained
        before this field was added — re-trigger training to populate it).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .. import const


class AnnualForecastSensor(CoordinatorEntity, SensorEntity):
    """Sensor exposing a 365-day energy consumption forecast from the ML model.

    The forecast is derived from ``TrainedModel.doy_daily_kwh`` — the mean
    total daily consumption (kWh) for each calendar day-of-year, computed
    from the training data.  At render time these are projected forward from
    today's date, cycling through the 366-entry lookup by calendar DOY.

    This gives a practical answer to "how much energy will I use each day
    of the year?" based on historical patterns observed during training.
    """

    _attr_should_poll = False
    _attr_translation_key = "annual_forecast"
    _attr_unique_id = const.ANNUAL_FORECAST_SENSOR
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:chart-bar"

    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        """Initialise the annual forecast sensor."""
        super().__init__(coordinator)
        self.hass = hass
        self._update_attributes()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_forecast(doy_daily_kwh: list[float]) -> list[dict]:
        """Return 365 forecast dicts starting from today.

        Args:
            doy_daily_kwh: 366-entry list where index 0 = day-of-year 1.

        Returns:
            List of ``{"date": "YYYY-MM-DD", "kwh": float}`` sorted ascending.
        """
        today = date.today()
        result: list[dict] = []
        for offset in range(365):
            target = today + timedelta(days=offset)
            doy = target.timetuple().tm_yday  # 1–366
            kwh = doy_daily_kwh[doy - 1]
            result.append({"date": target.isoformat(), "kwh": kwh})
        return result

    def _update_attributes(self) -> None:
        """Pull the latest forecast data from the coordinator's ml_client."""
        ml_client = getattr(self.coordinator, "ml_client", None)

        if not ml_client or not ml_client.is_ready:
            self._attr_native_value = None
            self._attr_extra_state_attributes = {
                "state": "unavailable",
                "reason": "ML model not yet trained",
            }
            return

        status = ml_client.get_status()
        doy_daily_kwh: list[float] | None = status.get("doy_daily_kwh")
        model = status  # use the status dict as a proxy for model fields
        if not doy_daily_kwh:
            # Model was trained before this field was introduced
            self._attr_native_value = None
            self._attr_extra_state_attributes = {
                "state": "unavailable",
                "reason": (
                    "Annual forecast data is missing — use Developer Tools → Services "
                    "→ battery_charge_calculator.trigger_ml_training to retrain."
                ),
                "needs_retrain": True,
            }
            return

        forecast = self._build_forecast(doy_daily_kwh)
        self._attr_native_value = forecast[0]["kwh"] if forecast else None
        self._attr_extra_state_attributes = {
            "forecast": forecast,
            "generated_at": model.get("model_trained_at"),
            "training_samples": model.get("model_n_training_samples"),
            "model_type": model.get("model_type"),
            "needs_retrain": False,
        }

    # ------------------------------------------------------------------
    # CoordinatorEntity callbacks
    # ------------------------------------------------------------------

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator."""
        self._update_attributes()
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Sensor is always available when registered."""
        return True
