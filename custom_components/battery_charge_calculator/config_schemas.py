"""Schema helper functions for config and options flows."""

import voluptuous as vol
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig
from . import const


def get_schema(
    serialno="",
    apitoken="",
    octopus_account_number="",
    octopus_api_key="",
    simulate=False,
    inverter_size_kw=const.DEFAULT_INVERTER_SIZE_KW,
    inverter_efficiency=const.DEFAULT_INVERTER_EFFICIENCY,
    battery_capacity_kwh=const.DEFAULT_BATTERY_CAPACITY_KWH,
    base_load=const.DEFAULT_BASE_LOAD_KWH_30MIN,
    **_kwargs,
):
    return vol.Schema(
        {
            vol.Required(const.GIVENERGY_SERIAL_NUMBER, default=serialno): str,
            vol.Required(const.GIVENERGY_API_TOKEN, default=apitoken): str,
            vol.Required(
                const.OCTOPUS_ACCOUNT_NUMBER, default=octopus_account_number
            ): str,
            vol.Required(const.OCTOPUS_APIKEY, default=octopus_api_key): str,
            vol.Required(const.SIMULATE_ONLY, default=simulate): bool,
            vol.Required(
                const.INVERTER_SIZE_KW, default=inverter_size_kw
            ): const.cv_float,
            vol.Required(
                const.INVERTER_EFFICIENCY, default=inverter_efficiency
            ): const.cv_float,
            vol.Optional(
                const.BATTERY_CAPACITY_KWH, default=battery_capacity_kwh
            ): const.cv_float,
            vol.Optional(const.BASE_LOAD_KWH_30MIN, default=base_load): const.cv_float,
        }
    )


def _heating_type_schema(heating_type=const.DEFAULT_HEATING_TYPE):
    return vol.Schema(
        {
            vol.Required(const.HEATING_TYPE, default=heating_type): SelectSelector(
                SelectSelectorConfig(
                    options=const.HEATING_TYPES,
                    translation_key="heating_type",
                )
            ),
        }
    )


def _heating_interpolation_schema(known_points=const.DEFAULT_HEATING_KNOWN_POINTS):
    return vol.Schema(
        {
            vol.Optional(const.HEATING_KNOWN_POINTS, default=known_points): str,
        }
    )


def _heating_electric_schema(
    cop=const.DEFAULT_HEATING_COP,
    indoor_temp=const.DEFAULT_HEATING_INDOOR_TEMP,
    flow_temp=const.DEFAULT_HEATING_FLOW_TEMP,
):
    return vol.Schema(
        {
            vol.Optional(const.HEATING_COP, default=cop): const.cv_float,
            vol.Optional(
                const.HEATING_INDOOR_TEMP, default=indoor_temp
            ): const.cv_float,
            vol.Optional(const.HEATING_FLOW_TEMP, default=flow_temp): const.cv_float,
        }
    )


def _heat_loss_method_schema(method=const.HEAT_LOSS_METHOD_KNOWN):
    return vol.Schema(
        {
            vol.Required(const.HEAT_LOSS_METHOD, default=method): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        const.HEAT_LOSS_METHOD_KNOWN,
                        const.HEAT_LOSS_METHOD_REPORT,
                        const.HEAT_LOSS_METHOD_ESTIMATE,
                    ],
                    translation_key="heat_loss_method",
                )
            ),
        }
    )


def _heat_loss_report_schema(
    watts=const.DEFAULT_HEAT_LOSS_REPORT_WATTS,
    outdoor_temp=const.DEFAULT_HEAT_LOSS_REPORT_OUTDOOR_TEMP,
    indoor_temp=const.DEFAULT_HEAT_LOSS_REPORT_INDOOR_TEMP,
):
    return vol.Schema(
        {
            vol.Required(const.HEAT_LOSS_REPORT_WATTS, default=watts): const.cv_float,
            vol.Required(
                const.HEAT_LOSS_REPORT_OUTDOOR_TEMP, default=outdoor_temp
            ): const.cv_float,
            vol.Required(
                const.HEAT_LOSS_REPORT_INDOOR_TEMP, default=indoor_temp
            ): const.cv_float,
        }
    )


def _heat_loss_known_schema(heat_loss=const.DEFAULT_HEATING_HEAT_LOSS):
    return vol.Schema(
        {
            vol.Required(const.HEATING_HEAT_LOSS, default=heat_loss): const.cv_float,
        }
    )


def _building_estimate_schema(
    floor_area=const.DEFAULT_BUILDING_FLOOR_AREA,
    age=const.DEFAULT_BUILDING_AGE,
    wall_type=const.DEFAULT_BUILDING_WALL_TYPE,
    glazing=const.DEFAULT_BUILDING_GLAZING,
):
    return vol.Schema(
        {
            vol.Optional(const.BUILDING_FLOOR_AREA, default=floor_area): const.cv_float,
            vol.Optional(const.BUILDING_AGE, default=age): SelectSelector(
                SelectSelectorConfig(
                    options=const.BUILDING_AGE_BANDS
                    if hasattr(const, "BUILDING_AGE_BANDS")
                    else [],
                    translation_key="building_age",
                )
            ),
            vol.Optional(const.BUILDING_WALL_TYPE, default=wall_type): SelectSelector(
                SelectSelectorConfig(
                    options=const.BUILDING_WALL_TYPES,
                    translation_key="building_wall_type",
                )
            ),
            vol.Optional(const.BUILDING_GLAZING, default=glazing): SelectSelector(
                SelectSelectorConfig(
                    options=const.BUILDING_GLAZING_TYPES
                    if hasattr(const, "BUILDING_GLAZING_TYPES")
                    else [],
                    translation_key="building_glazing",
                )
            ),
        }
    )
