"""Monthly Tariff Comparison sensor.

Exposes the Monthly Tariff Comparison as monthly breakdown attributes, suitable
for rendering as a grouped bar chart in Lovelace using the ApexCharts card.

State value:
    Net annual cost (£) of the first (current) tariff in the configured list,
    or None if comparison data is not yet available.

Attributes:
    Full comparison dict matching the schema defined in §7 of
    _docs/tariff-comparison.md.  Key fields:
    - ``generated_at`` — ISO-8601 UTC timestamp of the last calculation.
    - ``data_period`` — {from, to} date range used.
    - ``coverage_warning`` — True when any tariff has < 95 % slot coverage.
    - ``tariffs`` — list of per-tariff monthly/annual breakdowns.
    - ``export_configured`` — whether export consumption data was available.
    - ``export_meter_serial_missing`` — True when export was requested but
      the export meter serial is not configured.

Only registered when ``TARIFF_COMPARISON_ENABLED = True`` in config options.
Pattern follows sensors/annual_forecast.py.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .. import const


class TariffComparisonSensor(CoordinatorEntity, SensorEntity):
    """Sensor exposing Monthly Tariff Comparison as monthly breakdown attributes.

    The state value is the net annual cost (£) for the first (current) tariff.
    All tariff data — monthly breakdowns, annual totals, coverage, and data
    quality notes — is exposed in ``extra_state_attributes``.
    """

    _attr_should_poll = False
    _attr_translation_key = "tariff_comparison"
    _attr_unique_id = const.TARIFF_COMPARISON_SENSOR
    _attr_native_unit_of_measurement = "GBP"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_icon = "mdi:currency-gbp"

    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        """Initialise the tariff comparison sensor."""
        super().__init__(coordinator)
        self.hass = hass
        self._update_attributes()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_attributes(self) -> None:
        """Pull the latest comparison data from the coordinator."""
        data: dict | None = self.coordinator.data

        if not data or not data.get("tariffs"):
            self._attr_native_value = None
            self._attr_extra_state_attributes = {
                "state": "unavailable",
                "reason": "No tariff comparison data yet — check that at least one "
                "tariff is configured and the integration has performed its "
                "first refresh.",
            }
            return

        # State = net cost of the first (current) tariff — sum all monthly rows
        # (window is 1 month, so this equals monthly[0].net_cost_gbp)
        first_tariff = data["tariffs"][0]
        monthly = first_tariff.get("monthly", [])
        if monthly:
            net_cost: float | None = sum(m.get("net_cost_gbp", 0.0) for m in monthly)
        else:
            net_cost = None
        self._attr_native_value = round(net_cost, 2) if net_cost is not None else None

        # Full data dict as attributes
        self._attr_extra_state_attributes = dict(data)

    # ------------------------------------------------------------------
    # CoordinatorEntity callbacks
    # ------------------------------------------------------------------

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the tariff comparison coordinator."""
        self._update_attributes()
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Sensor is available when it has been registered."""
        return True
