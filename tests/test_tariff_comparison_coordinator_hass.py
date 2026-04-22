"""
Integration tests for TariffComparisonCoordinator using Home Assistant test harness.
Covers forced refresh and background fetch logic.
"""

import asyncio
from datetime import datetime, timezone
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, ConfigEntryState

from custom_components.battery_charge_calculator.tariff_comparison import (
    TariffComparisonCoordinator,
)
from custom_components.battery_charge_calculator.tariff_comparison.client import (
    TariffComparisonClient,
)


@pytest.fixture
async def config_entry(hass: HomeAssistant):
    entry = ConfigEntry(
        version=1,
        minor_version=0,
        domain="battery_charge_calculator",
        title="Test BCC",
        data={},
        options={
            "tariff_comparison_tariffs": '[{"import_tariff_code": "T1", "is_current": true}]',
            "tariff_comparison_cache_max_age_days": 7,
        },
        entry_id="test_entry",
        source="user",
        state=ConfigEntryState.NOT_LOADED,
        unique_id="test_unique_id",
        discovery_keys=MappingProxyType({}),
        subentries_data=(),
        created_at=datetime.now(),
        modified_at=datetime.now(),
    )
    await hass.config_entries.async_add(entry)
    return entry


@pytest.mark.asyncio
async def test_force_refresh_triggers_background_fetch(
    hass: HomeAssistant, config_entry
):
    """
    Test that forcing a refresh always triggers a background fetch and does not use cache.
    """
    fake_cache = {
        "data_year": "2026-03",
        "generated_at": "2026-04-01T00:00:00+00:00",
        "schema_version": 1,
    }
    with (
        patch(
            "custom_components.battery_charge_calculator.tariff_comparison.cache.read_cache",
            return_value=fake_cache,
        ),
        patch(
            "custom_components.battery_charge_calculator.tariff_comparison.cache.cache_data_year",
            return_value="2026-03",
        ),
        patch(
            "custom_components.battery_charge_calculator.tariff_comparison.cache.is_cache_fresh",
            return_value=True,
        ),
        patch.object(
            TariffComparisonCoordinator,
            "_build_result_from_cache",
            return_value={"tariffs": ["dummy"]},
        ),
        patch.object(
            TariffComparisonCoordinator,
            "_background_fetch_and_calculate",
            new_callable=AsyncMock,
        ) as mock_bg,
    ):
        coordinator = TariffComparisonCoordinator(hass, config_entry)
        await coordinator._async_update_data()
        assert mock_bg.called, (
            "Background fetch was not triggered when force_refresh is True"
        )


@pytest.mark.asyncio
async def test_no_duplicate_background_fetch(hass: HomeAssistant, config_entry):
    """
    Test that a background fetch is not scheduled if one is already running.
    """
    with (
        patch(
            "custom_components.battery_charge_calculator.tariff_comparison.cache.read_cache",
            return_value=None,
        ),
        patch.object(
            TariffComparisonCoordinator,
            "_build_result_from_cache",
            return_value={"tariffs": ["dummy"]},
        ),
        patch.object(
            TariffComparisonCoordinator,
            "_background_fetch_and_calculate",
            new_callable=AsyncMock,
        ) as mock_bg,
    ):
        coordinator = TariffComparisonCoordinator(hass, config_entry)
        coordinator._fetch_task = asyncio.Future()  # Simulate running fetch
        await coordinator._async_update_data()
        assert not mock_bg.called, (
            "Background fetch should not be triggered if already running"
        )


@pytest.mark.asyncio
async def test_fetch_and_calculate_adjusts_period_from_to_current_tariff_start(
    hass: HomeAssistant, config_entry
):
    """When current tariff starts inside the window, consumption fetch starts at that date."""
    coordinator = TariffComparisonCoordinator(hass, config_entry)
    session = AsyncMock()

    period_from = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    period_to = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
    adjusted_from = datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc)

    tariffs = [{"import_tariff_code": "T1", "is_current": True, "name": "Current"}]

    with (
        patch.object(
            TariffComparisonClient,
            "fetch_import_tariff_start_date",
            new=AsyncMock(return_value=adjusted_from),
        ),
        patch.object(
            TariffComparisonClient,
            "fetch_consumption",
            new=AsyncMock(return_value=[]),
        ) as mock_fetch_consumption,
        patch.object(
            TariffComparisonClient,
            "fetch_unit_rates",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            TariffComparisonClient,
            "fetch_standing_charges",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            TariffComparisonCoordinator,
            "_calculate_all",
            return_value={
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "data_period": {"from": "2026-03-17", "to": "2026-04-01"},
                "coverage_warning": False,
                "tariffs": [],
                "export_configured": False,
                "export_meter_serial_missing": True,
            },
        ),
    ):
        await coordinator._fetch_and_calculate(
            session=session,
            tariff_configs=tariffs,
            period_from=period_from,
            period_to=period_to,
            data_year="2026-03",
            existing_cache=None,
        )

    # fetch_consumption(session, effective_period_from, period_to, export=False)
    called_period_from = mock_fetch_consumption.await_args_list[0].args[1]
    assert called_period_from == adjusted_from
