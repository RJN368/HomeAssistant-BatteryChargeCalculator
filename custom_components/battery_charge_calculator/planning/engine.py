"""Planning engine for provider-agnostic plan computation."""

from __future__ import annotations

from datetime import datetime, timedelta

from .. import const
from .engine_models import PlanComputationResult, SlotStageData
from .models import PlanningProviderContext


class PlanningEngine:
    """Orchestrates planning strategy data collection and slot computation."""

    def __init__(
        self,
        *,
        planning_strategy,
        power_calculator,
        config_entry,
        evaluator_factory,
    ) -> None:
        self._planning_strategy = planning_strategy
        self._power_calculator = power_calculator
        self._config_entry = config_entry
        self._evaluator_factory = evaluator_factory

    def set_planning_strategy(self, planning_strategy) -> None:
        """Update planning strategy reference used for subsequent computations."""
        self._planning_strategy = planning_strategy

    async def _collect_inputs(self, *, hass, session, time_now: datetime):
        planning_context = PlanningProviderContext(
            hass=hass,
            config_entry=self._config_entry,
            session=session,
            time_now=time_now,
        )
        return await self._planning_strategy.collect_inputs(context=planning_context)

    def _build_evaluator(
        self,
        *,
        battery_kwh: float,
        standing_charge_rate: float,
        battery_capacity_kwh: float,
        inverter_size_kw_default: float,
        inverter_efficiency_default: float,
    ):
        return self._evaluator_factory(
            battery_kwh,
            standing_charge_rate,
            inverter_size_kw=self._config_entry.options.get(
                const.INVERTER_SIZE_KW, inverter_size_kw_default
            ),
            inverter_efficiency=self._config_entry.options.get(
                const.INVERTER_EFFICIENCY, inverter_efficiency_default
            ),
            battery_capacity_kwh=battery_capacity_kwh,
        )

    def _build_slot_stage_data(self, *, planning_inputs, time_now: datetime) -> list[SlotStageData]:
        time_end = planning_inputs.time_end
        max_range = (time_end - time_now).total_seconds() / 60

        ratedata = None
        export_ratedata = None
        tempdata = planning_inputs.current_temperature
        solardata = 0

        slot_stage_data: list[SlotStageData] = []
        for value in range(0, int(max_range), 30):
            current_time = time_now + timedelta(minutes=value)

            tempdata = planning_inputs.resolve_temperature(
                current_time=current_time,
                last_temperature=tempdata,
            )
            export_ratedata = planning_inputs.resolve_export_rate(
                current_time=current_time,
                last_rate=export_ratedata,
            )
            ratedata = planning_inputs.resolve_import_rate(
                current_time=current_time,
                last_rate=ratedata,
            )
            solardata = planning_inputs.resolve_solar_estimate(
                current_time=current_time,
                last_solar=solardata,
            )

            slot_stage_data.append(
                SlotStageData(
                    current_time=current_time,
                    temperature=tempdata,
                    import_rate=ratedata,
                    export_rate=export_ratedata,
                    solar_estimate=solardata,
                    physics_kwh=self._power_calculator.from_temp_and_time(
                        current_time,
                        tempdata,
                    ),
                )
            )

        return slot_stage_data

    async def _predict_ml_corrections(
        self,
        *,
        ml_client,
        slot_stage_data: list[SlotStageData],
    ) -> list[float]:
        if not ml_client or not ml_client.is_ready:
            return []

        predict_inputs = [
            {
                "slot_time": s.current_time.isoformat(),
                "temp_c": s.temperature,
                "physics_kwh": s.physics_kwh,
            }
            for s in slot_stage_data
        ]
        return await ml_client.async_predict_batch(predict_inputs)

    def _apply_constraints_and_evaluate(
        self,
        *,
        slot_stage_data: list[SlotStageData],
        ml_corrections: list[float],
        evaluator,
        inverter_size_kw_default: float,
    ) -> tuple[list[dict], float, list[str | None], list, float]:
        daily_forecast: list[dict] = []
        forced_actions: list[str | None] = []
        slot_adjustment_kwh_total = 0.0

        inverter_size_kw = float(
            self._config_entry.options.get(
                const.INVERTER_SIZE_KW,
                inverter_size_kw_default,
            )
        )

        for i, slot in enumerate(slot_stage_data):
            slot_end = slot.current_time + timedelta(minutes=30)
            axle_adjustment_kwh, forced_action = self._planning_strategy.axle_constraints(
                slot_start=slot.current_time,
                slot_end=slot_end,
                inverter_size_kw=inverter_size_kw,
            )

            required_power = ml_corrections[i] if i < len(ml_corrections) else slot.physics_kwh
            required_power += axle_adjustment_kwh
            slot_adjustment_kwh_total += axle_adjustment_kwh
            forced_actions.append(forced_action)

            daily_forecast.append(
                {
                    "time": slot.current_time.isoformat(),
                    "temp_c": round(slot.temperature, 1)
                    if slot.temperature is not None
                    else None,
                    "kwh": round(required_power, 4),
                    "physics_kwh": round(slot.physics_kwh, 4),
                    "axle_adjustment_kwh": round(axle_adjustment_kwh, 4),
                    "forced_action": forced_action,
                }
            )

            evaluator.add_data(
                slot.current_time,
                slot.import_rate,
                slot.export_rate,
                required_power,
                slot.solar_estimate,
            )

        evaluator.set_forced_actions(forced_actions)
        timeslots, total_cost = evaluator.evaluate()
        return daily_forecast, slot_adjustment_kwh_total, forced_actions, timeslots, total_cost

    async def async_compute_plan(
        self,
        *,
        hass,
        session,
        time_now: datetime,
        ml_client,
        battery_capacity_kwh: float,
        inverter_size_kw_default: float,
        inverter_efficiency_default: float,
    ) -> PlanComputationResult | None:
        """Compute a full plan from provider-backed strategy inputs."""
        planning_inputs = await self._collect_inputs(
            hass=hass,
            session=session,
            time_now=time_now,
        )

        battery_kw = planning_inputs.battery_kwh
        if battery_kw is None:
            return None

        evaluator = self._build_evaluator(
            battery_kwh=battery_kw,
            standing_charge_rate=planning_inputs.standing_charge_rate,
            battery_capacity_kwh=battery_capacity_kwh,
            inverter_size_kw_default=inverter_size_kw_default,
            inverter_efficiency_default=inverter_efficiency_default,
        )

        slot_stage_data = self._build_slot_stage_data(
            planning_inputs=planning_inputs,
            time_now=time_now,
        )
        ml_corrections = await self._predict_ml_corrections(
            ml_client=ml_client,
            slot_stage_data=slot_stage_data,
        )
        (
            daily_forecast,
            slot_adjustment_kwh_total,
            _,
            timeslots,
            total_cost,
        ) = self._apply_constraints_and_evaluate(
            slot_stage_data=slot_stage_data,
            ml_corrections=ml_corrections,
            evaluator=evaluator,
            inverter_size_kw_default=inverter_size_kw_default,
        )

        return PlanComputationResult(
            timeslots=timeslots,
            total_cost=total_cost,
            daily_forecast=daily_forecast,
            slot_adjustment_kwh_total=slot_adjustment_kwh_total,
            import_rates=planning_inputs.import_rates,
            today_consumption=planning_inputs.today_consumption,
        )
