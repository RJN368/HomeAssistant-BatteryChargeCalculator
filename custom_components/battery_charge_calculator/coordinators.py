"""The Scheduler Integration."""

import logging
from datetime import datetime, timedelta

from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import const, givenergy, power_calculator, genetic_evaluator
from .octopus_agile import OctopusAgileRatesClient

_LOGGER = logging.getLogger(__name__)


class BatteryChargeCoordinator(DataUpdateCoordinator):
    """Initialize."""

    def __init__(self, hass, entry):
        """Initialize."""
        self.config_entry = entry
        self.id = entry.entry_id
        self.hass = hass
        self.tz = dt_util.get_time_zone(self.hass.config.time_zone)
        self.power_calculator = power_calculator.PowerCalulator()
        self.timeslots = []
        self.totalcost = 0
        self.end_of_day_cost = 0
        self.agile_rates_client = OctopusAgileRatesClient(
            entry.options[const.OCTOPUS_APIKEY],
            entry.options[const.OCTOPUS_ACCOUNT_NUMBER],
        )
        self.givenergy = givenergy.GivEnergyMqttController(self.config_entry)

        super().__init__(
            hass, _LOGGER, name=const.DOMAIN, update_interval=timedelta(minutes=1)
        )

        self._timer_unsub = None

    async def _async_setup(self) -> None:
        """Run once during async_config_entry_first_refresh to start planning."""
        await self.octopus_state_change_listener(None)
        self._timer_unsub = async_track_time_interval(
            self.hass,
            self._handle_planning_timer,
            timedelta(hours=1),
        )

    @callback
    def _handle_planning_timer(self, now: datetime) -> None:
        """Trigger a new planning cycle every hour."""
        self.hass.async_create_task(self.octopus_state_change_listener(None))

    async def async_shutdown(self) -> None:
        """Cancel the hourly planning timer on shutdown."""
        if self._timer_unsub is not None:
            self._timer_unsub()
            self._timer_unsub = None
        await super().async_shutdown()

    async def octopus_state_change_listener(self, event):
        _LOGGER.debug("octopus_state_change_listener")

        try:
            time_now = self.ceil_dt(datetime.now(), timedelta(minutes=30)).astimezone(
                self.tz
            )

            session = async_get_clientsession(self.hass)
            octopus_import_standing_charge_rate: float = (
                await self.agile_rates_client.fetch_standing_charge(session)
            )

            all_octopus_rates = await self.agile_rates_client.fetch_rates(
                session, export=False
            )

            all_octopus_export_rates = await self.agile_rates_client.fetch_rates(
                session, export=True
            )

            weather_state = self.hass.states.get("weather.forecast_home")
            if weather_state is None:
                _LOGGER.error("Weather entity weather.forecast_home not found")
                return
            current_temp = weather_state.attributes.get("temperature")

            forecast_response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": "weather.forecast_home", "type": "hourly"},
                return_response=True,
                blocking=True,
            )
            hourly_forecast = forecast_response.get("weather.forecast_home", {}).get(
                "forecast", []
            )

            battery_kw: float = await self.givenergy.get_inverter_soc_kwh(self.hass)

            time_end = all_octopus_rates[-1]["end"]

            solarcast = await self.hass.services.async_call(
                "solcast_solar",
                "query_forecast_data",
                {
                    "start_date_time": time_now,
                    "end_date_time": time_end,
                },
                return_response=True,
                blocking=True,
            )

            # timeslots = []
            ratedata = None
            export_ratedata = None
            tempdata = current_temp
            solardata = 0
            # current_power = battery_kw
            # prev_timeslot = None

            max_range = (time_end - time_now).total_seconds() / 60

            evaluator = genetic_evaluator.GeneticEvaluator(
                battery_kw, octopus_import_standing_charge_rate
            )

            for index, value in enumerate(range(0, int(max_range), 30)):
                current_time = time_now + timedelta(minutes=value)

                tempdata = self.find_in_dataset(
                    hourly_forecast,
                    tempdata,
                    "temperature",
                    lambda f: datetime.strptime(
                        f["datetime"], "%Y-%m-%dT%H:%M:%S%z"
                    ).strftime("%d:%H")
                    == current_time.strftime("%d:%H"),
                )

                export_ratedata = self.find_in_dataset(
                    all_octopus_export_rates,
                    export_ratedata,
                    "value_inc_vat",
                    lambda f: f["start"].strftime("%d:%H:%M")
                    == current_time.strftime("%d:%H:%M"),
                )

                ratedata = self.find_in_dataset(
                    all_octopus_rates,
                    ratedata,
                    "value_inc_vat",
                    lambda f: f["start"].strftime("%d:%H:%M")
                    == current_time.strftime("%d:%H:%M"),
                )

                solardata = self.find_in_dataset(
                    solarcast["data"],
                    solardata,
                    "pv_estimate10",
                    lambda entry: entry["period_start"].strftime("%d:%H")
                    == current_time.strftime("%d:%H"),
                )

                required_power = self.power_calculator.from_temp_and_time(
                    current_time, tempdata
                )

                evaluator.add_data(
                    current_time, ratedata, export_ratedata, required_power, solardata
                )

            self.timeslots, self.totalcost = evaluator.evaluate()
            try:
                await self.async_refresh()
            except RuntimeError as err:
                if "Debouncer lock is not re-entrant" in str(err):
                    _LOGGER.warning("Debouncer lock is not re-entrant: %s", err)
                else:
                    raise
        except Exception as exc:
            _LOGGER.error(
                "Exception in octopus_state_change_listener: %s", exc, exc_info=True
            )

    def find_in_dataset(self, sourcedata, lastvalue, value_field, comparitor):
        result_list = list(filter(comparitor, sourcedata))

        if len(result_list) > 0:
            return result_list[0][value_field]

        return lastvalue

    def callback(self):
        _LOGGER.debug("hello")

    def ceil_dt(self, dt, delta):
        return dt + (datetime.min - dt) % delta

    def current_active_slot(self):
        if not self.timeslots or not isinstance(self.timeslots, list):
            return None

        slot = list(filter(self.date_comapre, self.timeslots))

        if slot:
            return slot[0]

        return None

    """Update the data"""

    async def _async_update_data(self):
        _LOGGER.info("udpate data in entity")

        simulate = self.config_entry.options.get(const.SIMULATE_ONLY)

        active_slot = self.current_active_slot()

        if active_slot != None:
            _LOGGER.info(active_slot.charge_option)

            if not simulate:
                if active_slot.charge_option == "charge":
                    await self.givenergy.enableCharge(self.hass)
                elif active_slot.charge_option == "export":
                    await self.givenergy.enableExport(self.hass)
                else:
                    await self.givenergy.disableCharge(self.hass)
                    await self.givenergy.disableExport(self.hass)

        return self.timeslots

    def date_comapre(self, ts):
        now = datetime.now(tz=self.tz)
        return (
            ts.start_datetime <= now
            and (ts.start_datetime + timedelta(minutes=30)) >= now
        )
