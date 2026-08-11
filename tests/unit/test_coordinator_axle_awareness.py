"""Unit tests for BatteryChargeCoordinator Axle dispatch awareness."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.battery_charge_calculator import const
from custom_components.battery_charge_calculator.planning.providers.axle.axle_client_error import (
    AxleClientError,
)
from custom_components.battery_charge_calculator.axle_windows import AxleWindow
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
        const.AXLE_ENABLED: True,
        const.AXLE_API_TOKEN: "axle-token",
        const.AXLE_FAIL_SAFE_MODE: fail_safe_mode,
        const.AXLE_POLL_INTERVAL_SECONDS: 60,
        const.AXLE_NEUTRALIZE_ON_ACTIVE_ENTRY: True,
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
        coordinator.axle_state.async_refresh_source_state = AsyncMock()
    coordinator.tz = timezone.utc
    return coordinator


def _set_axle_cache(
    coordinator: BatteryChargeCoordinator,
    *,
    now_utc: datetime,
    age_seconds: int | None,
    windows: list[AxleWindow],
) -> None:
    coordinator.axle_state.cache["windows"] = windows
    coordinator.axle_state.cache["last_success_utc"] = (
        now_utc - timedelta(seconds=age_seconds) if age_seconds is not None else None
    )


class TestCoordinatorAxleAwareness:
    @pytest.mark.asyncio
    async def test_active_window_does_not_block_enable_dispatch(self):
        coordinator = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXLE_FAIL_SAFE_MODE_OPEN,
        )

        now_utc = datetime.now(timezone.utc)
        coordinator.timeslots = [_make_timeslot(now_utc - timedelta(minutes=5), "charge")]
        _set_axle_cache(
            coordinator,
            now_utc=now_utc,
            age_seconds=5,
            windows=[
                AxleWindow(
                    start=now_utc - timedelta(minutes=10),
                    end=now_utc + timedelta(minutes=10),
                )
            ],
        )

        await coordinator._async_update_data()

        coordinator.givenergy.enableCharge.assert_called_once_with(coordinator.hass)
        coordinator.givenergy.enableExport.assert_not_called()
        coordinator.givenergy.disableCharge.assert_not_called()
        coordinator.givenergy.disableExport.assert_not_called()
        assert coordinator.axle_state.cache["is_active"] is True

    @pytest.mark.asyncio
    async def test_window_change_triggers_immediate_replan_reason(self):
        coordinator = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXLE_FAIL_SAFE_MODE_OPEN,
        )

        now_utc = datetime.now(timezone.utc)
        window = AxleWindow(
            start=now_utc - timedelta(minutes=10),
            end=now_utc + timedelta(minutes=10),
        )
        coordinator.timeslots = [_make_timeslot(now_utc - timedelta(minutes=5), "charge")]
        _set_axle_cache(
            coordinator,
            now_utc=now_utc,
            age_seconds=5,
            windows=[window],
        )
        coordinator.axle_state.set_windows([window])
        coordinator.axle_state.cache["windows_changed"] = True

        await coordinator._async_update_data()

        coordinator.octopus_state_change_listener.assert_called_once_with(
            None,
            reason=const.REPLAN_REASON_AXLE_WINDOWS_CHANGED,
        )
        assert coordinator.axle_state.cache["windows_changed"] is False

    @pytest.mark.asyncio
    async def test_stale_with_overlap_does_not_block_dispatch(self):
        coordinator = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXLE_FAIL_SAFE_MODE_OPEN,
        )

        now_utc = datetime.now(timezone.utc)
        coordinator.timeslots = [_make_timeslot(now_utc - timedelta(minutes=5), "charge")]
        _set_axle_cache(
            coordinator,
            now_utc=now_utc,
            age_seconds=20 * 60,
            windows=[
                AxleWindow(
                    start=now_utc - timedelta(minutes=2),
                    end=now_utc + timedelta(minutes=2),
                )
            ],
        )

        await coordinator._async_update_data()

        coordinator.givenergy.enableCharge.assert_called_once_with(coordinator.hass)
        assert coordinator.axle_state.cache["source_status"] == const.AXLE_SOURCE_STATUS_STALE
        assert (
            coordinator.axle_state.cache["suppression_reason"]
            == const.AXLE_SUPPRESSION_REASON_ACTIVE_WINDOW
        )
        assert coordinator.axle_state.cache["is_active"] is True

    @pytest.mark.asyncio
    async def test_unavailable_fail_safe_modes_open_vs_closed(self):
        now_utc = datetime.now(timezone.utc)

        coordinator_open = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXLE_FAIL_SAFE_MODE_OPEN,
        )
        coordinator_open.timeslots = [
            _make_timeslot(now_utc - timedelta(minutes=5), "charge")
        ]
        _set_axle_cache(
            coordinator_open,
            now_utc=now_utc,
            age_seconds=None,
            windows=[],
        )

        await coordinator_open._async_update_data()

        coordinator_open.givenergy.enableCharge.assert_called_once_with(coordinator_open.hass)
        assert (
            coordinator_open.axle_state.cache["source_status"]
            == const.AXLE_SOURCE_STATUS_UNAVAILABLE
        )
        assert coordinator_open.axle_state.cache["suppression_reason"] is None
        assert coordinator_open.axle_state.cache["is_active"] is False

        coordinator_closed = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXLE_FAIL_SAFE_MODE_CLOSED,
        )
        coordinator_closed.timeslots = [
            _make_timeslot(now_utc - timedelta(minutes=5), "charge")
        ]
        _set_axle_cache(
            coordinator_closed,
            now_utc=now_utc,
            age_seconds=None,
            windows=[],
        )

        await coordinator_closed._async_update_data()

        coordinator_closed.givenergy.enableCharge.assert_called_once_with(coordinator_closed.hass)
        assert (
            coordinator_closed.axle_state.cache["source_status"]
            == const.AXLE_SOURCE_STATUS_UNAVAILABLE
        )
        assert (
            coordinator_closed.axle_state.cache["suppression_reason"]
            == const.AXLE_SUPPRESSION_REASON_SOURCE_UNAVAILABLE_CLOSED
        )
        assert coordinator_closed.axle_state.cache["is_active"] is False

    @pytest.mark.asyncio
    async def test_axle_refresh_error_is_redacted_in_cache_and_logs(self, caplog):
        coordinator = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXLE_FAIL_SAFE_MODE_OPEN,
            stub_refresh_source_state=False,
        )

        with patch(
                "custom_components.battery_charge_calculator.planning.providers.axle.axle_state.AxleClient.async_fetch_event",
            side_effect=AxleClientError(
                "Bearer axle-token request failed for axle-token"
            ),
        ):
            with caplog.at_level(logging.WARNING):
                await coordinator.axle_state.async_refresh_source_state(
                    now_utc=datetime.now(timezone.utc)
                )

        assert "axle-token" not in coordinator.axle_state.cache["last_error"]
        assert "***REDACTED***" in coordinator.axle_state.cache["last_error"]
        assert "axle-token" not in caplog.text

    @pytest.mark.asyncio
    async def test_export_window_creates_positive_slot_adjustment(self):
        coordinator = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXLE_FAIL_SAFE_MODE_OPEN,
        )

        now_utc = datetime.now(timezone.utc)
        coordinator.axle_state.cache["windows"] = [
            AxleWindow(
                start=now_utc,
                end=now_utc + timedelta(minutes=15),
                control_intent="ExPoRt",
            )
        ]

        adjustment = coordinator.axle_slot_export_adjustment_kwh(
            slot_start=now_utc,
            slot_end=now_utc + timedelta(minutes=30),
            inverter_size_kw=4.0,
        )

        assert adjustment == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_non_export_intent_produces_zero_adjustment(self):
        coordinator = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXLE_FAIL_SAFE_MODE_OPEN,
        )

        now_utc = datetime.now(timezone.utc)
        coordinator.axle_state.cache["windows"] = [
            AxleWindow(
                start=now_utc,
                end=now_utc + timedelta(minutes=30),
                control_intent="charge",
            )
        ]

        adjustment = coordinator.axle_slot_export_adjustment_kwh(
            slot_start=now_utc,
            slot_end=now_utc + timedelta(minutes=30),
            inverter_size_kw=3.6,
        )

        assert adjustment == 0.0

    @pytest.mark.asyncio
    async def test_forced_action_marks_export_on_overlap(self):
        coordinator = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXLE_FAIL_SAFE_MODE_OPEN,
        )

        now_utc = datetime.now(timezone.utc)
        coordinator.axle_state.cache["windows"] = [
            AxleWindow(
                start=now_utc,
                end=now_utc + timedelta(minutes=10),
                control_intent="export",
            )
        ]

        forced_action = coordinator.axle_slot_forced_action(
            slot_start=now_utc,
            slot_end=now_utc + timedelta(minutes=30),
        )

        assert forced_action == "export"

    @pytest.mark.asyncio
    async def test_forced_action_is_none_without_export_overlap(self):
        coordinator = _make_coordinator(
            simulate=False,
            fail_safe_mode=const.AXLE_FAIL_SAFE_MODE_OPEN,
        )

        now_utc = datetime.now(timezone.utc)
        coordinator.axle_state.cache["windows"] = [
            AxleWindow(
                start=now_utc,
                end=now_utc + timedelta(minutes=30),
                control_intent="charge",
            )
        ]

        forced_action = coordinator.axle_slot_forced_action(
            slot_start=now_utc,
            slot_end=now_utc + timedelta(minutes=30),
        )

        assert forced_action is None

    @pytest.mark.asyncio
    async def test_simulate_only_blocks_neutralize_and_resume_dispatch(self):
        coordinator = _make_coordinator(
            simulate=True,
            fail_safe_mode=const.AXLE_FAIL_SAFE_MODE_OPEN,
        )

        now_utc = datetime.now(timezone.utc)
        coordinator.timeslots = [_make_timeslot(now_utc - timedelta(minutes=5), "charge")]

        _set_axle_cache(
            coordinator,
            now_utc=now_utc,
            age_seconds=5,
            windows=[
                AxleWindow(
                    start=now_utc - timedelta(minutes=10),
                    end=now_utc + timedelta(minutes=10),
                )
            ],
        )
        await coordinator._async_update_data()

        coordinator.axle_state.cache["windows"] = []
        coordinator.axle_state.cache["last_success_utc"] = datetime.now(timezone.utc)

        await coordinator._async_update_data()

        coordinator.givenergy.enableCharge.assert_not_called()
        coordinator.givenergy.enableExport.assert_not_called()
        coordinator.givenergy.disableCharge.assert_not_called()
        coordinator.givenergy.disableExport.assert_not_called()
