"""The Scheduler Integration."""

import json
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

        # Build PowerCalulator from config
        heating_type = entry.options.get(const.HEATING_TYPE, const.DEFAULT_HEATING_TYPE)
        cop = entry.options.get(const.HEATING_COP, const.DEFAULT_HEATING_COP)
        heat_loss_raw = entry.options.get(
            const.HEATING_HEAT_LOSS, const.DEFAULT_HEATING_HEAT_LOSS
        )
        heat_loss = float(heat_loss_raw) if heat_loss_raw else None
        if heat_loss == 0.0:
            heat_loss = None
        indoor_temp = entry.options.get(
            const.HEATING_INDOOR_TEMP, const.DEFAULT_HEATING_INDOOR_TEMP
        )
        known_points_str = entry.options.get(
            const.HEATING_KNOWN_POINTS, const.DEFAULT_HEATING_KNOWN_POINTS
        )
        known_points = json.loads(known_points_str) if known_points_str else None

        base_load_raw = entry.options.get(const.BASE_LOAD_KWH_30MIN)
        base_load = float(base_load_raw) if base_load_raw is not None else None

        flow_temp_raw = entry.options.get(const.HEATING_FLOW_TEMP)
        flow_temp = (
            float(flow_temp_raw)
            if flow_temp_raw is not None
            else const.DEFAULT_HEATING_FLOW_TEMP
        )

        self.power_calculator = power_calculator.PowerCalulator(
            heating_type=heating_type,
            cop=cop,
            heat_loss=heat_loss,
            indoor_temp=indoor_temp,
            heating_flow_temp=flow_temp,
            known_points=known_points,
            base_load_kwh_30min=base_load,
        )
        self.timeslots = []
        self.totalcost = 0
        self.end_of_day_cost = 0
        # Each entry: {"time": ISO string, "temp_c": float, "kwh": float}
        self.daily_power_forecast: list[dict] = []
        self.agile_rates_client = OctopusAgileRatesClient(
            entry.options[const.OCTOPUS_APIKEY],
            entry.options[const.OCTOPUS_ACCOUNT_NUMBER],
        )
        self.givenergy = givenergy.GivEnergyMqttController(self.config_entry)
        self.battery_capacity_kwh = const.DEFAULT_BATTERY_CAPACITY_KWH

        super().__init__(
            hass, _LOGGER, name=const.DOMAIN, update_interval=timedelta(minutes=1)
        )

        self._timer_unsub = None
        self._ml_retrain_unsub = None

        # ML Power Estimator — only created when ML_ENABLED = True (D-16)
        self.ml_estimator = None
        if entry.options.get(const.ML_ENABLED, False):
            from .ml.ml_power_estimator import MLPowerEstimator

            self.ml_estimator = MLPowerEstimator(hass, entry)
            # Inject the power calculator so estimator can build physics series
            self.ml_estimator.set_physics_calculator(self.power_calculator)

    async def _async_setup(self) -> None:
        """Run once during async_config_entry_first_refresh to start planning."""
        # Start ML estimator (loads or trains model in background — D-5/D-16)
        if self.ml_estimator is not None:
            await self.ml_estimator.async_start()
        await self.octopus_state_change_listener(None)
        self._timer_unsub = async_track_time_interval(
            self.hass,
            self._handle_planning_timer,
            timedelta(hours=1),
        )
        self._ml_retrain_unsub = async_track_time_interval(
            self.hass,
            lambda now: self.hass.async_create_task(self._async_maybe_retrain_ml()),
            timedelta(days=30),
        )

    @callback
    def _handle_planning_timer(self, now: datetime) -> None:
        """Trigger a conditional re-planning check every hour."""
        self.hass.async_create_task(self._conditional_replan())

    async def _conditional_replan(self) -> None:
        """Re-plan only when the current plan is no longer trustworthy.

        Re-planning is skipped unless at least one of the following is true:
        - No plan currently exists.
        - The actual battery level deviates from the projected level by more
          than 10 % of maximum battery capacity.
        - Fewer than 2 hours remain on the current plan.
        """
        if await self._should_replan():
            await self.octopus_state_change_listener(None)

    async def _should_replan(self) -> bool:
        """Return True when conditions warrant replacing the current plan."""
        if not self.timeslots:
            _LOGGER.debug("No existing plan — re-planning required")
            return True

        # Trigger re-plan when the plan is nearly exhausted.
        last_slot = self.timeslots[-1]
        plan_end = last_slot.start_datetime + timedelta(minutes=30)
        now = datetime.now(tz=self.tz)
        time_remaining = plan_end - now
        if time_remaining <= timedelta(hours=2):
            _LOGGER.info(
                "Fewer than 2 hours remain on the current plan (%s) — re-planning",
                time_remaining,
            )
            return True

        # Trigger re-plan when the battery level has drifted too far from the
        # projection embedded in the plan.
        actual_battery_kw = await self.givenergy.get_inverter_soc_kwh(self.hass)
        if actual_battery_kw is None:
            _LOGGER.warning("Battery SOC unavailable — skipping re-plan check")
            return False

        active_slot = self.current_active_slot()
        if active_slot is None:
            _LOGGER.debug("No active timeslot found — re-planning required")
            return True

        projected_battery_kw = active_slot.initial_power
        deviation = (
            abs(actual_battery_kw - projected_battery_kw) / self.battery_capacity_kwh
        )
        if deviation > 0.10:
            _LOGGER.info(
                "Battery deviation %.1f %% (actual %.2f kWh vs projected %.2f kWh) — re-planning",
                deviation * 100,
                actual_battery_kw,
                projected_battery_kw,
            )
            return True

        _LOGGER.debug(
            "Plan is still valid (battery deviation %.1f %%, %.1fh remaining) — skipping re-plan",
            deviation * 100,
            time_remaining.total_seconds() / 3600,
        )
        return False

    async def async_shutdown(self) -> None:
        """Cancel the hourly planning timer on shutdown."""
        if self._timer_unsub is not None:
            self._timer_unsub()
            self._timer_unsub = None
        if self._ml_retrain_unsub is not None:
            self._ml_retrain_unsub()
            self._ml_retrain_unsub = None
        if self.ml_estimator is not None:
            await self.ml_estimator.async_shutdown()
        await super().async_shutdown()

    async def _async_maybe_retrain_ml(self) -> None:
        """Trigger ML retraining when the monthly schedule fires (D-9)."""
        if self.ml_estimator is not None:
            await self.ml_estimator.async_trigger_retrain()

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

            battery_kw = await self.givenergy.get_inverter_soc_kwh(self.hass)

            if battery_kw is None:
                _LOGGER.warning(
                    "SOC not yet available from MQTT — skipping planning cycle"
                )
                return

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

            self.battery_capacity_kwh = self.config_entry.options.get(
                const.BATTERY_CAPACITY_KWH, const.DEFAULT_BATTERY_CAPACITY_KWH
            )
            evaluator = genetic_evaluator.GeneticEvaluator(
                battery_kw,
                octopus_import_standing_charge_rate,
                inverter_size_kw=self.config_entry.options.get(
                    const.INVERTER_SIZE_KW, const.DEFAULT_INVERTER_SIZE_KW
                ),
                inverter_efficiency=self.config_entry.options.get(
                    const.INVERTER_EFFICIENCY, const.DEFAULT_INVERTER_EFFICIENCY
                ),
                battery_capacity_kwh=self.battery_capacity_kwh,
            )

            daily_forecast: list[dict] = []

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
                # ML correction (D-1): blend physics estimate with ML residual correction
                if self.ml_estimator and self.ml_estimator.is_ready:
                    required_power = self.ml_estimator.predict(
                        current_time, tempdata, required_power
                    )

                daily_forecast.append(
                    {
                        "time": current_time.isoformat(),
                        "temp_c": round(tempdata, 1) if tempdata is not None else None,
                        "kwh": round(required_power, 4),
                    }
                )

                evaluator.add_data(
                    current_time, ratedata, export_ratedata, required_power, solardata
                )

            self.timeslots, self.totalcost = evaluator.evaluate()
            self.daily_power_forecast = daily_forecast

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
        _LOGGER.info("update data in entity (forcing plan refresh)")

        # Always refresh the plan before returning timeslots
        await self.octopus_state_change_listener(None)

        simulate = self.config_entry.options.get(const.SIMULATE_ONLY)
        active_slot = self.current_active_slot()
        if active_slot is not None:
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
