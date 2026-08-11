"""Default planning strategy backed by provider adapters."""

from __future__ import annotations

from datetime import datetime

from .models import PlanningInputs, PlanningProviderContext
from .providers.axle.axle_planning_provider import AxlePlanningProvider
from .providers.battery.givenergy_planning_provider import GivEnergyPlanningProvider
from .providers.solar.solar_planning_provider import SolarPlanningProvider
from .providers.tariff.tariff_consumption_planning_provider import (
    TariffConsumptionPlanningProvider,
)
from .providers.temperature.temperature_planning_provider import (
    TemperaturePlanningProvider,
)


class DefaultPlanningStrategy:
    """Collect and expose planning inputs using discrete providers."""

    def __init__(
        self,
        *,
        tariff_consumption_provider: TariffConsumptionPlanningProvider,
        givenergy_provider: GivEnergyPlanningProvider,
        temperature_provider: TemperaturePlanningProvider,
        solar_provider: SolarPlanningProvider,
        axle_provider: AxlePlanningProvider,
    ) -> None:
        self._tariff_consumption = tariff_consumption_provider
        self._givenergy = givenergy_provider
        self._temperature = temperature_provider
        self._solar = solar_provider
        self._axle = axle_provider

    async def collect_inputs(
        self,
        *,
        context: PlanningProviderContext,
    ) -> PlanningInputs:
        standing_charge_rate = await self._tariff_consumption.fetch_standing_charge(
            context.session
        )
        import_rates = await self._tariff_consumption.fetch_import_rates(
            context.session
        )
        export_rates = await self._tariff_consumption.fetch_export_rates(
            context.session
        )

        request = self._tariff_consumption.build_consumption_request(context=context)
        today_consumption = await self._tariff_consumption.fetch_today_consumption(
            context.session,
            request=request,
        )

        battery_kwh = await self._givenergy.get_battery_soc_kwh(context.hass)

        current_temperature = await self._temperature.fetch_current_temperature(
            context.hass
        )
        hourly_temperature_forecast = await self._temperature.fetch_hourly_forecast(
            context.hass
        )

        time_end = import_rates[-1]["end"]
        solar_forecast = await self._solar.fetch_forecast(
            context.hass,
            start_date_time=context.time_now,
            end_date_time=time_end,
        )

        return PlanningInputs(
            standing_charge_rate=standing_charge_rate,
            import_rates=import_rates,
            export_rates=export_rates,
            today_consumption=today_consumption,
            battery_kwh=battery_kwh,
            time_end=time_end,
            current_temperature=current_temperature,
            hourly_temperature_forecast=hourly_temperature_forecast,
            solar_forecast=solar_forecast,
        )

    def axle_constraints(
        self,
        *,
        slot_start: datetime,
        slot_end: datetime,
        inverter_size_kw: float,
    ) -> tuple[float, str | None]:
        return (
            self._axle.export_adjustment_kwh(
                slot_start=slot_start,
                slot_end=slot_end,
                inverter_size_kw=inverter_size_kw,
            ),
            self._axle.forced_action(
                slot_start=slot_start,
                slot_end=slot_end,
            ),
        )
