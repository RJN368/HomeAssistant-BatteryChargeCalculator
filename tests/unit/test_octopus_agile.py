"""Unit tests for Octopus Agile tariff agreement selection and refresh behavior."""

from datetime import datetime, timedelta, UTC
from unittest.mock import AsyncMock, MagicMock

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


# ---------------------------------------------------------------------------
# async_fetch_today_consumption
# ---------------------------------------------------------------------------


def _make_consumption_client() -> OctopusAgileRatesClient:
    return OctopusAgileRatesClient(api_key="key", account_number="acct")


def _mock_session_with_consumption(results: list[dict]) -> AsyncMock:
    """Return a mock aiohttp session whose GET returns the given consumption results."""
    resp = AsyncMock()
    resp.raise_for_status = MagicMock()
    resp.json = AsyncMock(return_value={"results": results})
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    session = AsyncMock()
    session.get = MagicMock(return_value=resp)
    return session


@pytest.mark.asyncio
async def test_fetch_today_consumption_returns_empty_when_mpan_missing() -> None:
    """Returns empty list immediately when MPAN is blank."""
    client = _make_consumption_client()
    session = AsyncMock()
    result = await client.async_fetch_today_consumption(session, "", "SN001")
    assert result == []
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_today_consumption_returns_empty_when_serial_missing() -> None:
    """Returns empty list immediately when meter serial is blank."""
    client = _make_consumption_client()
    session = AsyncMock()
    result = await client.async_fetch_today_consumption(session, "1200012345678", "")
    assert result == []
    session.get.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_today_consumption_parses_results() -> None:
    """Parses interval_start, interval_end, and consumption_kwh from API response."""
    client = _make_consumption_client()
    api_results = [
        {
            "interval_start": "2026-05-31T00:00:00Z",
            "interval_end": "2026-05-31T00:30:00Z",
            "consumption": 0.312,
        },
        {
            "interval_start": "2026-05-31T00:30:00Z",
            "interval_end": "2026-05-31T01:00:00Z",
            "consumption": 0.278,
        },
    ]
    session = _mock_session_with_consumption(api_results)
    result = await client.async_fetch_today_consumption(session, "1200012345678", "SN001")

    assert len(result) == 2
    assert result[0]["consumption_kwh"] == pytest.approx(0.312)
    assert result[1]["consumption_kwh"] == pytest.approx(0.278)
    # All datetimes must be UTC-aware
    for entry in result:
        assert entry["interval_start"].tzinfo is not None
        assert entry["interval_end"].tzinfo is not None


@pytest.mark.asyncio
async def test_fetch_today_consumption_returns_empty_on_http_error() -> None:
    """Returns empty list and does not raise when the API call fails."""
    client = _make_consumption_client()
    resp = AsyncMock()
    resp.raise_for_status = MagicMock(side_effect=Exception("503 Service Unavailable"))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    session = AsyncMock()
    session.get = MagicMock(return_value=resp)

    result = await client.async_fetch_today_consumption(session, "1200012345678", "SN001")
    assert result == []
