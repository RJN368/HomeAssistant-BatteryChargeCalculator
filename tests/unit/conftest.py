"""Shared fixtures for battery_charge_calculator unit tests.

Stubs out all homeassistant imports so the tests can run without a full
Home Assistant installation.
"""

import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock

import pytest


# ---------------------------------------------------------------------------
# Inject homeassistant stub modules before any integration code is loaded
# ---------------------------------------------------------------------------


def _stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _install_ha_stubs() -> None:
    if "homeassistant" in sys.modules:
        return  # already installed (e.g. real HA available)

    # Top-level
    ha = _stub("homeassistant")
    ha.config_entries = _stub("homeassistant.config_entries")
    ha.core = _stub("homeassistant.core")
    ha.exceptions = _stub("homeassistant.exceptions")
    ha.exceptions.ConfigEntryNotReady = Exception
    ha.const = _stub("homeassistant.const")

    # homeassistant.components.*
    _stub("homeassistant.components")
    sensor = _stub("homeassistant.components.sensor")
    sensor.DOMAIN = "sensor"
    sensor_const = _stub("homeassistant.components.sensor.const")

    select = _stub("homeassistant.components.select")

    mqtt = _stub("homeassistant.components.mqtt")
    mqtt.async_publish = AsyncMock()
    mqtt.async_subscribe = AsyncMock(return_value=MagicMock())
    mqtt.subscribe = MagicMock(return_value=MagicMock())
    mqtt.async_wait_for_mqtt_client = AsyncMock(return_value=True)

    # homeassistant.helpers.*
    helpers = _stub("homeassistant.helpers")
    cv = _stub("homeassistant.helpers.config_validation")
    cv.string = str
    cv.boolean = bool
    cv.positive_int = int
    _stub("homeassistant.helpers.update_coordinator")
    _stub("homeassistant.helpers.aiohttp_client")
    _stub("homeassistant.helpers.event")
    dr = _stub("homeassistant.helpers.device_registry")
    _stub("homeassistant.helpers.entity_registry")

    # homeassistant.helpers.selector stubs (for SelectSelector / SelectSelectorConfig)
    selector = _stub("homeassistant.helpers.selector")

    class SelectSelectorConfig:
        def __init__(self, options=None, translation_key=None, **kwargs):
            self.options = options or []
            self.translation_key = translation_key

    class SelectSelector:
        def __init__(self, config=None):
            self.config = config

        # Make voluptuous treat it as a passthrough validator
        def __call__(self, value):
            return value

    selector.SelectSelector = SelectSelector
    selector.SelectSelectorConfig = SelectSelectorConfig

    # homeassistant.util.*
    _stub("homeassistant.util")
    dt_util = _stub("homeassistant.util.dt")
    dt_util.get_time_zone = lambda tz: timezone.utc

    # homeassistant.data_entry_flow
    _stub("homeassistant.data_entry_flow")

    # --- Provide real-enough class stubs ---

    # ConfigEntry
    class ConfigEntry:
        pass

    ha.config_entries.ConfigEntry = ConfigEntry
    ha.config_entries.CONN_CLASS_LOCAL_POLL = "local_pull"
    ha.config_entries.CONN_CLASS_CLOUD_POLL = "cloud_poll"
    ha.config_entries.SOURCE_USER = "user"

    class _OptionsFlow:
        def __init__(self, config_entry=None):
            self.config_entry = config_entry

    class _ConfigFlow:
        VERSION = 1

        def __init_subclass__(cls, domain=None, **kwargs):
            super().__init_subclass__(**kwargs)

    ha.config_entries.ConfigFlow = _ConfigFlow
    ha.config_entries.OptionsFlow = _OptionsFlow

    class FlowResult(dict):
        pass

    sys.modules["homeassistant.data_entry_flow"].FlowResult = FlowResult

    # callback decorator — just return the function unchanged
    ha.core.callback = lambda fn: fn
    ha.core.HomeAssistant = MagicMock

    # CoordinatorEntity base
    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        def async_write_ha_state(self):
            pass

    sys.modules[
        "homeassistant.helpers.update_coordinator"
    ].CoordinatorEntity = CoordinatorEntity

    class DataUpdateCoordinator:
        def __init__(self, hass, logger, name, update_interval, **kwargs):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None
            self._debounced_refresh = MagicMock()

        async def async_refresh(self):
            async with self._debounced_refresh.async_lock:
                pass

        async def async_shutdown(self):
            pass

    sys.modules[
        "homeassistant.helpers.update_coordinator"
    ].DataUpdateCoordinator = DataUpdateCoordinator

    # SensorEntity / SelectEntity
    class SensorEntity:
        pass

    class RestoreSensor:
        pass

    class SelectEntity:
        pass

    sensor.SensorEntity = SensorEntity
    sensor.RestoreSensor = RestoreSensor
    select.SelectEntity = SelectEntity

    class SensorDeviceClass:
        ENERGY = "energy"
        MONETARY = "monetary"

    sensor_const.SensorDeviceClass = SensorDeviceClass

    # UnitOfEnergy
    class UnitOfEnergy:
        KILO_WATT_HOUR = "kWh"

    ha.const.UnitOfEnergy = UnitOfEnergy
    ha.const.STATE_ON = "on"
    ha.const.STATE_OFF = "off"

    # async_track_time_interval — stub that returns a cancellation callable
    sys.modules["homeassistant.helpers.event"].async_track_time_interval = (
        lambda hass, callback, interval: (lambda: None)
    )

    # async_get_clientsession stub
    sys.modules["homeassistant.helpers.aiohttp_client"].async_get_clientsession = (
        lambda hass: MagicMock()
    )

    # device_registry stub
    dr_mock = MagicMock()
    sys.modules["homeassistant.helpers.device_registry"].async_get = (
        lambda hass: dr_mock
    )


_install_ha_stubs()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fixed_dt() -> datetime:
    """Return a fixed aware datetime for use in tests."""
    return datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def mock_config_entry():
    """Return a minimal mock ConfigEntry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.options = {
        "givenergy_api_serial_number": "ABC123",
        "givenergy_api_token": "test-token",
        "octopus_account_number": "A-AAAA1111",
        "octopus_api_key": "test-octopus-key",
        "simulate_only": True,
    }
    return entry
