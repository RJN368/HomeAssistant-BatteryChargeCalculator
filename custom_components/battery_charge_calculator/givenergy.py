import logging
from datetime import datetime, timedelta
import json
import requests
from . import const

# Publish MQTT message using async_publish
from homeassistant.components.mqtt import async_publish, async_subscribe
from homeassistant.core import HomeAssistant, callback as ha_callback
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.components.mqtt.models import ReceiveMessage

_LOGGER = logging.getLogger(__name__)


class GivEnergyMqttController:
    """Alternate controller using MQTT for inverter state changes."""

    def __init__(self, config_entry) -> None:
        self.config_entry = config_entry
        self.options = dict(config_entry.options)
        self.serial = config_entry.options[const.GIVENERGY_SERIAL_NUMBER]
        self._soc_kwh: float | None = None
        self._soc_unsub = None

    async def get_headers(self):
        # Not used in MQTT, kept for interface compatibility
        return {}

    async def postRequest(self, hass, topic, payload):
        encoded = json.dumps(payload) if isinstance(payload, dict) else str(payload)
        await async_publish(hass, topic, encoded, qos=0, retain=False)

    async def getRequest(self, hass, topic, timeout=5):
        """Subscribe to a topic and wait for a single response."""

        future = hass.loop.create_future()

        async def message_received(msg):
            if not future.done():
                future.set_result(msg.payload)

        unsub = await async_subscribe(hass, topic, message_received)
        try:
            result = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            result = None
        finally:
            if unsub is not None:
                unsub()  # type: ignore
        return result

    async def async_start(self, hass: HomeAssistant) -> None:
        """Register a persistent SOC subscription.

        Returns immediately. The _soc_kwh cache is updated in the background
        each time GivTCP publishes to the retained topic.
        """
        soc_topic = f"GivEnergy/{self.serial}/Power/Power/SOC_kWh"

        @ha_callback
        def _on_soc(msg) -> None:
            try:
                self._soc_kwh = float(msg.payload)
                _LOGGER.debug("SOC updated: %.2f kWh from %s", self._soc_kwh, msg.topic)
            except (ValueError, TypeError) as ex:
                _LOGGER.warning("Failed to parse SOC payload '%s': %s — SOC invalidated", msg.payload, ex)
                self._soc_kwh = None

        _LOGGER.debug("Subscribing to SOC topic: %s", soc_topic)
        self._soc_unsub = await async_subscribe(hass, soc_topic, _on_soc)

    async def async_stop(self) -> None:
        """Unsubscribe the persistent SOC subscription."""
        if self._soc_unsub is not None:
            self._soc_unsub()
            self._soc_unsub = None

    async def get_inverter_soc_kwh(self, hass) -> float | None:
        """Return the cached battery state of charge in kWh, or None if not yet received."""
        _LOGGER.info("Battery SOC: %s kWh (cached)", self._soc_kwh)
        return self._soc_kwh


    async def disableCharge(self, hass):
        topic = f"GivEnergy/control/{self.serial}/forceCharge"
        await self.postRequest(hass, topic, "Normal")
        _LOGGER.debug("MQTT forceCharge Normal sent")

    async def disableExport(self, hass):
        topic = f"GivEnergy/control/{self.serial}/forceExport"
        await self.postRequest(hass, topic, "Normal")
        _LOGGER.debug("MQTT forceExport Normal sent")

    async def enableCharge(self, hass):
        topic = f"GivEnergy/control/{self.serial}/forceCharge"
        await self.postRequest(hass, topic, "30")
        _LOGGER.info("MQTT forceCharge 30 min sent")

    async def enableExport(self, hass):
        topic = f"GivEnergy/control/{self.serial}/forceExport"
        await self.postRequest(hass, topic, "30")
        _LOGGER.info("MQTT forceExport 30 min sent")
