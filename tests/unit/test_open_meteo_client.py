"""Unit tests for tariff_comparison.open_meteo_historical.OpenMeteoHistoricalClient.

Covers: happy path with 3 days of hourly temps, HTTP error, date range
parsing (keys are datetime.date objects), and single-day (period_from == period_to).
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.battery_charge_calculator.tariff_comparison.open_meteo_historical import (
    OpenMeteoHistoricalClient,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(lat: float = 52.0, lon: float = -0.2) -> OpenMeteoHistoricalClient:
    return OpenMeteoHistoricalClient(lat=lat, lon=lon)


def _build_open_meteo_response(start_date: datetime.date, num_days: int) -> dict:
    """Build a mock Open-Meteo API response for *num_days* days starting from *start_date*.

    Each day has 24 hourly temperature values.
    """
    times = []
    temperatures = []
    for day_offset in range(num_days):
        d = start_date + datetime.timedelta(days=day_offset)
        for hour in range(24):
            times.append(f"{d.isoformat()}T{hour:02d}:00")
            temperatures.append(float(10 + hour % 5))  # deterministic values

    return {
        "hourly": {
            "time": times,
            "temperature_2m": temperatures,
        }
    }


def _mock_session_get(data: dict, status: int = 200) -> AsyncMock:
    """Build a mock aiohttp.ClientSession whose single .get() yields *data*."""
    response = AsyncMock()
    response.status = status
    response.json = AsyncMock(return_value=data)
    if status >= 400:
        response.raise_for_status = MagicMock(
            side_effect=Exception(f"HTTP error {status}")
        )
    else:
        response.raise_for_status = MagicMock()

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=False)

    session = AsyncMock()
    session.get = MagicMock(return_value=cm)
    return session


# ---------------------------------------------------------------------------
# Tests: happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    async def test_three_days_returns_dict_with_three_date_keys(self):
        """3 days of hourly temps → dict with 3 date keys, each list of 24 floats."""
        start = datetime.date(2025, 6, 1)
        end = datetime.date(2025, 6, 3)
        mock_data = _build_open_meteo_response(start, 3)
        session = _mock_session_get(mock_data)
        client = _make_client()

        result = await client.fetch(session, period_from=start, period_to=end)

        assert len(result) == 3

    async def test_each_date_key_maps_to_24_floats(self):
        """Each date key in the result must map to a list of exactly 24 temperature floats."""
        start = datetime.date(2025, 6, 1)
        end = datetime.date(2025, 6, 3)
        mock_data = _build_open_meteo_response(start, 3)
        session = _mock_session_get(mock_data)
        client = _make_client()

        result = await client.fetch(session, period_from=start, period_to=end)

        for date_key, temps in result.items():
            assert len(temps) == 24, (
                f"Expected 24 temps for {date_key}, got {len(temps)}"
            )
            assert all(isinstance(t, float) for t in temps), (
                f"Non-float temperature in {date_key}: {temps}"
            )

    async def test_temperatures_match_mock_data(self):
        """Temperature values in result match the mocked API response."""
        start = datetime.date(2025, 6, 1)
        end = datetime.date(2025, 6, 1)
        mock_data = _build_open_meteo_response(start, 1)
        session = _mock_session_get(mock_data)
        client = _make_client()

        result = await client.fetch(session, period_from=start, period_to=end)

        assert start in result
        expected_temps = [float(10 + h % 5) for h in range(24)]
        assert result[start] == expected_temps


# ---------------------------------------------------------------------------
# Tests: HTTP error
# ---------------------------------------------------------------------------


class TestHttpError:
    async def test_http_500_raises_exception(self):
        """500 response from Open-Meteo → raises an exception."""
        start = datetime.date(2025, 6, 1)
        end = datetime.date(2025, 6, 3)
        session = _mock_session_get({}, status=500)
        client = _make_client()

        with pytest.raises(Exception):
            await client.fetch(session, period_from=start, period_to=end)

    async def test_http_404_raises_exception(self):
        """404 response from Open-Meteo → raises an exception."""
        start = datetime.date(2025, 6, 1)
        end = datetime.date(2025, 6, 3)
        session = _mock_session_get({}, status=404)
        client = _make_client()

        with pytest.raises(Exception):
            await client.fetch(session, period_from=start, period_to=end)


# ---------------------------------------------------------------------------
# Tests: date range parsing
# ---------------------------------------------------------------------------


class TestDateRangeParsing:
    async def test_dict_keys_are_date_objects(self):
        """Returned dict keys must be datetime.date objects, not strings."""
        start = datetime.date(2025, 6, 1)
        end = datetime.date(2025, 6, 2)
        mock_data = _build_open_meteo_response(start, 2)
        session = _mock_session_get(mock_data)
        client = _make_client()

        result = await client.fetch(session, period_from=start, period_to=end)

        for key in result:
            assert isinstance(key, datetime.date), (
                f"Expected datetime.date key, got {type(key)!r}: {key!r}"
            )
            # Ensure it is not a datetime (which is a subclass of date)
            assert type(key) is datetime.date, f"Key is datetime, not date: {key!r}"

    async def test_correct_date_range_coverage(self):
        """Result dict covers exactly the requested date range."""
        start = datetime.date(2025, 6, 1)
        end = datetime.date(2025, 6, 3)
        mock_data = _build_open_meteo_response(start, 3)
        session = _mock_session_get(mock_data)
        client = _make_client()

        result = await client.fetch(session, period_from=start, period_to=end)

        expected_dates = {
            datetime.date(2025, 6, 1),
            datetime.date(2025, 6, 2),
            datetime.date(2025, 6, 3),
        }
        assert set(result.keys()) == expected_dates


# ---------------------------------------------------------------------------
# Tests: single day
# ---------------------------------------------------------------------------


class TestSingleDay:
    async def test_single_day_period_from_equals_period_to(self):
        """period_from == period_to → result dict with exactly 1 key."""
        day = datetime.date(2025, 6, 15)
        mock_data = _build_open_meteo_response(day, 1)
        session = _mock_session_get(mock_data)
        client = _make_client()

        result = await client.fetch(session, period_from=day, period_to=day)

        assert len(result) == 1
        assert day in result
        assert len(result[day]) == 24

    async def test_api_called_with_lat_lon_params(self):
        """Client must pass lat/lon to the API URL."""
        day = datetime.date(2025, 6, 15)
        mock_data = _build_open_meteo_response(day, 1)
        session = _mock_session_get(mock_data)
        client = _make_client(lat=51.5, lon=-0.1)

        await client.fetch(session, period_from=day, period_to=day)

        call_args = session.get.call_args
        # URL or params must contain latitude/longitude
        assert call_args is not None
        url_or_params = str(call_args)
        assert "51.5" in url_or_params or "latitude" in url_or_params.lower()
