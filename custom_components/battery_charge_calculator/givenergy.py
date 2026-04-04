import logging
from datetime import datetime, timedelta
import json
import requests
from . import const

# Publish MQTT message using async_publish
from homeassistant.components.mqtt import async_publish, async_subscribe
import asyncio

_LOGGER = logging.getLogger(__name__)


class GivEnergyMqttController:
    """Alternate controller using MQTT for inverter state changes."""

    def __init__(self, config_entry) -> None:
        self.config_entry = config_entry
        self.options = dict(config_entry.options)
        self.serial = config_entry.options[const.GIVENERGY_SERIAL_NUMBER]

    async def get_headers(self):
        # Not used in MQTT, kept for interface compatibility
        return {}

    async def postRequest(self, hass, topic, payload):
        await async_publish(hass, topic, json.dumps(payload), qos=0, retain=False)

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

    async def disableCharge(self, hass):
        topic = f"givenergy/{self.serial}/settings/charge/disable"
        payload = {"value": "False", "context": "False"}
        await self.postRequest(hass, topic, payload)
        _LOGGER.debug(f"MQTT disabledCharge sent: {payload}")

    async def disableExport(self, hass):
        topic = f"givenergy/{self.serial}/settings/export/disable"
        payload = {"value": "False", "context": "False"}
        await self.postRequest(hass, topic, payload)
        _LOGGER.debug(f"MQTT disabledExport sent: {payload}")

    async def get_inverter_soc_kwh(self, hass) -> float:
        # Listen for Battery_SOC on wildcard topic
        battery_soc_topic = f"GivEnergy/{self.serial}/Power/Power/SOC_kWh"

        future = hass.loop.create_future()

        def message_received(msg):
            if not future.done():
                try:
                    # The topic will be like GivEnergy/<inverter_serial>/Battery_Details/<battery_serial>/Battery_SOC
                    payload = float(msg.payload)
                    _LOGGER.info(
                        f"Received Battery_SOC: {payload} from topic: {msg.topic}"
                    )
                    hass.loop.call_soon_threadsafe(future.set_result, payload)
                except Exception as ex:
                    _LOGGER.error(f"Failed to parse Battery_SOC: {ex}")
                    hass.loop.call_soon_threadsafe(future.set_result, None)

        unsub = await async_subscribe(hass, battery_soc_topic, message_received)
        try:
            # Publish to status topic to trigger status update
            status_topic = f"GivEnergy/{self.serial}/status"
            await self.postRequest(hass, status_topic, {})
            _LOGGER.info(f"MQTT status request sent to: {status_topic}")

            soc = await asyncio.wait_for(future, timeout=10)
        except asyncio.TimeoutError:
            soc = 0
        finally:
            if unsub is not None:
                unsub()
        return soc

    async def enableExport(self, hass):
        await self.disableCharge(hass)
        topic = f"givenergy/{self.serial}/settings/export/enable"
        payload = {"value": "True", "context": "True"}
        await self.postRequest(hass, topic, payload)
        _LOGGER.warning(f"MQTT enabledExport sent: {payload}")

    async def enableCharge(self, hass):
        await self.disableExport(hass)
        topic = f"givenergy/{self.serial}/settings/charge/enable"
        payload = {"value": "True", "context": "True"}
        await self.postRequest(hass, topic, payload)
        _LOGGER.warning(f"MQTT enabledCharge sent: {payload}")


class GivEnergyCotroller:
    baseUrl = "https://api.givenergy.cloud/v1/inverter/"

    def __init__(self, config_entry) -> None:
        self.config_entry = config_entry
        self.options = dict(config_entry.options)
        self.baseUrl = (
            self.baseUrl + config_entry.options[const.GIVENERGY_SERIAL_NUMBER] + "/"
        )

    async def get_headers(self):
        apiKey = self.options[const.GIVENERGY_API_TOKEN]

        headers = {
            "Authorization": f"Bearer {apiKey}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        return headers

    def postRequest(self, url, headers, payload):
        return requests.request("POST", url, headers=headers, json=payload)

    def getRequest(self, url, headers):
        return requests.request("GET", url, headers=headers)

    async def disableCharge(self, hass):
        headers = await self.get_headers()

        payload = {"value": "False", "context": "False"}

        response = await hass.async_add_executor_job(
            self.postRequest, self.baseUrl + "settings/66/write", headers, payload
        )
        _LOGGER.debug(f"disabledCharge: {response.json()} ")

    async def disableExport(self, hass):
        headers = await self.get_headers()

        payload = {"value": "False", "context": "False"}

        response = await hass.async_add_executor_job(
            self.postRequest, self.baseUrl + "settings/56/write", headers, payload
        )
        _LOGGER.debug(f"disabledExport: {response.json()} ")

    async def get_inverter_soc_kwh(self, hass):
        _LOGGER.debug(f"get_inverter_soc_kwh")

        headers = await self.get_headers()

        response = await hass.async_add_executor_job(
            self.getRequest, self.baseUrl + f"system-data/latest", headers
        )

        return (
            float(json.loads(response.content)["data"]["battery"]["percent"]) / 100 * 9
        )

    async def enableExport(self, hass):
        await self.disableCharge(hass)

        headers = await self.get_headers()

        response = await hass.async_add_executor_job(
            self.getRequest, self.baseUrl + "settings", headers
        )

        payload = {"value": "23:59", "context": "23:59"}

        response = await hass.async_add_executor_job(
            self.postRequest, self.baseUrl + "settings/54/write", headers, payload
        )
        _LOGGER.warning(f"enabledCharge-endtime: {response.json()} ")

        payload = {"value": "0:00", "context": "0:00"}

        response = await hass.async_add_executor_job(
            self.postRequest, self.baseUrl + "settings/53/write", headers, payload
        )
        _LOGGER.warning(f"enabledCharge-starttime: {response.json()} ")

        payload = {"value": "True", "context": "True"}

        response = await hass.async_add_executor_job(
            self.postRequest, self.baseUrl + "settings/56/write", headers, payload
        )
        _LOGGER.warning(f"enabledCharge: {response.json()} ")

    async def enableCharge(self, hass):
        await self.disableExport(hass)

        headers = await self.get_headers()

        payload = {"value": "23:59", "context": "23:59"}

        response = await hass.async_add_executor_job(
            self.postRequest, self.baseUrl + "settings/65/write", headers, payload
        )
        _LOGGER.warning(f"enabledCharge-endtime: {response.json()} ")

        payload = {"value": "0:00", "context": "0:00"}

        response = await hass.async_add_executor_job(
            self.postRequest, self.baseUrl + "settings/64/write", headers, payload
        )
        _LOGGER.warning(f"enabledCharge-starttime: {response.json()} ")

        payload = {"value": "True", "context": "True"}

        response = await hass.async_add_executor_job(
            self.postRequest, self.baseUrl + "settings/66/write", headers, payload
        )
        _LOGGER.warning(f"enabledCharge: {response.json()} ")
