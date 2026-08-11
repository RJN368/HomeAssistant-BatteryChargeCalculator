"""Factory wiring for planning strategy and provider adapters."""

from __future__ import annotations

import logging

from .. import const

from .adapters import (
    AxlePlanningAdapter,
    GivEnergyPlanningAdapter,
    HomeAssistantTemperaturePlanningAdapter,
    OctopusTariffConsumptionPlanningAdapter,
    SolarPlanningAdapter,
)
from .provider_family import ProviderBuilder, ProviderFamily
from .strategy import DefaultPlanningStrategy

_LOGGER = logging.getLogger(__name__)


def _build_tariff_provider(coordinator):
    return OctopusTariffConsumptionPlanningAdapter(coordinator.agile_rates_client)


def _build_battery_provider(coordinator):
    return GivEnergyPlanningAdapter(coordinator.givenergy)


def _build_solar_provider(_coordinator):
    return SolarPlanningAdapter()


def _build_axle_provider(coordinator):
    return AxlePlanningAdapter(
        export_adjustment_fn=coordinator.axle_slot_export_adjustment_kwh,
        forced_action_fn=coordinator.axle_slot_forced_action,
    )


def _build_temperature_provider(_coordinator):
    return HomeAssistantTemperaturePlanningAdapter()


TARIFF_PROVIDER_BUILDERS: dict[str, ProviderBuilder] = {
    const.PLANNING_TARIFF_PROVIDER_OCTOPUS: _build_tariff_provider,
}

BATTERY_PROVIDER_BUILDERS: dict[str, ProviderBuilder] = {
    const.PLANNING_BATTERY_PROVIDER_GIVENERGY_MQTT: _build_battery_provider,
}

SOLAR_PROVIDER_BUILDERS: dict[str, ProviderBuilder] = {
    const.PLANNING_SOLAR_PROVIDER_SOLCAST: _build_solar_provider,
}

AXLE_PROVIDER_BUILDERS: dict[str, ProviderBuilder] = {
    const.PLANNING_AXLE_PROVIDER_AXLE_API: _build_axle_provider,
}

TEMPERATURE_PROVIDER_BUILDERS: dict[str, ProviderBuilder] = {
    const.PLANNING_TEMPERATURE_PROVIDER_HOMEASSISTANT: _build_temperature_provider,
}

PROVIDER_FAMILIES: dict[str, ProviderFamily] = {
    "tariff_consumption": ProviderFamily(
        option_key=const.PLANNING_TARIFF_PROVIDER,
        default_key=const.DEFAULT_PLANNING_TARIFF_PROVIDER,
        provider_name="tariff",
        builders=TARIFF_PROVIDER_BUILDERS,
    ),
    "battery": ProviderFamily(
        option_key=const.PLANNING_BATTERY_PROVIDER,
        default_key=const.DEFAULT_PLANNING_BATTERY_PROVIDER,
        provider_name="battery",
        builders=BATTERY_PROVIDER_BUILDERS,
    ),
    "solar": ProviderFamily(
        option_key=const.PLANNING_SOLAR_PROVIDER,
        default_key=const.DEFAULT_PLANNING_SOLAR_PROVIDER,
        provider_name="solar",
        builders=SOLAR_PROVIDER_BUILDERS,
    ),
    "axle": ProviderFamily(
        option_key=const.PLANNING_AXLE_PROVIDER,
        default_key=const.DEFAULT_PLANNING_AXLE_PROVIDER,
        provider_name="axle",
        builders=AXLE_PROVIDER_BUILDERS,
    ),
    "temperature": ProviderFamily(
        option_key=const.PLANNING_TEMPERATURE_PROVIDER,
        default_key=const.DEFAULT_PLANNING_TEMPERATURE_PROVIDER,
        provider_name="temperature",
        builders=TEMPERATURE_PROVIDER_BUILDERS,
    ),
}


def _resolve_provider_key(
    selected_key: str | None,
    *,
    supported_keys: set[str],
    default_key: str,
    provider_name: str,
) -> str:
    key = selected_key or default_key
    if key in supported_keys:
        return key

    _LOGGER.warning(
        "Unsupported %s provider '%s'; falling back to '%s'",
        provider_name,
        key,
        default_key,
    )
    return default_key


def _resolve_provider(coordinator, options: dict, *, family: ProviderFamily):
    selected_key = _resolve_provider_key(
        options.get(family.option_key),
        supported_keys=set(family.builders),
        default_key=family.default_key,
        provider_name=family.provider_name,
    )
    return family.builders[selected_key](coordinator)


def build_default_planning_strategy(coordinator):
    """Build default strategy bound to existing coordinator dependencies."""
    options = getattr(getattr(coordinator, "config_entry", None), "options", {}) or {}

    resolved_providers = {
        capability: _resolve_provider(coordinator, options, family=family)
        for capability, family in PROVIDER_FAMILIES.items()
    }

    return DefaultPlanningStrategy(
        tariff_consumption_provider=resolved_providers["tariff_consumption"],
        givenergy_provider=resolved_providers["battery"],
        temperature_provider=resolved_providers["temperature"],
        solar_provider=resolved_providers["solar"],
        axle_provider=resolved_providers["axle"],
    )
