#!/usr/bin/env python3
"""
Local HACS validation script.

Replicates the checks performed by hacs/action:main without Docker or network access.
Validates local files against the same voluptuous schemas used by the HACS integration source.

Usage:
    python3 scripts/validate_hacs.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from awesomeversion import AwesomeVersion
import voluptuous as vol
from voluptuous.humanize import humanize_error

# ── Repo root ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m⚠\033[0m"

errors: list[str] = []
warnings: list[str] = []


def ok(msg: str) -> None:
    print(f"  {PASS} {msg}")


def fail(msg: str) -> None:
    print(f"  {FAIL} {msg}")
    errors.append(msg)


def warn(msg: str) -> None:
    print(f"  {WARN} {msg}")
    warnings.append(msg)


# ── Schemas (copied verbatim from HACS integration source) ───────────────────

VALID_COUNTRY_CODES = [
    "AD",
    "AE",
    "AF",
    "AG",
    "AI",
    "AL",
    "AM",
    "AO",
    "AQ",
    "AR",
    "AS",
    "AT",
    "AU",
    "AW",
    "AX",
    "AZ",
    "BA",
    "BB",
    "BD",
    "BE",
    "BF",
    "BG",
    "BH",
    "BI",
    "BJ",
    "BL",
    "BM",
    "BN",
    "BO",
    "BQ",
    "BR",
    "BS",
    "BT",
    "BV",
    "BW",
    "BY",
    "BZ",
    "CA",
    "CC",
    "CD",
    "CF",
    "CG",
    "CH",
    "CI",
    "CK",
    "CL",
    "CM",
    "CN",
    "CO",
    "CR",
    "CU",
    "CV",
    "CW",
    "CX",
    "CY",
    "CZ",
    "DE",
    "DJ",
    "DK",
    "DM",
    "DO",
    "DZ",
    "EC",
    "EE",
    "EG",
    "EH",
    "ER",
    "ES",
    "ET",
    "FI",
    "FJ",
    "FK",
    "FM",
    "FO",
    "FR",
    "GA",
    "GB",
    "GD",
    "GE",
    "GF",
    "GG",
    "GH",
    "GI",
    "GL",
    "GM",
    "GN",
    "GP",
    "GQ",
    "GR",
    "GS",
    "GT",
    "GU",
    "GW",
    "GY",
    "HK",
    "HM",
    "HN",
    "HR",
    "HT",
    "HU",
    "ID",
    "IE",
    "IL",
    "IM",
    "IN",
    "IO",
    "IQ",
    "IR",
    "IS",
    "IT",
    "JE",
    "JM",
    "JO",
    "JP",
    "KE",
    "KG",
    "KH",
    "KI",
    "KM",
    "KN",
    "KP",
    "KR",
    "KW",
    "KY",
    "KZ",
    "LA",
    "LB",
    "LC",
    "LI",
    "LK",
    "LR",
    "LS",
    "LT",
    "LU",
    "LV",
    "LY",
    "MA",
    "MC",
    "MD",
    "ME",
    "MF",
    "MG",
    "MH",
    "MK",
    "ML",
    "MM",
    "MN",
    "MO",
    "MP",
    "MQ",
    "MR",
    "MS",
    "MT",
    "MU",
    "MV",
    "MW",
    "MX",
    "MY",
    "MZ",
    "NA",
    "NC",
    "NE",
    "NF",
    "NG",
    "NI",
    "NL",
    "NO",
    "NP",
    "NR",
    "NU",
    "NZ",
    "OM",
    "PA",
    "PE",
    "PF",
    "PG",
    "PH",
    "PK",
    "PL",
    "PM",
    "PN",
    "PR",
    "PS",
    "PT",
    "PW",
    "PY",
    "QA",
    "RE",
    "RO",
    "RS",
    "RU",
    "RW",
    "SA",
    "SB",
    "SC",
    "SD",
    "SE",
    "SG",
    "SH",
    "SI",
    "SJ",
    "SK",
    "SL",
    "SM",
    "SN",
    "SO",
    "SR",
    "SS",
    "ST",
    "SV",
    "SX",
    "SY",
    "SZ",
    "TC",
    "TD",
    "TF",
    "TG",
    "TH",
    "TJ",
    "TK",
    "TL",
    "TM",
    "TN",
    "TO",
    "TR",
    "TT",
    "TV",
    "TW",
    "TZ",
    "UA",
    "UG",
    "UM",
    "US",
    "UY",
    "UZ",
    "VA",
    "VC",
    "VE",
    "VG",
    "VI",
    "VN",
    "VU",
    "WF",
    "WS",
    "YE",
    "YT",
    "ZA",
    "ZM",
    "ZW",
    # UK is not ISO 3166 but HACS accepts it
    "UK",
]


def _country_validator(values) -> list[str]:
    countries: list[str] = []
    if isinstance(values, str):
        countries.append(values.upper())
    elif isinstance(values, list):
        for v in values:
            countries.append(v.upper())
    else:
        raise vol.Invalid(
            f"Value '{values}' is not a string or list.", path=["country"]
        )
    for country in countries:
        if country not in VALID_COUNTRY_CODES:
            raise vol.Invalid(
                f"Value '{country}' is not a valid ISO 3166 country code.",
                path=["country"],
            )
    return countries


HACS_MANIFEST_JSON_SCHEMA = vol.Schema(
    {
        vol.Optional("content_in_root"): bool,
        vol.Optional("country"): _country_validator,
        vol.Optional("filename"): str,
        vol.Optional("hacs"): str,
        vol.Optional("hide_default_branch"): bool,
        vol.Optional("homeassistant"): str,
        vol.Optional("persistent_directory"): str,
        vol.Optional("render_readme"): bool,
        vol.Optional("zip_release"): bool,
        vol.Required("name"): str,
    },
    extra=vol.PREVENT_EXTRA,
)

INTEGRATION_MANIFEST_JSON_SCHEMA = vol.Schema(
    {
        vol.Required("codeowners"): list,
        vol.Required("documentation"): str,  # url_validator simplified
        vol.Required("domain"): str,
        vol.Required("issue_tracker"): str,  # url_validator simplified
        vol.Required("name"): str,
        vol.Required("version"): vol.Coerce(AwesomeVersion),
    },
    extra=vol.ALLOW_EXTRA,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        fail(f"Invalid JSON in {path.relative_to(ROOT)}: {exc}")
        return None
    except FileNotFoundError:
        fail(f"File not found: {path.relative_to(ROOT)}")
        return None


def find_integration_dir() -> Path | None:
    cc = ROOT / "custom_components"
    if not cc.is_dir():
        return None
    dirs = [d for d in cc.iterdir() if d.is_dir() and not d.name.startswith("_")]
    return dirs[0] if dirs else None


# ── Check: hacs.json ─────────────────────────────────────────────────────────


def check_hacsjson() -> None:
    print("\n[hacs.json]")
    hacs_path = ROOT / "hacs.json"
    data = load_json(hacs_path)
    if data is None:
        return
    ok("File is valid JSON")
    try:
        HACS_MANIFEST_JSON_SCHEMA(data)
        ok("Schema validation passed")
        for key, value in data.items():
            ok(f"  {key}: {value!r}")
    except vol.Invalid as exc:
        fail(f"Schema error: {humanize_error(data, exc)}")


# ── Check: integration manifest.json ─────────────────────────────────────────


def check_integration_manifest(integration_dir: Path) -> None:
    print("\n[manifest.json]")
    manifest_path = integration_dir / "manifest.json"
    data = load_json(manifest_path)
    if data is None:
        return
    ok("File is valid JSON")
    try:
        INTEGRATION_MANIFEST_JSON_SCHEMA(data)
        ok("Schema validation passed")
    except vol.Invalid as exc:
        fail(f"Schema error: {humanize_error(data, exc)}")
        return

    # Specific field checks
    if not data.get("documentation", "").startswith("http"):
        fail("'documentation' must be a URL")
    else:
        ok(f"  documentation: {data['documentation']}")

    if not data.get("issue_tracker", "").startswith("http"):
        fail("'issue_tracker' must be a URL")
    else:
        ok(f"  issue_tracker: {data['issue_tracker']}")

    if not data.get("version"):
        fail("'version' is required")
    else:
        ok(f"  version: {data['version']}")

    if not data.get("codeowners"):
        fail("'codeowners' must be a non-empty list")
    else:
        ok(f"  codeowners: {data['codeowners']}")


# ── Check: brand assets ───────────────────────────────────────────────────────


def check_brands(integration_dir: Path) -> None:
    print("\n[brand assets]")
    icon = integration_dir / "brand" / "icon.png"
    logo = integration_dir / "brand" / "logo.png"
    icon_dark = integration_dir / "brand" / "icon@2x.png"

    if icon.exists():
        ok(f"icon.png found ({icon.stat().st_size} bytes)")
    else:
        fail(f"icon.png missing at {icon.relative_to(ROOT)}")

    for optional in [logo, icon_dark]:
        if optional.exists():
            ok(f"{optional.name} found (optional)")


# ── Check: README ─────────────────────────────────────────────────────────────


def check_readme() -> None:
    print("\n[README]")
    for name in ("README.md", "readme.md", "README.MD"):
        if (ROOT / name).exists():
            ok(f"{name} found")
            return
    fail("No README.md found")


# ── Check: key files present ──────────────────────────────────────────────────


def check_structure(integration_dir: Path) -> None:
    print("\n[repository structure]")
    ok(f"Integration directory: custom_components/{integration_dir.name}/")

    required = ["__init__.py", "manifest.json"]
    for f in required:
        p = integration_dir / f
        if p.exists():
            ok(f"  {f} present")
        else:
            fail(f"  {f} missing")


# ── Check: no extra keys in hacs.json ────────────────────────────────────────


def check_no_known_bad_keys() -> None:
    print("\n[known invalid hacs.json keys]")
    hacs_path = ROOT / "hacs.json"
    data = load_json(hacs_path)
    if data is None:
        return
    known_invalid = [
        "render_readme"
    ]  # removed in HACS v2; causes PREVENT_EXTRA failure
    found_invalid = [k for k in data if k in known_invalid]
    if found_invalid:
        fail(f"Deprecated/removed keys present: {found_invalid}")
    else:
        ok("No deprecated keys found")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    print("=" * 60)
    print("  HACS Local Validation")
    print(f"  Repo: {ROOT.name}")
    print("=" * 60)

    integration_dir = find_integration_dir()
    if not integration_dir:
        print(f"\n{FAIL} No integration found under custom_components/")
        return 1

    check_hacsjson()
    check_no_known_bad_keys()
    check_integration_manifest(integration_dir)
    check_brands(integration_dir)
    check_readme()
    check_structure(integration_dir)

    print("\n" + "=" * 60)
    if errors:
        print(f"  {FAIL}  FAILED — {len(errors)} error(s):")
        for e in errors:
            print(f"     • {e}")
        if warnings:
            print(f"  {WARN}  {len(warnings)} warning(s):")
            for w in warnings:
                print(f"     • {w}")
        return 1
    else:
        print(f"  {PASS}  ALL CHECKS PASSED")
        if warnings:
            print(f"  {WARN}  {len(warnings)} warning(s):")
            for w in warnings:
                print(f"     • {w}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
