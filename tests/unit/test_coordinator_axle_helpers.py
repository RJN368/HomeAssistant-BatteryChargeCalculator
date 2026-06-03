"""Unit tests for BatteryChargeCoordinator Axle freshness helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from custom_components.battery_charge_calculator import const
from custom_components.battery_charge_calculator.coordinators import (
    BatteryChargeCoordinator,
)


def _make_coordinator() -> BatteryChargeCoordinator:
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.options = {
        const.OCTOPUS_APIKEY: "test-key",
        const.OCTOPUS_ACCOUNT_NUMBER: "A-1111",
        const.GIVENERGY_SERIAL_NUMBER: "SN001",
        const.GIVENERGY_API_TOKEN: "token",
        const.SIMULATE_ONLY: True,
        const.AXLE_POLL_INTERVAL_SECONDS: 60,
    }

    hass = MagicMock()
    hass.config.time_zone = "Europe/London"
    hass.loop = MagicMock()

    with (
        patch(
            "custom_components.battery_charge_calculator.coordinators.OctopusAgileRatesClient"
        ),
        patch("custom_components.battery_charge_calculator.coordinators.givenergy"),
        patch(
            "custom_components.battery_charge_calculator.coordinators.power_calculator"
        ),
        patch(
            "custom_components.battery_charge_calculator.coordinators.dt_util.get_time_zone",
            return_value=timezone.utc,
        ),
    ):
        coordinator = BatteryChargeCoordinator(hass, entry)

    coordinator.hass = hass
    coordinator.config_entry = entry
    return coordinator


def test_axle_status_unavailable_when_no_success() -> None:
    coordinator = _make_coordinator()

    assert coordinator._axle_cache_age_seconds() is None
    assert (
        coordinator._axle_evaluate_source_status()
        == const.AXLE_SOURCE_STATUS_UNAVAILABLE
    )


def test_axle_status_fresh_when_age_within_multiplier_window() -> None:
    coordinator = _make_coordinator()
    now = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)
    coordinator._axle_cache["last_success_utc"] = now - timedelta(seconds=180)

    assert coordinator._axle_evaluate_source_status(now_utc=now) == const.AXLE_SOURCE_STATUS_FRESH


def test_axle_status_stale_and_unavailable_thresholds() -> None:
    coordinator = _make_coordinator()
    now = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)

    coordinator._axle_cache["last_success_utc"] = now - timedelta(minutes=20)
    assert coordinator._axle_evaluate_source_status(now_utc=now) == const.AXLE_SOURCE_STATUS_STALE

    coordinator._axle_cache["last_success_utc"] = now - timedelta(minutes=40)
    assert (
        coordinator._axle_evaluate_source_status(now_utc=now)
        == const.AXLE_SOURCE_STATUS_UNAVAILABLE
    )
