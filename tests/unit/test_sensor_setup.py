"""Unit tests for sensor entity registration."""

from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.battery_charge_calculator import const
from custom_components.battery_charge_calculator.axle_windows import AxleWindow


def _install_stub_sensors_module() -> type:
    """Install a lightweight sensors module used by sensor.py imports in tests."""

    class _BaseSensor:
        def __init__(self, _hass, _coordinator):
            pass

    class AxleRemoteControlSensor(_BaseSensor):
        pass

    module = types.ModuleType("custom_components.battery_charge_calculator.sensors")
    module.AnnualForecastSensor = _BaseSensor
    module.AxleRemoteControlSensor = AxleRemoteControlSensor
    module.BatteryProjectionSensor = _BaseSensor
    module.BatterySocSensor = _BaseSensor
    module.CostPredictionSensor = _BaseSensor
    module.DailyPowerForecastSensor = _BaseSensor
    module.EstimatedPowerDemandSensor = _BaseSensor
    module.LastRecalculationSensor = _BaseSensor
    module.MLModelStatusSensor = _BaseSensor
    module.MLPowerSurfaceSensor = _BaseSensor
    module.TariffComparisonSensor = _BaseSensor
    module.TimeSlotSensor = _BaseSensor

    sys.modules[module.__name__] = module
    return AxleRemoteControlSensor


def _import_sensor_module():
    """Import sensor.py after stubbing dependencies."""
    sys.modules.pop("custom_components.battery_charge_calculator.sensor", None)
    return importlib.import_module("custom_components.battery_charge_calculator.sensor")


def _import_axle_remote_control_sensor_class():
    """Import only the Axle sensor submodule without loading sensors/__init__."""
    package_name = "custom_components.battery_charge_calculator.sensors"
    module_name = f"{package_name}.axle_remote_control"

    package = types.ModuleType(package_name)
    package.__path__ = [
        str(
            Path(__file__).resolve().parents[2]
            / "custom_components"
            / "battery_charge_calculator"
            / "sensors"
        )
    ]
    sys.modules[package_name] = package
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    return module.AxleRemoteControlSensor


@pytest.mark.asyncio
async def test_axle_sensor_not_registered_when_axle_disabled() -> None:
    AxleRemoteControlSensor = _install_stub_sensors_module()
    sensor_module = _import_sensor_module()

    hass = MagicMock()
    coordinator = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {
        const.ML_ENABLED: False,
        const.AXLE_ENABLED: False,
        const.TARIFF_COMPARISON_ENABLED: False,
    }
    hass.data = {const.DOMAIN: {entry.entry_id: coordinator}}

    entities = []

    await sensor_module.async_setup_entry(
        hass,
        entry,
        lambda new_entities: entities.extend(new_entities),
    )

    assert not any(isinstance(entity, AxleRemoteControlSensor) for entity in entities)


@pytest.mark.asyncio
async def test_axle_sensor_registered_when_axle_enabled() -> None:
    AxleRemoteControlSensor = _install_stub_sensors_module()
    sensor_module = _import_sensor_module()

    hass = MagicMock()
    coordinator = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.options = {
        const.ML_ENABLED: False,
        const.AXLE_ENABLED: True,
        const.TARIFF_COMPARISON_ENABLED: False,
    }
    hass.data = {const.DOMAIN: {entry.entry_id: coordinator}}

    entities = []

    await sensor_module.async_setup_entry(
        hass,
        entry,
        lambda new_entities: entities.extend(new_entities),
    )

    assert any(isinstance(entity, AxleRemoteControlSensor) for entity in entities)


def test_axle_remote_control_diagnostics_schema_unavailable() -> None:
    AxleRemoteControlSensor = _import_axle_remote_control_sensor_class()

    coordinator = MagicMock()
    coordinator._axle_cache = {
        "source_status": const.AXLE_SOURCE_STATUS_UNAVAILABLE,
        "is_active": False,
        "suppression_reason": None,
        "last_transition_reason": None,
        "last_error": "source unavailable",
        "last_success_utc": None,
    }
    coordinator.config_entry.options = {
        const.AXLE_FAIL_SAFE_MODE: const.AXLE_FAIL_SAFE_MODE_OPEN,
        const.AXLE_NEUTRALIZE_ON_ACTIVE_ENTRY: True,
        const.AXLE_POLL_INTERVAL_SECONDS: 60,
        const.AXLE_REQUEST_TIMEOUT_SECONDS: 10,
    }
    coordinator._axle_overlapping_window.return_value = None
    coordinator._axle_cache_age_seconds.return_value = None

    sensor = AxleRemoteControlSensor(MagicMock(), coordinator)

    assert sensor._attr_native_value == "unavailable"
    attrs = sensor._attr_extra_state_attributes
    assert set(attrs.keys()) == {
        "source_status",
        "suppression_reason",
        "last_transition_reason",
        "last_error",
        "active_window_start",
        "active_window_end",
        "cache_age_seconds",
        "fail_safe_mode",
        "neutralize_on_active_entry",
        "poll_interval_seconds",
        "request_timeout_seconds",
    }
    assert attrs["active_window_start"] is None
    assert attrs["active_window_end"] is None
    assert attrs["cache_age_seconds"] is None
    assert attrs["source_status"] == const.AXLE_SOURCE_STATUS_UNAVAILABLE


def test_axle_remote_control_diagnostics_active_window_fields() -> None:
    AxleRemoteControlSensor = _import_axle_remote_control_sensor_class()

    now_utc = datetime.now(timezone.utc)
    active_window = AxleWindow(
        start=now_utc - timedelta(minutes=2),
        end=now_utc + timedelta(minutes=2),
    )

    coordinator = MagicMock()
    coordinator._axle_cache = {
        "source_status": const.AXLE_SOURCE_STATUS_STALE,
        "is_active": True,
        "suppression_reason": const.AXLE_SUPPRESSION_REASON_ACTIVE_WINDOW,
        "last_transition_reason": const.AXLE_TRANSITION_REASON_ACTIVE_ENTRY,
        "last_error": None,
        "last_success_utc": now_utc,
    }
    coordinator.config_entry.options = {
        const.AXLE_FAIL_SAFE_MODE: const.AXLE_FAIL_SAFE_MODE_CLOSED,
        const.AXLE_NEUTRALIZE_ON_ACTIVE_ENTRY: True,
        const.AXLE_POLL_INTERVAL_SECONDS: 120,
        const.AXLE_REQUEST_TIMEOUT_SECONDS: 15,
    }
    coordinator._axle_overlapping_window.return_value = active_window
    coordinator._axle_cache_age_seconds.return_value = 120.0

    sensor = AxleRemoteControlSensor(MagicMock(), coordinator)

    assert sensor._attr_native_value == "active"
    attrs = sensor._attr_extra_state_attributes
    assert attrs["source_status"] == const.AXLE_SOURCE_STATUS_STALE
    assert attrs["suppression_reason"] == const.AXLE_SUPPRESSION_REASON_ACTIVE_WINDOW
    assert attrs["last_transition_reason"] == const.AXLE_TRANSITION_REASON_ACTIVE_ENTRY
    assert attrs["active_window_start"] == active_window.start.isoformat()
    assert attrs["active_window_end"] == active_window.end.isoformat()
    assert attrs["cache_age_seconds"] == 120.0
    assert attrs["fail_safe_mode"] == const.AXLE_FAIL_SAFE_MODE_CLOSED
