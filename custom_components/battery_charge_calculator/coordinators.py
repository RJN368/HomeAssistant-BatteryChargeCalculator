"""The Scheduler Integration."""

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import const, givenergy, power_calculator, genetic_evaluator
from .planning.providers.axle.axle_state import AxleStateManager
from .planning.providers.tariff.octopus_agile import OctopusAgileRatesClient
from .planning.engine import PlanningEngine
from .planning.factory import build_default_planning_strategy

_LOGGER = logging.getLogger(__name__)


class BatteryChargeCoordinator(DataUpdateCoordinator):
    """Initialize."""

    def __init__(self, hass, entry):
        """Initialize."""
        self.config_entry = entry
        self.id = entry.entry_id
        self.hass = hass
        self.tz = dt_util.get_time_zone(self.hass.config.time_zone)
        if self.tz is None:
            self.tz = ZoneInfo("Europe/London")

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
        self.recalculation_time: datetime | None = None
        self.recalculation_reason: str | None = None
        # Each entry: {"time": ISO string, "temp_c": float, "kwh": float}
        self.daily_power_forecast: list[dict] = []
        self.agile_rates_client = OctopusAgileRatesClient(
            entry.options[const.OCTOPUS_APIKEY],
            entry.options[const.OCTOPUS_ACCOUNT_NUMBER],
        )
        self.givenergy = givenergy.GivEnergyMqttController(self.config_entry)
        self.planning_strategy = build_default_planning_strategy(self)
        self.planning_engine = PlanningEngine(
            planning_strategy=self.planning_strategy,
            power_calculator=self.power_calculator,
            config_entry=self.config_entry,
            evaluator_factory=lambda *args, **kwargs: genetic_evaluator.GeneticEvaluator(
                *args,
                **kwargs,
            ),
        )
        self.battery_capacity_kwh = const.DEFAULT_BATTERY_CAPACITY_KWH

        super().__init__(
            hass,
            _LOGGER,
            name=const.DOMAIN,
            update_interval=timedelta(minutes=1),
            config_entry=entry,
        )

        self._timer_unsub = None
        self._ml_retrain_unsub = None

        # ML Service Client — thin HTTPS client to the external BCC ML Service (D-16)
        self.ml_client = None
        if entry.options.get(const.ML_ENABLED, False):
            service_url = entry.options.get(const.ML_SERVICE_URL, "")
            api_key = entry.options.get(const.ML_SERVICE_API_KEY, "")
            if service_url and api_key:
                from .ml.ml_service_client import MLServiceClient

                self.ml_client = MLServiceClient(
                    base_url=service_url,
                    api_key=api_key,
                    tls_fingerprint=entry.options.get(
                        const.ML_SERVICE_TLS_FINGERPRINT, ""
                    ),
                    config=self._build_ml_service_config(entry, hass),
                )
            else:
                _LOGGER.warning(
                    "ML enabled but ML_SERVICE_URL or ML_SERVICE_API_KEY is not set — "
                    "ML power estimation disabled until the service is configured."
                )

        # Tariff Comparison Coordinator (lazy — created in async_setup_entry if enabled)
        self.tariff_coordinator = None

        self.axle_state = AxleStateManager(config_entry=entry, hass=hass)

    def _build_ml_service_config(self, entry, hass=None) -> dict:
        """Build the config dict sent to POST /configure on the ML service."""
        opts = entry.options
        return {
            "givenergy_api_key": opts.get(const.GIVENERGY_API_TOKEN, ""),
            "givenergy_inverter_serial": opts.get(const.GIVENERGY_SERIAL_NUMBER, ""),
            "octopus_api_key": opts.get(const.OCTOPUS_APIKEY, ""),
            "octopus_account_id": opts.get(const.OCTOPUS_ACCOUNT_NUMBER, ""),
            "octopus_mpan": opts.get(const.OCTOPUS_MPN, ""),
            "octopus_meter_serial": opts.get(const.OCTOPUS_METER_SERIAL, ""),
            "consumption_source": opts.get(
                const.ML_CONSUMPTION_SOURCE, const.DEFAULT_ML_CONSUMPTION_SOURCE
            ),
            "training_lookback_days": int(
                opts.get(
                    const.ML_TRAINING_LOOKBACK_DAYS,
                    const.DEFAULT_ML_TRAINING_LOOKBACK_DAYS,
                )
            ),
            "heating_type": opts.get(const.HEATING_TYPE, const.DEFAULT_HEATING_TYPE),
            "cop": float(opts.get(const.HEATING_COP, const.DEFAULT_HEATING_COP)),
            "heat_loss_w_per_k": float(
                opts.get(const.HEATING_HEAT_LOSS, const.DEFAULT_HEATING_HEAT_LOSS)
                or 0.0
            ),
            "indoor_temp_c": float(
                opts.get(const.HEATING_INDOOR_TEMP, const.DEFAULT_HEATING_INDOOR_TEMP)
            ),
            "heating_flow_temp_c": float(
                opts.get(const.HEATING_FLOW_TEMP, const.DEFAULT_HEATING_FLOW_TEMP)
            ),
            "latitude": (hass or self.hass).config.latitude,
            "longitude": (hass or self.hass).config.longitude,
        }

    async def _async_setup(self) -> None:
        """Run once during async_config_entry_first_refresh to start planning."""
        # Connect to ML service (configure + load model status — D-16)
        if self.ml_client is not None:
            try:
                await self.ml_client.async_start()
            except Exception as exc:
                _LOGGER.warning("ML service unreachable at startup: %s", exc)

        if self.axle_state.is_enabled():
            await self.axle_state.async_refresh_source_state(
                now_utc=datetime.now(timezone.utc)
            )

        await self.octopus_state_change_listener(
            None, reason=const.REPLAN_REASON_INITIAL_SETUP
        )
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
        # Ensure now is aware and in local timezone
        if now.tzinfo is None:
            logging.warning("Naive datetime in _handle_planning_timer; assuming UTC.")
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(self.tz)
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
        should, reason = await self._should_replan()
        if should:
            await self.octopus_state_change_listener(None, reason=reason)

    async def _should_replan(self) -> tuple[bool, str]:
        """Return (True, reason) when conditions warrant replacing the current plan."""
        if not self.timeslots:
            _LOGGER.debug("No existing plan — re-planning required")
            return True, const.REPLAN_REASON_NO_PLAN

        # Trigger re-plan when the plan is nearly exhausted.
        last_slot = self.timeslots[-1]
        plan_end = last_slot.start_datetime
        if plan_end.tzinfo is None:
            logging.warning("Naive datetime in plan_end; assuming UTC.")
            plan_end = plan_end.replace(tzinfo=timezone.utc)
        plan_end = plan_end.astimezone(self.tz) + timedelta(minutes=30)
        now = datetime.now(tz=self.tz)
        time_remaining = plan_end - now
        if time_remaining <= timedelta(hours=2):
            _LOGGER.info(
                "Fewer than 2 hours remain on the current plan (%s) — re-planning",
                time_remaining,
            )
            return True, const.REPLAN_REASON_PLAN_EXPIRING

        # Trigger re-plan when the battery level has drifted too far from the
        # projection embedded in the plan.
        actual_battery_kw = await self.givenergy.get_inverter_soc_kwh(self.hass)
        if actual_battery_kw is None:
            _LOGGER.warning("Battery SOC unavailable — skipping re-plan check")
            return False, ""

        active_slot = self.current_active_slot()
        if active_slot is None:
            _LOGGER.debug("No active timeslot found — re-planning required")
            return True, const.REPLAN_REASON_NO_ACTIVE_SLOT

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
            return True, const.REPLAN_REASON_BATTERY_DEVIATION

        _LOGGER.debug(
            "Plan is still valid (battery deviation %.1f %%, %.1fh remaining) — skipping re-plan",
            deviation * 100,
            time_remaining.total_seconds() / 3600,
        )
        return False, ""

    async def async_shutdown(self) -> None:
        """Cancel the hourly planning timer on shutdown."""
        if self._timer_unsub is not None:
            self._timer_unsub()
            self._timer_unsub = None
        if self._ml_retrain_unsub is not None:
            self._ml_retrain_unsub()
            self._ml_retrain_unsub = None
        await super().async_shutdown()

    async def _async_maybe_retrain_ml(self) -> None:
        """Trigger ML retraining when the monthly schedule fires (D-9)."""
        if self.ml_client is not None:
            await self.ml_client.async_trigger_retrain()

    async def octopus_state_change_listener(
        self, event, *, reason: str = const.REPLAN_REASON_MANUAL
    ):
        """Backward-compatible trigger wrapper for plan recalculation."""
        await self._async_recalculate_plan(reason=reason)

    async def _async_recalculate_plan(self, *, reason: str) -> None:
        """Recalculate dispatch plan using provider-agnostic planning strategy."""
        _LOGGER.debug("plan recalculation requested — reason: %s", reason)
        self.recalculation_time = datetime.now(timezone.utc)
        self.recalculation_reason = reason

        try:
            now = datetime.now(timezone.utc)
            time_now = self.ceil_dt(now, timedelta(minutes=30)).astimezone(self.tz)

            session = async_get_clientsession(self.hass)
            self.planning_engine.set_planning_strategy(self.planning_strategy)
            self.battery_capacity_kwh = self.config_entry.options.get(
                const.BATTERY_CAPACITY_KWH, const.DEFAULT_BATTERY_CAPACITY_KWH
            )
            plan_result = await self.planning_engine.async_compute_plan(
                hass=self.hass,
                session=session,
                time_now=time_now,
                ml_client=self.ml_client,
                battery_capacity_kwh=self.battery_capacity_kwh,
                inverter_size_kw_default=const.DEFAULT_INVERTER_SIZE_KW,
                inverter_efficiency_default=const.DEFAULT_INVERTER_EFFICIENCY,
            )

            if plan_result is None:
                _LOGGER.warning(
                    "SOC not yet available from MQTT — skipping planning cycle"
                )
                return
            self.timeslots = plan_result.timeslots
            self.totalcost = plan_result.total_cost
            self.daily_power_forecast = plan_result.daily_forecast
            self.axle_state.set_planning_adjustments(
                plan_result.slot_adjustment_kwh_total
            )
            self.end_of_day_cost = self._calculate_end_of_day_cost(
                self.timeslots,
                plan_result.import_rates,
                plan_result.today_consumption,
                now,
            )

            # Refresh ML service status for diagnostic sensors
            if self.ml_client is not None:
                await self.ml_client.async_refresh_status()

            self.async_set_updated_data(self.timeslots)
        except Exception as exc:
            _LOGGER.error(
                "Exception in octopus_state_change_listener: %s", exc, exc_info=True
            )

    def _calculate_end_of_day_cost(
        self,
        timeslots: list,
        all_octopus_rates: list[dict],
        consumption_data: list[dict],
        now: datetime,
    ) -> float:
        """Return the predicted end-of-day energy cost for today.

        Blends two sources:
        - **Actual spend** — real half-hourly consumption (from Octopus API) ×
          the tariff rate for each slot, for intervals that have already passed.
        - **Predicted spend** — ``timeslot.cost`` from the genetic evaluator for
          all remaining slots from now until midnight.

        If consumption data is unavailable for a past interval the evaluator's
        predicted cost for that slot is used as a fallback.
        """
        london = ZoneInfo("Europe/London")
        today_day = now.astimezone(london).day
        total = 0.0

        # --- Actual spend: past half-hours with real meter readings ---
        for entry in consumption_data:
            interval_start = entry["interval_start"]
            if interval_start.astimezone(london).day != today_day:
                continue
            rate = next(
                (
                    r["value_inc_vat"]
                    for r in all_octopus_rates
                    if r["start"] <= interval_start < r["end"]
                ),
                None,
            )
            if rate is not None:
                total += entry["consumption_kwh"] * rate
            else:
                _LOGGER.debug(
                    "No import rate found for consumption interval %s — skipping",
                    interval_start,
                )

        # --- Predicted spend: today's timeslots from now onwards ---
        for slot in timeslots:
            slot_dt = slot.start_datetime
            if slot_dt.tzinfo is None:
                slot_dt = slot_dt.replace(tzinfo=timezone.utc)
            if slot_dt.astimezone(london).day == today_day:
                total += getattr(slot, "cost", 0)

        return total

    @staticmethod
    def _coerce_aware_utc(dt_value: datetime, *, context: str) -> datetime:
        """Normalize datetimes to aware UTC values for safe instant comparison."""
        if dt_value.tzinfo is None:
            _LOGGER.warning("Naive datetime in %s; assuming UTC.", context)
            dt_value = dt_value.replace(tzinfo=timezone.utc)
        return dt_value.astimezone(timezone.utc)

    def _parse_iso_datetime(self, value: str, *, context: str) -> datetime:
        """Parse an ISO timestamp and normalize to aware UTC."""
        parsed = datetime.fromisoformat(value)
        return self._coerce_aware_utc(parsed, context=context)

    def _time_in_slot(
        self,
        slot_start: datetime,
        slot_end: datetime,
        current_time: datetime,
        *,
        context: str,
    ) -> bool:
        """Return True when current_time is within [slot_start, slot_end)."""
        slot_start_utc = self._coerce_aware_utc(slot_start, context=f"{context}.start")
        slot_end_utc = self._coerce_aware_utc(slot_end, context=f"{context}.end")
        current_utc = self._coerce_aware_utc(current_time, context=f"{context}.current")
        return slot_start_utc <= current_utc < slot_end_utc

    def find_in_dataset(self, data, lastvalue, key, predicate):
        """Compatibility helper used by existing tests and legacy callsites."""
        matches = list(filter(predicate, data))
        if matches:
            return matches[0][key]
        return lastvalue

    @property
    def _axle_cache(self) -> dict:
        """Compatibility view for legacy code that reads coordinator cache directly."""
        return self.axle_state.cache

    def _axle_cache_age_seconds(self, now_utc: datetime | None = None) -> float | None:
        """Compatibility wrapper around AxleStateManager cache age helper."""
        return self.axle_state.cache_age_seconds(now_utc=now_utc)

    def _axle_evaluate_source_status(self, now_utc: datetime | None = None) -> str:
        """Compatibility wrapper around AxleStateManager source status helper."""
        return self.axle_state.evaluate_source_status(now_utc=now_utc)

    def _axle_overlapping_window(self, now_utc: datetime | None = None):
        """Compatibility wrapper for overlapping Axle window lookup."""
        effective_now = now_utc or datetime.now(timezone.utc)
        return self.axle_state.overlapping_window(effective_now)

    def ceil_dt(self, dt, delta):
        tz = dt.tzinfo
        naive = dt.replace(tzinfo=None)
        rounded = naive + (datetime.min - naive) % delta
        return rounded.replace(tzinfo=tz)

    def current_active_slot(self):
        if not self.timeslots or not isinstance(self.timeslots, list):
            return None

        slot = list(filter(self.date_comapre, self.timeslots))

        if slot:
            return slot[0]

        return None

    def axle_slot_export_adjustment_kwh(
        self,
        *,
        slot_start: datetime,
        slot_end: datetime,
        inverter_size_kw: float,
    ) -> float:
        """Public wrapper for slot export adjustment calculation."""
        return self.axle_state.slot_export_adjustment_kwh(
            slot_start=slot_start,
            slot_end=slot_end,
            inverter_size_kw=inverter_size_kw,
        )

    def axle_slot_forced_action(
        self,
        *,
        slot_start: datetime,
        slot_end: datetime,
    ) -> str | None:
        """Public wrapper for Axle forced action lookup."""
        return self.axle_state.slot_forced_action(
            slot_start=slot_start,
            slot_end=slot_end,
        )

    """Update the data"""

    async def _async_update_data(self):
        simulate = self.config_entry.options.get(const.SIMULATE_ONLY)
        now_utc = datetime.now(timezone.utc)
        _LOGGER.debug(
            "Coordinator update cycle start: simulate_only=%s axle_enabled=%s",
            simulate,
            self.axle_state.is_enabled(),
        )

        if self.axle_state.is_enabled():
            await self.axle_state.async_refresh_source_state(now_utc=now_utc)
            refresh_snapshot = self.axle_state.snapshot(now_utc=now_utc)
            if refresh_snapshot.last_error:
                _LOGGER.warning(
                    "Axle source refresh failed; source_status=%s, cache_age_seconds=%s, error=%s",
                    refresh_snapshot.source_status,
                    refresh_snapshot.cache_age_seconds,
                    refresh_snapshot.last_error,
                )
            if self.axle_state.windows_changed():
                self.axle_state.clear_windows_changed()
                await self.octopus_state_change_listener(
                    None,
                    reason=const.REPLAN_REASON_AXLE_WINDOWS_CHANGED,
                )

        axle_snapshot = self.axle_state.sync_runtime_state(now_utc=now_utc)
        source_status = axle_snapshot.source_status
        active_window = self.axle_state.overlapping_window(now_utc)

        if source_status == const.AXLE_SOURCE_STATUS_STALE and active_window is not None:
            _LOGGER.info(
                "Axle stale cache overlaps active window; window_start=%s window_end=%s",
                active_window.start.isoformat(),
                active_window.end.isoformat(),
            )
        elif (
            source_status == const.AXLE_SOURCE_STATUS_UNAVAILABLE
            and self.config_entry.options.get(
                const.AXLE_FAIL_SAFE_MODE,
                const.DEFAULT_AXLE_FAIL_SAFE_MODE,
            )
            == const.AXLE_FAIL_SAFE_MODE_CLOSED
        ):
            _LOGGER.warning(
                "Axle source unavailable with fail-safe closed; diagnostics only in planning-adjustment mode."
            )
        elif source_status == const.AXLE_SOURCE_STATUS_UNAVAILABLE:
            _LOGGER.info(
                "Axle source unavailable with fail-safe open; allowing local dispatch."
            )

        active_slot = self.current_active_slot()
        if active_slot is not None:
            slot_local = active_slot.start_datetime.astimezone(self.tz)
            _LOGGER.info(
                "Active slot: %s → %s (local %s)",
                active_slot.charge_option,
                active_slot.start_datetime.isoformat(),
                slot_local.strftime("%d/%m %H:%M %Z"),
            )
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
        slot_start = ts.start_datetime
        if slot_start.tzinfo is None:
            logging.warning("Naive datetime in timeslot; assuming UTC.")
            slot_start = slot_start.replace(tzinfo=timezone.utc)
        slot_start = slot_start.astimezone(self.tz)
        return slot_start <= now and (slot_start + timedelta(minutes=30)) >= now


async def async_setup_tariff_coordinator(hass, entry) -> None:
    """Create and register TariffComparisonCoordinator if tariff comparison is enabled.

    Called from ``__init__.async_setup_entry`` after the main coordinator is ready.
    Stores the coordinator under ``hass.data[DOMAIN][entry_id + "_tariff"]`` so that
    ``sensor.py:async_setup_entry`` can retrieve it when registering the sensor.
    """
    from . import const  # avoid circular at module level

    if not entry.options.get(const.TARIFF_COMPARISON_ENABLED, False):
        return

    from .tariff_comparison import TariffComparisonCoordinator

    coordinator = TariffComparisonCoordinator(hass, entry)
    hass.data[const.DOMAIN][entry.entry_id + "_tariff"] = coordinator
    await coordinator.async_config_entry_first_refresh()
