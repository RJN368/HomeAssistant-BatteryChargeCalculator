"""Unit tests for Octopus Agile tariff agreement selection and refresh behavior."""

from datetime import datetime, timedelta, UTC
from unittest.mock import AsyncMock

import pytest

from custom_components.battery_charge_calculator.octopus_agile import (
    OctopusAgileRatesClient,
    _active_agreement_at,
)


def _agreement(
    *,
    tariff_code: str,
    valid_from: datetime,
    valid_to: datetime | None,
) -> dict:
    return {
        "tariff_code": tariff_code,
        "valid_from": valid_from.isoformat(),
        "valid_to": valid_to.isoformat() if valid_to else None,
    }


def test_active_agreement_returns_open_ended_agreement() -> None:
    """Agreement with valid_to=null is returned as the current (open-ended) tariff."""
    now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
    past = _agreement(
        tariff_code="E-1R-OLD-IMPORT-A",
        valid_from=now - timedelta(days=60),
        valid_to=now - timedelta(days=30),  # ended last month
    )
    current = _agreement(
        tariff_code="E-1R-CURRENT-IMPORT-A",
        valid_from=now - timedelta(days=30),
        valid_to=None,  # open-ended — the active agreement
    )

    selected = _active_agreement_at([past, current], now)
    assert selected is not None
    assert selected["tariff_code"] == "E-1R-CURRENT-IMPORT-A"


def test_active_agreement_returns_first_match_when_date_range_contains_now() -> None:
    """Agreement whose valid_from..valid_to range contains now is returned."""
    now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
    past = _agreement(
        tariff_code="E-1R-OLD-IMPORT-A",
        valid_from=now - timedelta(days=60),
        valid_to=now - timedelta(days=30),
    )
    current = _agreement(
        tariff_code="E-1R-CURRENT-IMPORT-A",
        valid_from=now - timedelta(days=30),
        valid_to=now + timedelta(days=30),
    )
    future = _agreement(
        tariff_code="E-1R-FUTURE-IMPORT-A",
        valid_from=now + timedelta(days=30),
        valid_to=None,
    )

    selected = _active_agreement_at([past, current, future], now)
    assert selected is not None
    assert selected["tariff_code"] == "E-1R-CURRENT-IMPORT-A"


def test_active_agreement_returns_none_when_no_match() -> None:
    """Returns None when no agreement's range contains now."""
    now = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
    past = _agreement(
        tariff_code="E-1R-OLD-IMPORT-A",
        valid_from=now - timedelta(days=60),
        valid_to=now - timedelta(days=30),
    )

    selected = _active_agreement_at([past], now)
    assert selected is None


@pytest.mark.asyncio
async def test_find_current_tariffs_refreshes_after_boundary() -> None:
    """Tariff codes are re-resolved once the active agreement boundary passes."""
    t0 = datetime(2026, 4, 21, 10, 0, tzinfo=UTC)
    boundary = t0 + timedelta(minutes=15)

    initial_meters = [
        {
            "is_export": False,
            "agreements": [
                _agreement(
                    tariff_code="E-1R-OLD-IMPORT-A",
                    valid_from=t0 - timedelta(days=5),
                    valid_to=boundary,
                )
            ],
        }
    ]
    updated_meters = [
        {
            "is_export": False,
            "agreements": [
                _agreement(
                    tariff_code="E-1R-NEW-IMPORT-A",
                    valid_from=boundary,
                    valid_to=None,
                )
            ],
        }
    ]

    client = OctopusAgileRatesClient(
        api_key="key",
        account_number="acct",
        tariff_cache_ttl=timedelta(hours=6),
    )
    meters_mock = AsyncMock(side_effect=[initial_meters, updated_meters])
    setattr(client, "_get_electricity_meters", meters_mock)

    await client.refresh_current_tariffs(AsyncMock(), now=t0)
    assert client.import_tariff_code == "E-1R-OLD-IMPORT-A"

    # Before boundary: cached tariff is still valid.
    await client.refresh_current_tariffs(AsyncMock(), now=t0 + timedelta(minutes=5))
    assert client.import_tariff_code == "E-1R-OLD-IMPORT-A"
    assert meters_mock.await_count == 1

    # After boundary: cache invalidates and tariffs are re-resolved.
    await client.refresh_current_tariffs(
        AsyncMock(), now=boundary + timedelta(minutes=1)
    )
    assert client.import_tariff_code == "E-1R-NEW-IMPORT-A"
    assert meters_mock.await_count == 2
