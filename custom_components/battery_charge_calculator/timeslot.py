"""Timeslot model used by battery scheduling evaluation."""

from __future__ import annotations

import logging
from datetime import timezone
from zoneinfo import ZoneInfo


class Timeslot:
    """A single timeslot for battery scheduling, holding all relevant data and results."""

    def __init__(
        self, start_datetime, import_price, export_price, demand_in, solar_in
    ) -> None:
        """Initialize a timeslot with all required parameters."""
        # Parse string datetimes
        if isinstance(start_datetime, str):
            from datetime import datetime as _dt

            start_datetime = _dt.fromisoformat(start_datetime)
        # Ensure start_datetime is aware and in UTC
        if start_datetime.tzinfo is None:
            logging.warning("Naive datetime detected in Timeslot; assuming UTC.")
            start_datetime = start_datetime.replace(tzinfo=timezone.utc)
        self._start_datetime = start_datetime.astimezone(timezone.utc)
        self._import_price = float(import_price) if import_price is not None else 0.0
        self._export_price = float(export_price) if export_price is not None else 0.0
        self._demand = float(demand_in) if demand_in is not None else 0.0
        self._solar = float(solar_in) if solar_in is not None else 0.0
        self._cost = 0
        self._charge_option = ""
        self._initial_power = 0

    def start_datetime_london(self):
        """Return start_datetime converted to Europe/London timezone."""
        return self._start_datetime.astimezone(ZoneInfo("Europe/London"))

    @property
    def start_datetime(self):
        """Get the start datetime for this timeslot."""
        return self._start_datetime

    @property
    def import_price(self):
        """Get the import price for this timeslot."""
        return self._import_price

    @property
    def export_price(self):
        """Get the export price for this timeslot."""
        return self._export_price

    @property
    def demand(self):
        """Get the demand value for this timeslot."""
        return self._demand

    @property
    def solar(self):
        """Get the solar value for this timeslot."""
        return self._solar

    @property
    def cost(self):
        """Get the cost for this timeslot."""
        return self._cost

    @cost.setter
    def cost(self, new_value):
        self._cost = new_value

    @property
    def charge_option(self):
        """Get the charge option for this timeslot."""
        return self._charge_option

    @charge_option.setter
    def charge_option(self, new_value):
        self._charge_option = new_value

    @property
    def initial_power(self):
        """Get the initial battery power for this timeslot."""
        return self._initial_power

    @initial_power.setter
    def initial_power(self, new_value):
        self._initial_power = new_value
