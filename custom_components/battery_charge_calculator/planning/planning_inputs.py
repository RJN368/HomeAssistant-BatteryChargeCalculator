"""Collected provider input payload used by planning flow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(slots=True)
class PlanningInputs:
    """Collected provider input payload used by planning flow."""

    standing_charge_rate: float
    import_rates: list[dict]
    export_rates: list[dict]
    today_consumption: list[dict]
    battery_kwh: float | None
    time_end: datetime
    current_temperature: float | None
    hourly_temperature_forecast: list[dict]
    solar_forecast: dict

    @staticmethod
    def _as_aware_utc(dt_value: datetime) -> datetime:
        if dt_value.tzinfo is None:
            dt_value = dt_value.replace(tzinfo=timezone.utc)
        return dt_value.astimezone(timezone.utc)

    @classmethod
    def _time_in_slot(
        cls,
        slot_start: datetime,
        slot_end: datetime,
        current_time: datetime,
    ) -> bool:
        current_utc = cls._as_aware_utc(current_time)
        slot_start_utc = cls._as_aware_utc(slot_start)
        slot_end_utc = cls._as_aware_utc(slot_end)
        return slot_start_utc <= current_utc < slot_end_utc

    @staticmethod
    def _first_matching_value(
        source_data: list[dict],
        *,
        last_value,
        value_field: str,
        predicate,
    ):
        matches = list(filter(predicate, source_data))
        if matches:
            return matches[0][value_field]
        return last_value

    def resolve_temperature(
        self,
        *,
        current_time: datetime,
        last_temperature,
    ):
        return self._first_matching_value(
            self.hourly_temperature_forecast,
            last_value=last_temperature,
            value_field="temperature",
            predicate=lambda entry: self._time_in_slot(
                datetime.fromisoformat(entry["datetime"]),
                datetime.fromisoformat(entry["datetime"]) + timedelta(hours=1),
                current_time,
            ),
        )

    def resolve_import_rate(
        self,
        *,
        current_time: datetime,
        last_rate,
    ):
        return self._first_matching_value(
            self.import_rates,
            last_value=last_rate,
            value_field="value_inc_vat",
            predicate=lambda entry: self._time_in_slot(
                entry["start"],
                entry["end"],
                current_time,
            ),
        )

    def resolve_export_rate(
        self,
        *,
        current_time: datetime,
        last_rate,
    ):
        return self._first_matching_value(
            self.export_rates,
            last_value=last_rate,
            value_field="value_inc_vat",
            predicate=lambda entry: self._time_in_slot(
                entry["start"],
                entry["end"],
                current_time,
            ),
        )

    def resolve_solar_estimate(
        self,
        *,
        current_time: datetime,
        last_solar,
    ):
        return self._first_matching_value(
            self.solar_forecast.get("data", []),
            last_value=last_solar,
            value_field="pv_estimate10",
            predicate=lambda entry: self._time_in_slot(
                entry["period_start"],
                entry["period_start"] + timedelta(minutes=30),
                current_time,
            ),
        )
