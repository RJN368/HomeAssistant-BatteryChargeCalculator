"""Unit tests for tariff_comparison.client.TariffComparisonClient and
_build_historical_rate_map.

Uses AsyncMock to stub aiohttp.ClientSession — no real HTTP calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.battery_charge_calculator.tariff_comparison.client import (
    TariffComparisonClient,
    _build_historical_rate_map,
)

UTC = timezone.utc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PERIOD_FROM = datetime(2025, 4, 1, 0, 0, tzinfo=UTC)
PERIOD_TO = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)


def _make_client(
    api_key: str = "test-key",
    mpan: str = "1000000000001",
    meter_serial: str = "SN001",
    export_mpan: str | None = None,
    export_meter_serial: str | None = None,
) -> TariffComparisonClient:
    return TariffComparisonClient(
        api_key=api_key,
        mpan=mpan,
        meter_serial=meter_serial,
        export_mpan=export_mpan,
        export_meter_serial=export_meter_serial,
    )


def _mock_aiohttp_response(data: dict, status: int = 200) -> AsyncMock:
    """Build a mock aiohttp response that returns *data* as JSON."""
    response = AsyncMock()
    response.status = status
    response.json = AsyncMock(return_value=data)
    response.raise_for_status = MagicMock()
    if status >= 400:
        response.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return response


def _mock_session_get(
    responses: list[dict], statuses: list[int] | None = None
) -> AsyncMock:
    """Return a mock aiohttp.ClientSession whose .get() yields responses in order."""
    if statuses is None:
        statuses = [200] * len(responses)
    mock_responses = [_mock_aiohttp_response(r, s) for r, s in zip(responses, statuses)]
    session = AsyncMock()
    # Each call to session.get() returns an async context manager
    context_managers = []
    for resp in mock_responses:
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        context_managers.append(cm)
    session.get = MagicMock(side_effect=context_managers)
    return session


# ---------------------------------------------------------------------------
# Tests: fetch_consumption
# ---------------------------------------------------------------------------


class TestFetchConsumption:
    async def test_happy_path_pagination_followed(self):
        """fetch_consumption follows pagination and returns sorted merged list."""
        page1 = {
            "results": [
                {
                    "interval_start": "2025-04-01T00:00:00Z",
                    "interval_end": "2025-04-01T00:30:00Z",
                    "consumption": 0.3,
                },
                {
                    "interval_start": "2025-04-01T00:30:00Z",
                    "interval_end": "2025-04-01T01:00:00Z",
                    "consumption": 0.4,
                },
            ],
            "next": "https://api.octopus.energy/v1/...?page=2",
        }
        page2 = {
            "results": [
                {
                    "interval_start": "2025-04-01T01:00:00Z",
                    "interval_end": "2025-04-01T01:30:00Z",
                    "consumption": 0.5,
                },
            ],
            "next": None,
        }
        session = _mock_session_get([page1, page2])
        client = _make_client()

        slots = await client.fetch_consumption(session, PERIOD_FROM, PERIOD_TO)

        # Both pages fetched (session.get called twice)
        assert session.get.call_count == 2
        # All 3 slots returned
        assert len(slots) == 3
        # Sorted ascending by interval_start
        starts = [s["interval_start"] for s in slots]
        assert starts == sorted(starts)
        # Returned dicts have expected keys
        assert "interval_start" in slots[0]
        assert "consumption" in slots[0]

    async def test_export_true_without_export_mpan_raises_value_error(self):
        """fetch_consumption(export=True) with no export MPAN → ValueError."""
        session = AsyncMock()
        client = _make_client(export_mpan=None, export_meter_serial=None)

        with pytest.raises(ValueError):
            await client.fetch_consumption(session, PERIOD_FROM, PERIOD_TO, export=True)

    async def test_single_page_no_next(self):
        """Single-page response (next=None) returns results without second call."""
        page = {
            "results": [
                {
                    "interval_start": "2025-04-01T00:00:00Z",
                    "interval_end": "2025-04-01T00:30:00Z",
                    "consumption": 0.2,
                },
            ],
            "next": None,
        }
        session = _mock_session_get([page])
        client = _make_client()

        slots = await client.fetch_consumption(session, PERIOD_FROM, PERIOD_TO)

        assert session.get.call_count == 1
        assert len(slots) == 1


# ---------------------------------------------------------------------------
# Tests: fetch_unit_rates — rate-before-window seed (Hockney critical note)
# ---------------------------------------------------------------------------


class TestFetchUnitRates:
    async def test_seed_request_is_prepended_before_rate_map_build(self):
        """Rate-before-window seed: client issues a second request with period_to=period_from&page_size=1.

        The seed response is prepended to raw_rates so that _build_historical_rate_map
        has a value from which to forward-fill the full window.
        """
        TARIFF = "E-1R-AGILE-FLEX-22-11-25-B"

        # Main window: no rates (simulates a Fixed tariff started before window)
        main_response = {"results": [], "next": None}
        # Seed: the one rate in force just before the window
        seed_response = {
            "results": [
                {
                    "valid_from": "2024-01-01T00:00:00Z",
                    "valid_to": None,
                    "value_inc_vat": 28.50,
                }
            ],
            "next": None,
        }
        # Client makes: (1) main rates request, (2) seed request
        session = _mock_session_get([main_response, seed_response])
        client = _make_client()

        rates = await client.fetch_unit_rates(session, TARIFF, PERIOD_FROM, PERIOD_TO)

        # Session was called at least twice (main + seed)
        assert session.get.call_count >= 2
        # Result contains at least one rate entry
        assert len(rates) >= 1
        # The seeded rate should appear in results
        values = [r["value_inc_vat"] for r in rates]
        assert 28.50 in values

    async def test_fetch_unit_rates_returns_sorted_list(self):
        """fetch_unit_rates returns list sorted ascending by valid_from."""
        TARIFF = "E-1R-AGILE-FLEX-22-11-25-B"
        main_response = {
            "results": [
                {
                    "valid_from": "2025-06-01T00:00:00Z",
                    "valid_to": "2025-06-01T00:30:00Z",
                    "value_inc_vat": 30.0,
                },
                {
                    "valid_from": "2025-04-01T00:00:00Z",
                    "valid_to": "2025-04-01T00:30:00Z",
                    "value_inc_vat": 20.0,
                },
            ],
            "next": None,
        }
        seed_response = {"results": [], "next": None}
        session = _mock_session_get([main_response, seed_response])
        client = _make_client()

        rates = await client.fetch_unit_rates(session, TARIFF, PERIOD_FROM, PERIOD_TO)

        valid_froms = [r["valid_from"] for r in rates]
        assert valid_froms == sorted(valid_froms)


# ---------------------------------------------------------------------------
# Tests: _build_historical_rate_map
# ---------------------------------------------------------------------------


class TestBuildHistoricalRateMap:
    def test_fixed_tariff_single_rate_covers_whole_period(self):
        """Fixed tariff: single rate → all 30-min slots in a 1-day period get that rate."""
        period_from = datetime(2025, 6, 1, 0, 0, tzinfo=UTC)
        period_to = datetime(2025, 6, 2, 0, 0, tzinfo=UTC)  # 1 day = 48 slots
        raw_rates = [
            {
                "valid_from": datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
                "valid_to": None,
                "value_inc_vat": 28.50,
            }
        ]

        rate_map = _build_historical_rate_map(raw_rates, period_from, period_to)

        assert len(rate_map) == 48
        assert all(v == 28.50 for v in rate_map.values())

    def test_full_year_fixed_rate_has_17520_slots(self):
        """Single fixed rate over a 365-day window → 17 520 slots (365 × 48)."""
        raw_rates = [
            {
                "valid_from": datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
                "valid_to": None,
                "value_inc_vat": 28.50,
            }
        ]
        rate_map = _build_historical_rate_map(raw_rates, PERIOD_FROM, PERIOD_TO)

        assert len(rate_map) == 365 * 48  # 17 520

    def test_agile_three_rate_bands_correct_assignment(self):
        """Agile-style: 3 rate bands each covering multiple slots → correct assignment."""
        period_from = datetime(2025, 6, 1, 0, 0, tzinfo=UTC)
        period_to = datetime(2025, 6, 1, 3, 0, tzinfo=UTC)  # 6 slots

        # 3 bands of 2 slots each at different rates
        raw_rates = [
            {
                "valid_from": datetime(2025, 6, 1, 0, 0, tzinfo=UTC),
                "valid_to": datetime(2025, 6, 1, 1, 0, tzinfo=UTC),
                "value_inc_vat": 10.0,
            },
            {
                "valid_from": datetime(2025, 6, 1, 1, 0, tzinfo=UTC),
                "valid_to": datetime(2025, 6, 1, 2, 0, tzinfo=UTC),
                "value_inc_vat": 20.0,
            },
            {
                "valid_from": datetime(2025, 6, 1, 2, 0, tzinfo=UTC),
                "valid_to": datetime(2025, 6, 1, 3, 0, tzinfo=UTC),
                "value_inc_vat": 30.0,
            },
        ]

        rate_map = _build_historical_rate_map(raw_rates, period_from, period_to)

        assert len(rate_map) == 6
        assert rate_map[datetime(2025, 6, 1, 0, 0, tzinfo=UTC)] == 10.0
        assert rate_map[datetime(2025, 6, 1, 0, 30, tzinfo=UTC)] == 10.0
        assert rate_map[datetime(2025, 6, 1, 1, 0, tzinfo=UTC)] == 20.0
        assert rate_map[datetime(2025, 6, 1, 1, 30, tzinfo=UTC)] == 20.0
        assert rate_map[datetime(2025, 6, 1, 2, 0, tzinfo=UTC)] == 30.0
        assert rate_map[datetime(2025, 6, 1, 2, 30, tzinfo=UTC)] == 30.0

    def test_forward_fill_gap_uses_previous_rate_not_zero(self):
        """Hockney: gap in rates → forward-filled from previous rate, NOT 0.

        We provide coverage for 00:00–01:00, then a gap at 01:00–01:30,
        then coverage again at 01:30–02:00.  The gap slot must carry the
        rate from the preceding band (10.0), not zero.
        """
        period_from = datetime(2025, 6, 1, 0, 0, tzinfo=UTC)
        period_to = datetime(2025, 6, 1, 2, 0, tzinfo=UTC)  # 4 slots

        raw_rates = [
            # Covers 00:00–01:00 (2 slots)
            {
                "valid_from": datetime(2025, 6, 1, 0, 0, tzinfo=UTC),
                "valid_to": datetime(2025, 6, 1, 1, 0, tzinfo=UTC),
                "value_inc_vat": 10.0,
            },
            # Gap: 01:00–01:30 is missing
            # Covers 01:30–02:00 (1 slot)
            {
                "valid_from": datetime(2025, 6, 1, 1, 30, tzinfo=UTC),
                "valid_to": datetime(2025, 6, 1, 2, 0, tzinfo=UTC),
                "value_inc_vat": 25.0,
            },
        ]

        rate_map = _build_historical_rate_map(raw_rates, period_from, period_to)

        # 01:00 slot must be forward-filled from 10.0, not 0.0
        gap_slot = datetime(2025, 6, 1, 1, 0, tzinfo=UTC)
        assert rate_map[gap_slot] == 10.0
        assert rate_map[gap_slot] != 0.0

    def test_all_dict_keys_are_utc_aware_datetimes(self):
        """Hockney: rate_map dict keys must be timezone-aware UTC datetimes."""
        period_from = datetime(2025, 6, 1, 0, 0, tzinfo=UTC)
        period_to = datetime(2025, 6, 1, 1, 0, tzinfo=UTC)  # 2 slots
        raw_rates = [
            {"valid_from": period_from, "valid_to": period_to, "value_inc_vat": 20.0}
        ]

        rate_map = _build_historical_rate_map(raw_rates, period_from, period_to)

        for key in rate_map:
            assert isinstance(key, datetime), f"Key {key!r} is not a datetime"
            assert key.tzinfo is not None, f"Key {key!r} is not timezone-aware"
            # Confirm UTC (utcoffset == 0)
            assert key.utcoffset().total_seconds() == 0, f"Key {key!r} is not UTC"


# ---------------------------------------------------------------------------
# Tests: fetch_standing_charges
# ---------------------------------------------------------------------------


class TestFetchStandingCharges:
    async def test_happy_path_returns_parsed_dicts(self):
        """fetch_standing_charges returns list of dicts with parsed datetimes."""
        TARIFF = "E-1R-AGILE-FLEX-22-11-25-B"
        response = {
            "results": [
                {
                    "valid_from": "2025-04-01T00:00:00Z",
                    "valid_to": None,
                    "value_inc_vat": 46.36,
                }
            ],
            "next": None,
        }
        session = _mock_session_get([response])
        client = _make_client()

        charges = await client.fetch_standing_charges(
            session, TARIFF, PERIOD_FROM, PERIOD_TO
        )

        assert len(charges) == 1
        assert isinstance(charges[0]["valid_from"], datetime)
        assert charges[0]["value_inc_vat"] == pytest.approx(46.36)
        assert charges[0]["valid_to"] is None

    async def test_standing_charges_valid_from_is_utc_aware(self):
        """Parsed valid_from must be UTC-aware."""
        TARIFF = "E-1R-AGILE-FLEX-22-11-25-B"
        response = {
            "results": [
                {
                    "valid_from": "2025-04-01T00:00:00Z",
                    "valid_to": None,
                    "value_inc_vat": 50.0,
                }
            ],
            "next": None,
        }
        session = _mock_session_get([response])
        client = _make_client()

        charges = await client.fetch_standing_charges(
            session, TARIFF, PERIOD_FROM, PERIOD_TO
        )

        assert charges[0]["valid_from"].tzinfo is not None


# ---------------------------------------------------------------------------
# Tests: seed valid_to fix (Agile midnight-slot regression)
# ---------------------------------------------------------------------------


class TestSeedValidToFix:
    """Regression tests for the Agile pre-window seed valid_to bug.

    Bug: The last Agile slot before period_from has valid_to == period_from.
    If the seed copies this valid_to, the seed has valid_from == valid_to
    (zero-width window) and _build_historical_rate_map rejects it for every
    slot (condition: vt is None or vt > slot fails when vt == slot).

    Fix: seed valid_to is set to period_from + 30 min, covering only the
    midnight slot. current_rate persists forward until the first real Agile
    rate takes over.
    """

    def test_seed_with_valid_to_equals_valid_from_is_rejected(self):
        """Regression: zero-width seed (valid_to == valid_from) → midnight slot gets 0."""
        period_from = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        period_to = datetime(2026, 3, 1, 1, 0, tzinfo=UTC)  # 2 slots

        # Zero-width seed — this was the old bug
        raw_rates = [
            {
                "valid_from": period_from,
                "valid_to": period_from,  # valid_to == valid_from → zero-width
                "value_inc_vat": 17.0,
            },
        ]

        rate_map = _build_historical_rate_map(raw_rates, period_from, period_to)

        # With a zero-width seed, no rate ever matches → map is empty (bug reproduced)
        midnight = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        assert rate_map.get(midnight) is None or rate_map.get(midnight, 0.0) == 0.0

    def test_seed_with_valid_to_30min_covers_midnight(self):
        """Fix: seed valid_to = period_from + 30 min → midnight slot gets the seed rate."""
        period_from = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        period_to = datetime(2026, 3, 1, 1, 0, tzinfo=UTC)  # 2 slots

        # Corrected seed — valid_to = period_from + 30 min
        raw_rates = [
            {
                "valid_from": period_from,
                "valid_to": period_from + timedelta(minutes=30),
                "value_inc_vat": 17.0,
            },
        ]

        rate_map = _build_historical_rate_map(raw_rates, period_from, period_to)

        midnight = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        assert rate_map.get(midnight) == pytest.approx(17.0)

    def test_seed_does_not_overwrite_real_agile_rates_after_first_slot(self):
        """Fix: seed covers only midnight; real Agile rates are used from 00:30 onward."""
        period_from = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        period_to = datetime(2026, 3, 1, 2, 0, tzinfo=UTC)  # 4 slots

        raw_rates = [
            # Seed covering only midnight slot
            {
                "valid_from": period_from,
                "valid_to": period_from + timedelta(minutes=30),
                "value_inc_vat": 17.0,
            },
            # Real Agile rate from 00:30
            {
                "valid_from": datetime(2026, 3, 1, 0, 30, tzinfo=UTC),
                "valid_to": datetime(2026, 3, 1, 1, 0, tzinfo=UTC),
                "value_inc_vat": 19.0,
            },
            # Real Agile rate from 01:00
            {
                "valid_from": datetime(2026, 3, 1, 1, 0, tzinfo=UTC),
                "valid_to": datetime(2026, 3, 1, 1, 30, tzinfo=UTC),
                "value_inc_vat": 22.0,
            },
            # Real Agile rate from 01:30
            {
                "valid_from": datetime(2026, 3, 1, 1, 30, tzinfo=UTC),
                "valid_to": datetime(2026, 3, 1, 2, 0, tzinfo=UTC),
                "value_inc_vat": 21.0,
            },
        ]

        rate_map = _build_historical_rate_map(raw_rates, period_from, period_to)

        assert rate_map[datetime(2026, 3, 1, 0, 0, tzinfo=UTC)] == pytest.approx(17.0)
        assert rate_map[datetime(2026, 3, 1, 0, 30, tzinfo=UTC)] == pytest.approx(19.0)
        assert rate_map[datetime(2026, 3, 1, 1, 0, tzinfo=UTC)] == pytest.approx(22.0)
        assert rate_map[datetime(2026, 3, 1, 1, 30, tzinfo=UTC)] == pytest.approx(21.0)

    def test_seed_persists_to_first_real_agile_rate_mid_month(self):
        """Seed persists through all days before the first real Agile rate entry.

        Simulates the scenario where the Agile rate API only returns data from
        March 17, but the period starts March 1. The seed rate should fill slots
        March 1–16 and real rates take over from March 17.
        """
        period_from = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        period_to = datetime(
            2026, 3, 1, 2, 0, tzinfo=UTC
        )  # 4 slots (proxy for full scenario)

        # Seed: last pre-window rate (e.g. Feb 28 23:30 slot)
        # valid_to is period_from + 30 min (corrected)
        seed_rate = 17.0
        first_real_rate = 19.0

        raw_rates = [
            {
                "valid_from": period_from,
                "valid_to": period_from + timedelta(minutes=30),
                "value_inc_vat": seed_rate,
            },
            # First real Agile rate at 00:30 (represents March 17 in the real scenario)
            {
                "valid_from": datetime(2026, 3, 1, 0, 30, tzinfo=UTC),
                "valid_to": datetime(2026, 3, 1, 2, 0, tzinfo=UTC),
                "value_inc_vat": first_real_rate,
            },
        ]

        rate_map = _build_historical_rate_map(raw_rates, period_from, period_to)

        # Midnight covered by seed
        assert rate_map[datetime(2026, 3, 1, 0, 0, tzinfo=UTC)] == pytest.approx(
            seed_rate
        )
        # 00:30 and beyond use real rates
        assert rate_map[datetime(2026, 3, 1, 0, 30, tzinfo=UTC)] == pytest.approx(
            first_real_rate
        )
        assert rate_map[datetime(2026, 3, 1, 1, 0, tzinfo=UTC)] == pytest.approx(
            first_real_rate
        )
        assert rate_map[datetime(2026, 3, 1, 1, 30, tzinfo=UTC)] == pytest.approx(
            first_real_rate
        )


# ---------------------------------------------------------------------------
# Tests: _rates_cover_period
# ---------------------------------------------------------------------------


class TestRatesCoverPeriod:
    """Tests for the _rates_cover_period cache-coverage validator."""

    def setup_method(self):
        from custom_components.battery_charge_calculator.tariff_comparison import (
            _rates_cover_period,
        )

        self._fn = _rates_cover_period

    def _make_rates(self, first_valid_from: datetime) -> dict:
        return {
            "unit_rates": [
                {
                    "valid_from": first_valid_from,
                    "valid_to": None,
                    "value_inc_vat": 20.0,
                }
            ],
            "standing_charges": [],
        }

    def test_none_returns_false(self):
        assert self._fn(None, datetime(2026, 3, 1, tzinfo=UTC)) is False

    def test_empty_unit_rates_returns_false(self):
        assert (
            self._fn(
                {"unit_rates": [], "standing_charges": []},
                datetime(2026, 3, 1, tzinfo=UTC),
            )
            is False
        )

    def test_rates_starting_at_period_from_returns_true(self):
        period_from = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        rates = self._make_rates(period_from)
        assert self._fn(rates, period_from) is True

    def test_rates_starting_before_period_from_returns_true(self):
        period_from = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        rates = self._make_rates(datetime(2026, 2, 1, 0, 0, tzinfo=UTC))
        assert self._fn(rates, period_from) is True

    def test_rates_starting_after_period_from_returns_false(self):
        """Cached rates starting March 17 don't cover a period starting March 1."""
        period_from = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        rates = self._make_rates(datetime(2026, 3, 17, 0, 0, tzinfo=UTC))
        assert self._fn(rates, period_from) is False

    def test_string_valid_from_not_datetime_returns_false(self):
        """Rates that were not parsed (still strings) return False — forces re-fetch."""
        rates = {
            "unit_rates": [
                {"valid_from": "2026-03-17T00:00:00Z", "value_inc_vat": 20.0}
            ],
            "standing_charges": [],
        }
        assert self._fn(rates, datetime(2026, 3, 1, tzinfo=UTC)) is False
