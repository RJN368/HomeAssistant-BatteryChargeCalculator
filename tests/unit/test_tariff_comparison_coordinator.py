"""Unit tests for TariffComparisonCoordinator._calculate_all.

Tests cover:
- export_tariff_code is propagated from shared_export_code into each entry
- export_earnings_gbp is non-zero when export slots + export rates are provided
- _calculate_all does NOT need self (pure function of args)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

# conftest installs HA stubs before any integration import
from custom_components.battery_charge_calculator.tariff_comparison import (
    TariffComparisonCoordinator,
)

UTC = timezone.utc

# Shorthand: call _calculate_all as a plain function (self is never used in body)
_calculate_all = TariffComparisonCoordinator._calculate_all


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slot(dt: datetime, consumption: float) -> dict:
    return {"interval_start": dt, "consumption": consumption}


def _rate_entry(valid_from: datetime, valid_to: datetime | None, rate: float) -> dict:
    return {"valid_from": valid_from, "valid_to": valid_to, "value_inc_vat": rate}


def _make_period():
    """Return a 1-day period for simple tests."""
    period_from = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    period_to = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)
    return period_from, period_to


def _build_rate_dict(
    valid_from: datetime,
    valid_to: datetime | None,
    import_rate: float,
    sc_rate: float = 30.0,
) -> dict:
    """Build a tariff_rates entry for one tariff code."""
    return {
        "unit_rates": [_rate_entry(valid_from, valid_to, import_rate)],
        "standing_charges": [_rate_entry(valid_from, valid_to, sc_rate)],
    }


# ---------------------------------------------------------------------------
# Tests: export_tariff_code in entries
# ---------------------------------------------------------------------------

class TestExportTariffCodeInEntries:
    def test_export_tariff_code_is_set_when_shared_export_code_provided(self):
        """Each tariff entry has export_tariff_code == shared_export_code."""
        period_from, period_to = _make_period()
        t = period_from

        tariff_configs = [
            {"import_tariff_code": "IMPORT-A", "name": "A", "is_current": True},
            {"import_tariff_code": "IMPORT-B", "name": "B", "is_current": False},
        ]
        import_slots = [_slot(t + timedelta(minutes=30 * i), 0.5) for i in range(4)]
        tariff_rates = {
            "IMPORT-A": _build_rate_dict(t, None, 25.0),
            "IMPORT-B": _build_rate_dict(t, None, 20.0),
            "EXPORT-X": {"unit_rates": [_rate_entry(t, None, 5.0)], "standing_charges": []},
        }

        result = _calculate_all(
            None,
            tariff_configs,
            import_slots,
            None,  # no export slots
            tariff_rates,
            period_from,
            period_to,
            export_meter_missing=True,
            shared_export_code="EXPORT-X",
        )

        entries = {e["import_tariff_code"]: e for e in result["tariffs"]}
        assert entries["IMPORT-A"]["export_tariff_code"] == "EXPORT-X"
        assert entries["IMPORT-B"]["export_tariff_code"] == "EXPORT-X"

    def test_export_tariff_code_is_null_when_no_shared_export_code(self):
        """export_tariff_code is None when shared_export_code is not provided."""
        period_from, period_to = _make_period()
        t = period_from
        tariff_configs = [{"import_tariff_code": "IMPORT-A", "name": "A", "is_current": True}]
        tariff_rates = {"IMPORT-A": _build_rate_dict(t, None, 25.0)}
        import_slots = [_slot(t, 0.5)]

        result = _calculate_all(
            None,
            tariff_configs,
            import_slots,
            None,
            tariff_rates,
            period_from,
            period_to,
            export_meter_missing=True,
            shared_export_code=None,
        )

        assert result["tariffs"][0]["export_tariff_code"] is None


# ---------------------------------------------------------------------------
# Tests: export_earnings_gbp
# ---------------------------------------------------------------------------

class TestExportEarnings:
    def test_export_earnings_nonzero_when_export_slots_and_rates_provided(self):
        """export_earnings_gbp > 0 when export slots and export rate map are provided."""
        period_from, period_to = _make_period()
        t = period_from

        tariff_configs = [
            {"import_tariff_code": "IMPORT-A", "name": "A", "is_current": True}
        ]
        # 4 import slots, 2 export slots
        import_slots = [_slot(t + timedelta(minutes=30 * i), 0.5) for i in range(4)]
        export_slots = [
            _slot(t + timedelta(minutes=0), 1.0),   # 1 kWh exported at 14:30
            _slot(t + timedelta(minutes=30), 0.8),  # 0.8 kWh exported at 15:00
        ]
        export_code = "EXPORT-SEG"
        tariff_rates = {
            "IMPORT-A": _build_rate_dict(t, None, 25.0),
            export_code: {
                "unit_rates": [_rate_entry(t, None, 15.0)],  # 15 p/kWh export
                "standing_charges": [],
            },
        }

        result = _calculate_all(
            None,
            tariff_configs,
            import_slots,
            export_slots,
            tariff_rates,
            period_from,
            period_to,
            export_meter_missing=False,
            shared_export_code=export_code,
        )

        entry = result["tariffs"][0]
        assert entry["export_tariff_code"] == export_code
        # 1.0 + 0.8 = 1.8 kWh * 15 p/kWh = 27 p = £0.27
        assert entry["totals"]["export_earnings_gbp"] == pytest.approx(0.27, abs=0.01)
        assert entry["monthly"][0]["export_earnings_gbp"] == pytest.approx(0.27, abs=0.01)

    def test_export_earnings_zero_when_export_rate_code_not_in_tariff_rates(self):
        """export_earnings_gbp == 0 when shared_export_code has no rate data fetched."""
        period_from, period_to = _make_period()
        t = period_from

        tariff_configs = [
            {"import_tariff_code": "IMPORT-A", "name": "A", "is_current": True}
        ]
        import_slots = [_slot(t, 0.5)]
        export_slots = [_slot(t, 1.0)]
        tariff_rates = {"IMPORT-A": _build_rate_dict(t, None, 25.0)}
        # EXPORT-MISSING is not in tariff_rates → no rate map built

        result = _calculate_all(
            None,
            tariff_configs,
            import_slots,
            export_slots,
            tariff_rates,
            period_from,
            period_to,
            export_meter_missing=False,
            shared_export_code="EXPORT-MISSING",
        )

        # export_tariff_code is set even though rates are missing (entry records intent)
        assert result["tariffs"][0]["export_tariff_code"] == "EXPORT-MISSING"
        # But earnings are 0 because no rate map could be built
        assert result["tariffs"][0]["totals"]["export_earnings_gbp"] == 0.0

    def test_export_earnings_zero_when_export_slots_none(self):
        """export_earnings_gbp == 0 when no export meter is configured."""
        period_from, period_to = _make_period()
        t = period_from
        tariff_configs = [{"import_tariff_code": "IMPORT-A", "name": "A", "is_current": True}]
        import_slots = [_slot(t, 0.5)]
        export_code = "EXPORT-X"
        tariff_rates = {
            "IMPORT-A": _build_rate_dict(t, None, 25.0),
            export_code: {"unit_rates": [_rate_entry(t, None, 15.0)], "standing_charges": []},
        }

        result = _calculate_all(
            None,
            tariff_configs,
            import_slots,
            None,  # no export slots
            tariff_rates,
            period_from,
            period_to,
            export_meter_missing=True,
            shared_export_code=export_code,
        )

        assert result["tariffs"][0]["totals"]["export_earnings_gbp"] == 0.0


# ---------------------------------------------------------------------------
# Tests: period_to boundary — no partial-month leakage
# ---------------------------------------------------------------------------

class TestPeriodToBoundary:
    def test_slot_at_exactly_period_to_is_excluded(self):
        """A slot starting exactly at period_to must not appear in output."""
        period_from = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        period_to = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)  # April 1 00:00

        # All March slots plus one stray April slot at exactly period_to
        march_slots = [
            _slot(period_from + timedelta(minutes=30 * i), 0.5)
            for i in range(2)
        ]
        april_boundary_slot = _slot(period_to, 0.5)  # exactly at period_to
        import_slots = march_slots + [april_boundary_slot]

        t = period_from
        tariff_rates = {
            "IMPORT-A": {
                "unit_rates": [_rate_entry(t, None, 25.0)],
                "standing_charges": [],
            }
        }
        tariff_configs = [{"import_tariff_code": "IMPORT-A", "name": "A", "is_current": True}]

        result = _calculate_all(
            None,
            tariff_configs,
            import_slots,
            None,
            tariff_rates,
            period_from,
            period_to,
            export_meter_missing=True,
        )

        months = [m["month"] for m in result["tariffs"][0]["monthly"]]
        assert "2026-04" not in months, "April slot at period_to boundary must be excluded"
        assert "2026-03" in months

    def test_standing_charges_not_calculated_for_full_month_when_only_boundary_slot(self):
        """Standing charges for a partial-month entry must be prorated to period_to.

        Regression: when a slot at period_to leaks in, the calculator was
        computing standing charges for the entire calendar month (e.g. all of
        April = 30 days) even though no real consumption occurred in April.
        The fix removes such slots before the standing charge loop runs.
        """
        period_from = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        period_to = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)

        march_slots = [_slot(period_from + timedelta(minutes=30 * i), 0.5) for i in range(2)]
        april_slot = _slot(period_to, 0.1)  # leaks in from API
        import_slots = march_slots + [april_slot]

        sc_valid_from = datetime(2026, 1, 1, tzinfo=UTC)
        tariff_rates = {
            "IMPORT-A": {
                "unit_rates": [_rate_entry(sc_valid_from, None, 25.0)],
                "standing_charges": [_rate_entry(sc_valid_from, None, 50.0)],  # 50p/day
            }
        }
        tariff_configs = [{"import_tariff_code": "IMPORT-A", "name": "A", "is_current": True}]

        result = _calculate_all(
            None,
            tariff_configs,
            import_slots,
            None,
            tariff_rates,
            period_from,
            period_to,
            export_meter_missing=True,
        )

        months_by_key = {m["month"]: m for m in result["tariffs"][0]["monthly"]}
        # April should not appear at all after the fix
        assert "2026-04" not in months_by_key, (
            "April slot at period_to boundary should be filtered out; "
            "if April appears, standing charges for the full month are wrongly added"
        )
