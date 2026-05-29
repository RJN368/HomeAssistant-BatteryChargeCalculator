"""Unit tests for BatteryChargeCoordinator Axel dispatch awareness."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.battery_charge_calculator import const
from custom_components.battery_charge_calculator.axel_client import AxelClientError
from custom_components.battery_charge_calculator.axel_windows import AxelWindow
from custom_components.battery_charge_calculator.coordinators import (
    BatteryChargeCoordinator,
)
from custom_components.battery_charge_calculator.genetic_evaluator import Timeslot


def _make_timeslot(dt: datetime, charge_option: str = "charge") -> Timeslot:
    slot = Timeslot(
        dt,
        import_price=0.25,
        export_price=0.15,
        demand_in=0.3,
        solar_in=0.0,
    )
    slot.charge_option = charge_option
    return slot


def _make_coordinator(
    *,
    simulate: bool,
    fail_safe_mode: str,
    stub_refresh_source_state: bool = True,
) -> BatteryChargeCoordinator:
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.options = {
        const.OCTOPUS_APIKEY: "test-key",
        const.OCTOPUS_ACCOUNT_NUMBER: "A-1111",
        const.GIVENERGY_SERIAL_NUMBER: "SN001",
        const.GIVENERGY_API_TOKEN: "token",
        const.SIMULATE_ONLY: simulate,
        const.AXEL_ENABLED: True,
        const.AXEL_API_TOKEN: "axel-token",
        const.AXEL_FAIL_SAFE_MODE: fail_safe_mode,
        const.AXEL_POLL_INTERVAL_SECONDS: 60,
        const.AXEL_NEUTRALIZE_ON_ACTIVE_ENTRY: True,
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
    coordinator.givenergy.enableCharge = AsyncMock()
    coordinator.givenergy.enableExport = AsyncMock()
    coordinator.givenergy.disableCharge = AsyncMock()
    coordinator.givenergy.disableExport = AsyncMock()
    coordinator.octopus_state_change_listener = AsyncMock()
    if stub_refresh_source_state:
        coordinator._axel_refresh_source_state = AsyncMock()
    coordinator.tz = timezone.utc
    return coordinator


def _set_axel_cache(
    coordinator: BatteryChargeCoordinator,
    *,
    now_utc: datetime,
    age_seconds: int | None,
    windows: list[AxelWindow],
) -> None:
    coordinator._axel_cache["windows"] = windows
    coordinator._axel_cache["last_success_utc"] = (
        now_utc - timedelta(seconds=age_seconds) if age_seconds is not None else None
    )


class TestCoordinatorAxelAwareness:
    @pytest.mark.asyncio
    async def test_active_suppresses_enable_dispatch(self):
        coordinator = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXEL_FAIL_SAFE_MODE_OPEN,
        )
        coordinator.config_entry.options[const.AXEL_NEUTRALIZE_ON_ACTIVE_ENTRY] = False

        now_utc = datetime.now(timezone.utc)
        coordinator.timeslots = [_make_timeslot(now_utc - timedelta(minutes=5), "charge")]
        _set_axel_cache(
            coordinator,
            now_utc=now_utc,
            age_seconds=5,
            windows=[
                AxelWindow(
                    start=now_utc - timedelta(minutes=10),
                    end=now_utc + timedelta(minutes=10),
                )
            ],
        )

        await coordinator._async_update_data()

        coordinator.givenergy.enableCharge.assert_not_called()
        coordinator.givenergy.enableExport.assert_not_called()
        coordinator.givenergy.disableCharge.assert_not_called()
        coordinator.givenergy.disableExport.assert_not_called()

    @pytest.mark.asyncio
    async def test_neutralize_on_entry_only_once_per_window(self):
        coordinator = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXEL_FAIL_SAFE_MODE_OPEN,
        )

        now_utc = datetime.now(timezone.utc)
        window = AxelWindow(
            start=now_utc - timedelta(minutes=10),
            end=now_utc + timedelta(minutes=10),
        )
        coordinator.timeslots = [_make_timeslot(now_utc - timedelta(minutes=5), "charge")]
        _set_axel_cache(
            coordinator,
            now_utc=now_utc,
            age_seconds=5,
            windows=[window],
        )

        await coordinator._async_update_data()
        await coordinator._async_update_data()

        coordinator.givenergy.disableCharge.assert_called_once_with(coordinator.hass)
        coordinator.givenergy.disableExport.assert_called_once_with(coordinator.hass)
        coordinator.givenergy.enableCharge.assert_not_called()
        coordinator.givenergy.enableExport.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_with_overlap_still_suppresses_dispatch(self):
        coordinator = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXEL_FAIL_SAFE_MODE_OPEN,
        )
        coordinator.config_entry.options[const.AXEL_NEUTRALIZE_ON_ACTIVE_ENTRY] = False

        now_utc = datetime.now(timezone.utc)
        coordinator.timeslots = [_make_timeslot(now_utc - timedelta(minutes=5), "charge")]
        _set_axel_cache(
            coordinator,
            now_utc=now_utc,
            age_seconds=20 * 60,
            windows=[
                AxelWindow(
                    start=now_utc - timedelta(minutes=2),
                    end=now_utc + timedelta(minutes=2),
                )
            ],
        )

        await coordinator._async_update_data()

        coordinator.givenergy.enableCharge.assert_not_called()
        assert coordinator._axel_cache["source_status"] == const.AXEL_SOURCE_STATUS_STALE
        assert (
            coordinator._axel_cache["suppression_reason"]
            == const.AXEL_SUPPRESSION_REASON_ACTIVE_WINDOW
        )
        assert coordinator._axel_cache["is_active"] is True

    @pytest.mark.asyncio
    async def test_unavailable_fail_safe_modes_open_vs_closed(self):
        now_utc = datetime.now(timezone.utc)

        coordinator_open = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXEL_FAIL_SAFE_MODE_OPEN,
        )
        coordinator_open.config_entry.options[const.AXEL_NEUTRALIZE_ON_ACTIVE_ENTRY] = False
        coordinator_open.timeslots = [
            _make_timeslot(now_utc - timedelta(minutes=5), "charge")
        ]
        _set_axel_cache(
            coordinator_open,
            now_utc=now_utc,
            age_seconds=None,
            windows=[],
        )

        await coordinator_open._async_update_data()

        coordinator_open.givenergy.enableCharge.assert_called_once_with(coordinator_open.hass)
        assert (
            coordinator_open._axel_cache["source_status"]
            == const.AXEL_SOURCE_STATUS_UNAVAILABLE
        )
        assert coordinator_open._axel_cache["suppression_reason"] is None
        assert coordinator_open._axel_cache["is_active"] is False

        coordinator_closed = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXEL_FAIL_SAFE_MODE_CLOSED,
        )
        coordinator_closed.config_entry.options[const.AXEL_NEUTRALIZE_ON_ACTIVE_ENTRY] = False
        coordinator_closed.timeslots = [
            _make_timeslot(now_utc - timedelta(minutes=5), "charge")
        ]
        _set_axel_cache(
            coordinator_closed,
            now_utc=now_utc,
            age_seconds=None,
            windows=[],
        )

        await coordinator_closed._async_update_data()

        coordinator_closed.givenergy.enableCharge.assert_not_called()
        assert (
            coordinator_closed._axel_cache["source_status"]
            == const.AXEL_SOURCE_STATUS_UNAVAILABLE
        )
        assert (
            coordinator_closed._axel_cache["suppression_reason"]
            == const.AXEL_SUPPRESSION_REASON_SOURCE_UNAVAILABLE_CLOSED
        )
        assert coordinator_closed._axel_cache["is_active"] is True

    @pytest.mark.asyncio
    async def test_axel_refresh_error_is_redacted_in_cache_and_logs(self, caplog):
        coordinator = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXEL_FAIL_SAFE_MODE_OPEN,
            stub_refresh_source_state=False,
        )

        with patch(
            "custom_components.battery_charge_calculator.coordinators.AxelClient.async_fetch_event",
            side_effect=AxelClientError(
                "Bearer axel-token request failed for axel-token"
            ),
        ):
            with caplog.at_level(logging.WARNING):
                await coordinator._axel_refresh_source_state(now_utc=datetime.now(timezone.utc))

        assert "axel-token" not in coordinator._axel_cache["last_error"]
        assert "***REDACTED***" in coordinator._axel_cache["last_error"]
        assert "axel-token" not in caplog.text

    @pytest.mark.asyncio
    async def test_active_to_inactive_triggers_immediate_resume_path(self):
        coordinator = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXEL_FAIL_SAFE_MODE_OPEN,
        )
        coordinator.config_entry.options[const.AXEL_NEUTRALIZE_ON_ACTIVE_ENTRY] = False

        now_utc = datetime.now(timezone.utc)
        coordinator.timeslots = [_make_timeslot(now_utc - timedelta(minutes=5), "charge")]

        _set_axel_cache(
            coordinator,
            now_utc=now_utc,
            age_seconds=5,
            windows=[
                AxelWindow(
                    start=now_utc - timedelta(minutes=10),
                    end=now_utc + timedelta(minutes=10),
                )
            ],
        )
        await coordinator._async_update_data()

        coordinator._axel_cache["windows"] = []
        coordinator._axel_cache["last_success_utc"] = datetime.now(timezone.utc)

        await coordinator._async_update_data()

        coordinator.octopus_state_change_listener.assert_called_once_with(
            None,
            reason=const.REPLAN_REASON_AXEL_WINDOW_ENDED,
        )
        coordinator.givenergy.enableCharge.assert_called_once_with(coordinator.hass)

    @pytest.mark.asyncio
    async def test_simulate_only_blocks_neutralize_and_resume_dispatch(self):
        coordinator = _make_coordinator(
            simulate=True,
            fail_safe_mode=const.AXEL_FAIL_SAFE_MODE_OPEN,
        )

        now_utc = datetime.now(timezone.utc)
        coordinator.timeslots = [_make_timeslot(now_utc - timedelta(minutes=5), "charge")]

        _set_axel_cache(
            coordinator,
            now_utc=now_utc,
            age_seconds=5,
            windows=[
                AxelWindow(
                    start=now_utc - timedelta(minutes=10),
                    end=now_utc + timedelta(minutes=10),
                )
            ],
        )
        await coordinator._async_update_data()

        coordinator._axel_cache["windows"] = []
        coordinator._axel_cache["last_success_utc"] = datetime.now(timezone.utc)

        await coordinator._async_update_data()

        coordinator.givenergy.enableCharge.assert_not_called()
        coordinator.givenergy.enableExport.assert_not_called()
        coordinator.givenergy.disableCharge.assert_not_called()
        coordinator.givenergy.disableExport.assert_not_called()
