"""Unit tests for GivEnergyMqttController MQTT topics and SOC retrieval."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# conftest installs HA stubs before this import
from custom_components.battery_charge_calculator.givenergy import (
    GivEnergyMqttController,
)

INVERTER_SERIAL = "ED2253G215"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_msg(topic: str, payload: str) -> MagicMock:
    """Build a minimal MQTT message stub."""
    msg = MagicMock()
    msg.topic = topic
    msg.payload = payload
    return msg


def _make_hass() -> MagicMock:
    """Build a minimal hass stub with a real event loop."""
    hass = MagicMock()
    loop = asyncio.get_event_loop()
    hass.loop = loop
    return hass


@pytest.fixture
def controller():
    config_entry = MagicMock()
    config_entry.options = {
        "givenergy_api_serial_number": INVERTER_SERIAL,
    }
    return GivEnergyMqttController(config_entry)


# ---------------------------------------------------------------------------
# async_start / async_stop tests
# ---------------------------------------------------------------------------


async def test_async_start_caches_soc_from_retained_message(controller):
    """async_start callback immediately updates _soc_kwh from the retained message."""
    hass = _make_hass()
    soc_msg = _make_msg(f"GivEnergy/{INVERTER_SERIAL}/Power/Power/SOC_kWh", "7.5")

    async def fake_subscribe(hass, topic, callback):
        callback(soc_msg)
        return MagicMock()

    with patch(
        "custom_components.battery_charge_calculator.givenergy.async_subscribe",
        side_effect=fake_subscribe,
    ):
        await controller.async_start(hass)

    assert controller._soc_kwh == 7.5


async def test_async_start_subscribes_to_correct_topic(controller):
    """async_start subscribes to the exact serial-based SOC topic."""
    hass = _make_hass()
    subscribe_calls = []

    async def fake_subscribe(hass, topic, callback):
        subscribe_calls.append(topic)
        return MagicMock()

    with patch(
        "custom_components.battery_charge_calculator.givenergy.async_subscribe",
        side_effect=fake_subscribe,
    ):
        await controller.async_start(hass)

    assert subscribe_calls == [f"GivEnergy/{INVERTER_SERIAL}/Power/Power/SOC_kWh"]


async def test_async_start_handles_invalid_payload(controller):
    """async_start invalidates SOC (sets to None) when payload cannot be parsed."""
    hass = _make_hass()
    bad_msg = _make_msg(f"GivEnergy/{INVERTER_SERIAL}/Power/Power/SOC_kWh", "bad")

    async def fake_subscribe(hass, topic, callback):
        callback(bad_msg)
        return MagicMock()

    with patch(
        "custom_components.battery_charge_calculator.givenergy.async_subscribe",
        side_effect=fake_subscribe,
    ):
        await controller.async_start(hass)

    assert controller._soc_kwh is None


async def test_async_stop_unsubscribes(controller):
    """async_stop calls the unsub function and clears it."""
    unsub_mock = MagicMock()
    controller._soc_unsub = unsub_mock
    await controller.async_stop()
    unsub_mock.assert_called_once()
    assert controller._soc_unsub is None


# ---------------------------------------------------------------------------
# SOC cached value tests
# ---------------------------------------------------------------------------


async def test_get_inverter_soc_kwh_returns_cached_value(controller):
    """get_inverter_soc_kwh returns the in-memory cached value."""
    hass = _make_hass()
    controller._soc_kwh = 9.12
    result = await controller.get_inverter_soc_kwh(hass)
    assert result == 9.12


async def test_get_inverter_soc_kwh_returns_none_when_not_received(controller):
    """get_inverter_soc_kwh returns None before any MQTT message has arrived."""
    hass = _make_hass()
    assert controller._soc_kwh is None
    result = await controller.get_inverter_soc_kwh(hass)
    assert result is None


# ---------------------------------------------------------------------------
# Command topic tests
# ---------------------------------------------------------------------------


async def test_disable_charge_topic(controller):
    hass = _make_hass()
    with patch(
        "custom_components.battery_charge_calculator.givenergy.async_publish",
        new_callable=AsyncMock,
    ) as mock_pub:
        await controller.disableCharge(hass)
    mock_pub.assert_awaited_once_with(
        hass,
        f"GivEnergy/control/{INVERTER_SERIAL}/forceCharge",
        "Normal",
        qos=0,
        retain=False,
    )


async def test_disable_export_topic(controller):
    hass = _make_hass()
    with patch(
        "custom_components.battery_charge_calculator.givenergy.async_publish",
        new_callable=AsyncMock,
    ) as mock_pub:
        await controller.disableExport(hass)
    mock_pub.assert_awaited_once_with(
        hass,
        f"GivEnergy/control/{INVERTER_SERIAL}/forceExport",
        "Normal",
        qos=0,
        retain=False,
    )


async def test_enable_charge_topic(controller):
    hass = _make_hass()
    with patch(
        "custom_components.battery_charge_calculator.givenergy.async_publish",
        new_callable=AsyncMock,
    ) as mock_pub:
        await controller.enableCharge(hass)
    mock_pub.assert_awaited_once_with(
        hass,
        f"GivEnergy/control/{INVERTER_SERIAL}/forceCharge",
        "30",
        qos=0,
        retain=False,
    )


async def test_enable_export_topic(controller):
    hass = _make_hass()
    with patch(
        "custom_components.battery_charge_calculator.givenergy.async_publish",
        new_callable=AsyncMock,
    ) as mock_pub:
        await controller.enableExport(hass)
    mock_pub.assert_awaited_once_with(
        hass,
        f"GivEnergy/control/{INVERTER_SERIAL}/forceExport",
        "30",
        qos=0,
        retain=False,
    )
