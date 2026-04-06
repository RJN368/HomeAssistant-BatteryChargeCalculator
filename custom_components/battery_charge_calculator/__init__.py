import logging

from homeassistant.components.mqtt import async_wait_for_mqtt_client
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from . import const
from .coordinators import BatteryChargeCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [SENSOR_DOMAIN]


async def async_setup(hass, config):
    """Track states and offer events for sensors."""
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Battery Charge Calculator from a config entry."""
    coordinator = BatteryChargeCoordinator(hass, entry)

    if not await async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("MQTT is not available — will retry")

    await coordinator.givenergy.async_start(hass)

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(const.DOMAIN, coordinator.id)},
        name=const.TITLE,
        model=const.DOMAIN,
        sw_version=const.VERSION,
        manufacturer="@rjn368",
    )

    hass.data.setdefault(const.DOMAIN, {})[entry.entry_id] = coordinator

    async def handle_trigger_planning(call):
        """Service handler: run a full planning cycle immediately."""
        _LOGGER.info("trigger_planning service called — running planning cycle")
        await coordinator.octopus_state_change_listener(None)

    hass.services.async_register(
        const.DOMAIN, "trigger_planning", handle_trigger_planning
    )

    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator = hass.data[const.DOMAIN].pop(entry.entry_id)
        await coordinator.givenergy.async_stop()
    return unload_ok
