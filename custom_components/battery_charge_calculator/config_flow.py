"""Config flow for the battery_manager component."""

import json
import logging
import re
import voluptuous as vol

import aiohttp

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import aiohttp_client, config_validation as cv
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import const
from .octopus_agile import OCTOPUS_API_BASE, OctopusAgileRatesClient

_LOGGER = logging.getLogger(__name__)


# ─────────────────────────── schema helpers ──────────────────────────────────


def get_schema(
    serialno="",
    apitoken="",
    octopus_account_number="",
    octopus_api_key="",
    simulate=False,
    inverter_size_kw=const.DEFAULT_INVERTER_SIZE_KW,
    inverter_efficiency=const.DEFAULT_INVERTER_EFFICIENCY,
    battery_capacity_kwh=const.DEFAULT_BATTERY_CAPACITY_KWH,
    base_load=const.DEFAULT_BASE_LOAD_KWH_30MIN,
    # legacy heating params accepted but ignored – heating has its own steps
    **_kwargs,
):
    """Main/grid settings schema (Step 1).  Heating is configured separately."""
    return vol.Schema(
        {
            vol.Required(const.GIVENERGY_SERIAL_NUMBER, default=serialno): cv.string,
            vol.Required(const.GIVENERGY_API_TOKEN, default=apitoken): cv.string,
            vol.Required(
                const.OCTOPUS_ACCOUNT_NUMBER, default=octopus_account_number
            ): cv.string,
            vol.Required(const.OCTOPUS_APIKEY, default=octopus_api_key): cv.string,
            vol.Required(const.SIMULATE_ONLY, default=simulate): cv.boolean,
            vol.Required(
                const.INVERTER_SIZE_KW, default=inverter_size_kw
            ): const.cv_float,
            vol.Required(
                const.INVERTER_EFFICIENCY, default=inverter_efficiency
            ): const.cv_float,
            vol.Optional(
                const.BATTERY_CAPACITY_KWH, default=battery_capacity_kwh
            ): const.cv_float,
            vol.Optional(const.BASE_LOAD_KWH_30MIN, default=base_load): const.cv_float,
        }
    )


def _heating_type_schema(heating_type=const.DEFAULT_HEATING_TYPE):
    return vol.Schema(
        {
            vol.Required(const.HEATING_TYPE, default=heating_type): SelectSelector(
                SelectSelectorConfig(
                    options=const.HEATING_TYPES,
                    translation_key="heating_type",
                )
            ),
        }
    )


def _heating_interpolation_schema(known_points=const.DEFAULT_HEATING_KNOWN_POINTS):
    return vol.Schema(
        {
            vol.Optional(const.HEATING_KNOWN_POINTS, default=known_points): cv.string,
        }
    )


def _heating_electric_schema(
    cop=const.DEFAULT_HEATING_COP,
    indoor_temp=const.DEFAULT_HEATING_INDOOR_TEMP,
    flow_temp=const.DEFAULT_HEATING_FLOW_TEMP,
):
    """COP, indoor target temp, and heat pump flow temperature.

    flow_temp: the temperature the heating system delivers to radiators/UFH.
    Used to calculate how COP varies with outdoor temperature.
    Typical values: 45-55°C for radiators, 35-40°C for underfloor heating.
    Only affects shape of the power curve when COP > 1.
    """
    return vol.Schema(
        {
            vol.Optional(const.HEATING_COP, default=cop): const.cv_float,
            vol.Optional(
                const.HEATING_INDOOR_TEMP, default=indoor_temp
            ): const.cv_float,
            vol.Optional(const.HEATING_FLOW_TEMP, default=flow_temp): const.cv_float,
        }
    )


def _heat_loss_method_schema(method=const.HEAT_LOSS_METHOD_KNOWN):
    """Ask the user whether they know their heat loss or want to estimate it."""
    return vol.Schema(
        {
            vol.Required(const.HEAT_LOSS_METHOD, default=method): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        const.HEAT_LOSS_METHOD_KNOWN,
                        const.HEAT_LOSS_METHOD_REPORT,
                        const.HEAT_LOSS_METHOD_ESTIMATE,
                    ],
                    translation_key="heat_loss_method",
                )
            ),
        }
    )


def _heat_loss_report_schema(
    watts=const.DEFAULT_HEAT_LOSS_REPORT_WATTS,
    outdoor_temp=const.DEFAULT_HEAT_LOSS_REPORT_OUTDOOR_TEMP,
    indoor_temp=const.DEFAULT_HEAT_LOSS_REPORT_INDOOR_TEMP,
):
    """Fields matching a standard heat loss survey report."""
    return vol.Schema(
        {
            vol.Required(const.HEAT_LOSS_REPORT_WATTS, default=watts): const.cv_float,
            vol.Required(
                const.HEAT_LOSS_REPORT_OUTDOOR_TEMP, default=outdoor_temp
            ): const.cv_float,
            vol.Required(
                const.HEAT_LOSS_REPORT_INDOOR_TEMP, default=indoor_temp
            ): const.cv_float,
        }
    )


def _heat_loss_known_schema(heat_loss=const.DEFAULT_HEATING_HEAT_LOSS):
    """Single field: the known heat loss in W/°C."""
    return vol.Schema(
        {
            vol.Required(const.HEATING_HEAT_LOSS, default=heat_loss): const.cv_float,
        }
    )


def _building_estimate_schema(
    floor_area=const.DEFAULT_BUILDING_FLOOR_AREA,
    age=const.DEFAULT_BUILDING_AGE,
    wall_type=const.DEFAULT_BUILDING_WALL_TYPE,
    glazing=const.DEFAULT_BUILDING_GLAZING,
):
    return vol.Schema(
        {
            vol.Optional(const.BUILDING_FLOOR_AREA, default=floor_area): const.cv_float,
            vol.Optional(const.BUILDING_AGE, default=age): SelectSelector(
                SelectSelectorConfig(
                    options=const.BUILDING_AGE_BANDS,
                    translation_key="building_age",
                )
            ),
            vol.Optional(const.BUILDING_WALL_TYPE, default=wall_type): SelectSelector(
                SelectSelectorConfig(
                    options=const.BUILDING_WALL_TYPES,
                    translation_key="building_wall_type",
                )
            ),
            vol.Optional(const.BUILDING_GLAZING, default=glazing): SelectSelector(
                SelectSelectorConfig(
                    options=const.BUILDING_GLAZING_TYPES,
                    translation_key="building_glazing",
                )
            ),
        }
    )


def _ml_settings_schema(
    ml_enabled: bool = False,
    service_url: str = const.DEFAULT_ML_SERVICE_URL,
    api_key: str = const.DEFAULT_ML_SERVICE_API_KEY,
    tls_fingerprint: str = const.DEFAULT_ML_SERVICE_TLS_FINGERPRINT,
    consumption_source: str = const.DEFAULT_ML_CONSUMPTION_SOURCE,
    octopus_mpan: str = "",
    octopus_meter_serial: str = const.DEFAULT_OCTOPUS_METER_SERIAL,
    lookback_days: int = const.DEFAULT_ML_TRAINING_LOOKBACK_DAYS,
) -> vol.Schema:
    """Schema for the ML power estimation settings step.

    Points to an external BCC ML Service instance.  All fields default to
    safe values so existing config entries continue to work unchanged (D-16).
    """
    return vol.Schema(
        {
            vol.Optional(const.ML_ENABLED, default=ml_enabled): cv.boolean,
            vol.Optional(const.ML_SERVICE_URL, default=service_url): cv.string,
            vol.Optional(const.ML_SERVICE_API_KEY, default=api_key): cv.string,
            vol.Optional(
                const.ML_SERVICE_TLS_FINGERPRINT, default=tls_fingerprint
            ): cv.string,
            vol.Optional(
                const.ML_CONSUMPTION_SOURCE, default=consumption_source
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        const.ML_CONSUMPTION_SOURCE_GIVENERGY,
                        const.ML_CONSUMPTION_SOURCE_OCTOPUS,
                        const.ML_CONSUMPTION_SOURCE_BOTH,
                    ],
                    translation_key="ml_consumption_source",
                )
            ),
            vol.Optional(const.OCTOPUS_MPN, default=octopus_mpan): cv.string,
            vol.Optional(
                const.OCTOPUS_METER_SERIAL, default=octopus_meter_serial
            ): cv.string,
            vol.Optional(
                const.ML_TRAINING_LOOKBACK_DAYS, default=lookback_days
            ): vol.All(vol.Coerce(int), vol.Range(min=14, max=730)),
        }
    )


_TARIFF_CODE_RE = re.compile(r"^E-[12]R-[A-Z0-9\-]+-[A-Z]$")
_MAX_TARIFFS = 6


def _region_from_tariff_code(tariff_code: str) -> str:
    """Extract the single-letter region suffix from an Octopus tariff code.

    e.g. 'E-1R-AGILE-FLEX-22-11-25-B' -> 'B'
    """
    return tariff_code.split("-")[-1]


def _tariff_comparison_enable_schema(enabled: bool = False) -> vol.Schema:
    """Schema for the tariff comparison enable/disable toggle step."""
    return vol.Schema(
        {
            vol.Optional(
                const.TARIFF_COMPARISON_ENABLED, default=enabled
            ): BooleanSelector(),
        }
    )


def _tariff_comparison_pick_schema(
    options: list[dict], current_codes: list[str]
) -> vol.Schema:
    """Schema for the tariff picker multi-select step."""
    return vol.Schema(
        {
            vol.Optional(
                const.TARIFF_COMPARISON_TARIFFS, default=current_codes
            ): SelectSelector(SelectSelectorConfig(options=options, multiple=True)),
        }
    )


def _validate_tariff_json(tariffs_json: str) -> list[str]:
    """Validate the tariff comparison JSON string.

    Returns a list of error messages; empty list = valid.
    """
    errors: list[str] = []
    if not tariffs_json.strip():
        return errors  # empty is OK (feature will be disabled)
    try:
        tariffs = json.loads(tariffs_json)
    except json.JSONDecodeError as exc:
        return [f"Invalid JSON: {exc}"]

    if not isinstance(tariffs, list):
        return ["Tariff list must be a JSON array"]

    if len(tariffs) > _MAX_TARIFFS:
        errors.append(f"Maximum {_MAX_TARIFFS} tariff entries allowed")

    names_seen: set[str] = set()
    for i, entry in enumerate(tariffs):
        name = entry.get("name", f"entry {i + 1}")
        if name in names_seen:
            errors.append(f"Duplicate tariff name '{name}' — names must be unique")
        names_seen.add(name)

        import_code = entry.get("import_tariff_code", "")
        if import_code and not _TARIFF_CODE_RE.match(import_code):
            errors.append(
                f"'{import_code}' does not match expected tariff code format "
                f"(e.g. E-1R-AGILE-FLEX-22-11-25-B)"
            )

    return errors


async def _fetch_available_tariffs(
    session: aiohttp.ClientSession, region_letter: str
) -> list[dict]:
    """Fetch available Octopus import tariff products and convert to SelectSelector options.

    Uses the public (no-auth) Octopus products API. Filters out prepay, business,
    and restricted products. Each result maps to a regional tariff code of the form
    E-1R-{product_code}-{region_letter}.
    """
    url = f"{OCTOPUS_API_BASE}/products/"
    params: dict = {"is_prepay": "false", "is_business": "false"}
    options: list[dict] = []

    while url:
        try:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to fetch Octopus products: %s", exc)
            break

        for product in data.get("results", []):
            if product.get("is_restricted", False):
                continue
            if product.get("direction", "IMPORT") == "EXPORT":
                continue
            code = product.get("code", "")
            if not code:
                continue
            name = product.get("display_name") or product.get("full_name") or code
            tariff_code = f"E-1R-{code}-{region_letter}"
            options.append({"label": name, "value": tariff_code})

        url = data.get("next")
        params = {}

    return options


def _tariff_codes_from_stored_json(tariffs_json: str) -> list[str]:
    """Extract import_tariff_code values from the stored JSON string.

    Handles both the old dict-list format and a bare list of code strings.
    """
    if not tariffs_json:
        return []
    try:
        data = json.loads(tariffs_json)
        if isinstance(data, list):
            codes = []
            for item in data:
                if isinstance(item, str):
                    codes.append(item)
                elif isinstance(item, dict):
                    code = item.get("import_tariff_code", "")
                    if code:
                        codes.append(code)
            return codes
    except json.JSONDecodeError, TypeError:
        pass
    return []


def _tariff_codes_to_stored_json(codes: list[str]) -> str:
    """Convert a list of selected tariff codes to the coordinator's JSON format."""
    return json.dumps(
        [
            {"import_tariff_code": code, "name": code, "is_current": i == 0}
            for i, code in enumerate(codes)
        ]
    )


def estimate_heat_loss(
    floor_area: float, age: str, wall_type: str, glazing: str
) -> float:
    """Estimate building heat loss (W/°C) from construction characteristics."""
    age_factor = {
        "pre_1930": 3.0,
        "1930_1975": 2.5,
        "1975_2000": 1.8,
        "post_2000": 1.2,
    }.get(age, 2.0)
    wall_factor = {
        "solid_uninsulated": 1.4,
        "solid_insulated": 0.8,
        "cavity_uninsulated": 1.0,
        "cavity_insulated": 0.6,
        "modern_insulated": 0.4,
    }.get(wall_type, 1.0)
    glazing_factor = {
        "single": 1.3,
        "double": 1.0,
        "triple": 0.8,
    }.get(glazing, 1.0)
    return round(floor_area * age_factor * wall_factor * glazing_factor, 1)


# ─────────────────────────── initial config flow ─────────────────────────────


class BatteryChargCalculatorConfigFlow(config_entries.ConfigFlow, domain=const.DOMAIN):
    """Multi-step config flow for Battery Charge Calculator – initial setup.

    Step 1  (user)                  – API keys, inverter, battery, base load
    Step 2  (heating)               – choose heating type: none / interpolation / electric
    Step 3a (heating_interpolation) – known (temp, kWh) points  [interpolation only]
    Step 3b (heating_electric)      – COP + indoor temp          [electric only]
    Step 4  (heat_loss_method)      – known value OR estimate from building properties
    Step 5a (heat_loss_known)       – enter W/°C directly
    Step 5b (building_estimate)     – building questions → auto-calculated W/°C
    """

    VERSION = 2
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def __init__(self):
        self._main_data: dict = {}
        self._heating_data: dict = {}

    async def async_step_user(self, user_input=None):
        """Step 1 – main settings."""
        await self.async_set_unique_id(const.DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            self._main_data = dict(user_input)
            return await self.async_step_heating()

        return self.async_show_form(step_id="user", data_schema=get_schema())

    async def async_step_heating(self, user_input=None):
        """Step 2 – select heating type."""
        if user_input is not None:
            heating_type = user_input[const.HEATING_TYPE]
            self._heating_data[const.HEATING_TYPE] = heating_type
            if heating_type == const.HEATING_TYPE_NONE:
                return await self.async_step_ml_settings()
            if heating_type == const.HEATING_TYPE_INTERPOLATION:
                return await self.async_step_heating_interpolation()
            return await self.async_step_heating_electric()

        return self.async_show_form(
            step_id="heating",
            data_schema=_heating_type_schema(),
        )

    async def async_step_heating_interpolation(self, user_input=None):
        """Step 3a – known (temperature, energy) points."""
        if user_input is not None:
            self._heating_data[const.HEATING_KNOWN_POINTS] = user_input.get(
                const.HEATING_KNOWN_POINTS, const.DEFAULT_HEATING_KNOWN_POINTS
            )
            return await self.async_step_ml_settings()

        return self.async_show_form(
            step_id="heating_interpolation",
            data_schema=_heating_interpolation_schema(),
        )

    async def async_step_heating_electric(self, user_input=None):
        """Step 3b – COP, indoor temperature, and flow temperature."""
        if user_input is not None:
            self._heating_data.update(
                {
                    const.HEATING_COP: user_input.get(
                        const.HEATING_COP, const.DEFAULT_HEATING_COP
                    ),
                    const.HEATING_INDOOR_TEMP: user_input.get(
                        const.HEATING_INDOOR_TEMP, const.DEFAULT_HEATING_INDOOR_TEMP
                    ),
                    const.HEATING_FLOW_TEMP: user_input.get(
                        const.HEATING_FLOW_TEMP, const.DEFAULT_HEATING_FLOW_TEMP
                    ),
                }
            )
            return await self.async_step_heat_loss_method()

        return self.async_show_form(
            step_id="heating_electric",
            data_schema=_heating_electric_schema(),
        )

    async def async_step_heat_loss_method(self, user_input=None):
        """Step 4 – choose how to supply the building heat loss."""
        if user_input is not None:
            method = user_input[const.HEAT_LOSS_METHOD]
            if method == const.HEAT_LOSS_METHOD_KNOWN:
                return await self.async_step_heat_loss_known()
            if method == const.HEAT_LOSS_METHOD_REPORT:
                return await self.async_step_heat_loss_report()
            return await self.async_step_building_estimate()

        return self.async_show_form(
            step_id="heat_loss_method",
            data_schema=_heat_loss_method_schema(),
        )

    async def async_step_heat_loss_report(self, user_input=None):
        """Step 5b – convert report W-at-design-temp to W/°C coefficient."""
        if user_input is not None:
            watts = float(user_input[const.HEAT_LOSS_REPORT_WATTS])
            outdoor = float(user_input[const.HEAT_LOSS_REPORT_OUTDOOR_TEMP])
            indoor = float(user_input[const.HEAT_LOSS_REPORT_INDOOR_TEMP])
            delta_t = indoor - outdoor
            if delta_t <= 0:
                return self.async_show_form(
                    step_id="heat_loss_report",
                    data_schema=_heat_loss_report_schema(watts, outdoor, indoor),
                    errors={"base": "invalid_design_temps"},
                )
            self._heating_data.update(
                {
                    const.HEATING_HEAT_LOSS: round(watts / delta_t, 2),
                    const.HEAT_LOSS_REPORT_WATTS: watts,
                    const.HEAT_LOSS_REPORT_OUTDOOR_TEMP: outdoor,
                    const.HEAT_LOSS_REPORT_INDOOR_TEMP: indoor,
                }
            )
            return await self.async_step_ml_settings()

        return self.async_show_form(
            step_id="heat_loss_report",
            data_schema=_heat_loss_report_schema(),
        )

    async def async_step_heat_loss_known(self, user_input=None):
        """Step 5c – enter heat loss value directly as W/°C."""
        if user_input is not None:
            self._heating_data[const.HEATING_HEAT_LOSS] = float(
                user_input[const.HEATING_HEAT_LOSS]
            )
            return await self.async_step_ml_settings()

        return self.async_show_form(
            step_id="heat_loss_known",
            data_schema=_heat_loss_known_schema(
                heat_loss=self._heating_data.get(
                    const.HEATING_HEAT_LOSS, const.DEFAULT_HEATING_HEAT_LOSS
                )
            ),
        )

    async def async_step_building_estimate(self, user_input=None):
        """Step 5b – estimate heat loss from building characteristics."""
        if user_input is not None:
            floor_area = float(
                user_input.get(
                    const.BUILDING_FLOOR_AREA, const.DEFAULT_BUILDING_FLOOR_AREA
                )
            )
            age = user_input.get(const.BUILDING_AGE, const.DEFAULT_BUILDING_AGE)
            wall_type = user_input.get(
                const.BUILDING_WALL_TYPE, const.DEFAULT_BUILDING_WALL_TYPE
            )
            glazing = user_input.get(
                const.BUILDING_GLAZING, const.DEFAULT_BUILDING_GLAZING
            )
            self._heating_data.update(
                {
                    const.HEATING_HEAT_LOSS: estimate_heat_loss(
                        floor_area, age, wall_type, glazing
                    ),
                    const.BUILDING_FLOOR_AREA: floor_area,
                    const.BUILDING_AGE: age,
                    const.BUILDING_WALL_TYPE: wall_type,
                    const.BUILDING_GLAZING: glazing,
                }
            )
            return await self.async_step_ml_settings()

        return self.async_show_form(
            step_id="building_estimate",
            data_schema=_building_estimate_schema(),
        )

    async def async_step_ml_settings(self, user_input=None):
        """ML power estimation settings step.

        Configures the optional ML feature.  All fields default to off/defaults
        so existing users are unaffected when this step is introduced (D-16).
        """
        if user_input is not None:
            # Update both _heating_data and options to ensure persistence
            ml_settings = {
                const.ML_ENABLED: user_input.get(const.ML_ENABLED, False),
                const.ML_SERVICE_URL: user_input.get(
                    const.ML_SERVICE_URL, const.DEFAULT_ML_SERVICE_URL
                ),
                const.ML_SERVICE_API_KEY: user_input.get(
                    const.ML_SERVICE_API_KEY, const.DEFAULT_ML_SERVICE_API_KEY
                ),
                const.ML_SERVICE_TLS_FINGERPRINT: user_input.get(
                    const.ML_SERVICE_TLS_FINGERPRINT,
                    const.DEFAULT_ML_SERVICE_TLS_FINGERPRINT,
                ),
                const.ML_CONSUMPTION_SOURCE: user_input.get(
                    const.ML_CONSUMPTION_SOURCE, const.DEFAULT_ML_CONSUMPTION_SOURCE
                ),
                const.OCTOPUS_MPN: user_input.get(const.OCTOPUS_MPN, ""),
                const.OCTOPUS_METER_SERIAL: user_input.get(
                    const.OCTOPUS_METER_SERIAL, const.DEFAULT_OCTOPUS_METER_SERIAL
                ),
                const.ML_TRAINING_LOOKBACK_DAYS: user_input.get(
                    const.ML_TRAINING_LOOKBACK_DAYS,
                    const.DEFAULT_ML_TRAINING_LOOKBACK_DAYS,
                ),
            }
            self._heating_data.update(ml_settings)
            # Also update self.options to ensure persistence if options flow is used later
            if hasattr(self, "options"):
                self.options.update(ml_settings)
            return await self.async_step_tariff_comparison()

        return self.async_show_form(
            step_id="ml_settings",
            data_schema=_ml_settings_schema(
                ml_enabled=self._heating_data.get(const.ML_ENABLED, False),
                service_url=self._heating_data.get(
                    const.ML_SERVICE_URL, const.DEFAULT_ML_SERVICE_URL
                ),
                api_key=self._heating_data.get(
                    const.ML_SERVICE_API_KEY, const.DEFAULT_ML_SERVICE_API_KEY
                ),
                tls_fingerprint=self._heating_data.get(
                    const.ML_SERVICE_TLS_FINGERPRINT,
                    const.DEFAULT_ML_SERVICE_TLS_FINGERPRINT,
                ),
                consumption_source=self._heating_data.get(
                    const.ML_CONSUMPTION_SOURCE, const.DEFAULT_ML_CONSUMPTION_SOURCE
                ),
                octopus_mpan=self._heating_data.get(const.OCTOPUS_MPN, ""),
                octopus_meter_serial=self._heating_data.get(
                    const.OCTOPUS_METER_SERIAL, const.DEFAULT_OCTOPUS_METER_SERIAL
                ),
                lookback_days=self._heating_data.get(
                    const.ML_TRAINING_LOOKBACK_DAYS,
                    const.DEFAULT_ML_TRAINING_LOOKBACK_DAYS,
                ),
            ),
        )

    async def async_step_tariff_comparison(self, user_input=None):
        """Step: enable/disable tariff comparison.

        A simple toggle. If enabled, proceeds to the tariff picker step
        which fetches available tariffs from the Octopus API and lets the
        user select from a graphical list.
        """
        if user_input is not None:
            enabled = user_input.get(const.TARIFF_COMPARISON_ENABLED, False)
            self._heating_data[const.TARIFF_COMPARISON_ENABLED] = enabled
            if enabled:
                return await self.async_step_tariff_comparison_pick()
            # Feature disabled — store empty list and finish
            self._heating_data[const.TARIFF_COMPARISON_TARIFFS] = "[]"
            return self._create_entry()

        return self.async_show_form(
            step_id="tariff_comparison",
            data_schema=_tariff_comparison_enable_schema(
                enabled=self._heating_data.get(const.TARIFF_COMPARISON_ENABLED, False),
            ),
        )

    async def async_step_tariff_comparison_pick(self, user_input=None):
        """Step: pick tariffs from a live list fetched from Octopus.

        Fetches the product catalogue on first display (user_input is None).
        The user's current tariff is pre-selected. On submit, stores the
        selection as the coordinator-compatible JSON format.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            selected: list[str] = user_input.get(const.TARIFF_COMPARISON_TARIFFS, [])
            if not selected:
                errors[const.TARIFF_COMPARISON_TARIFFS] = "select_at_least_one"
            else:
                self._heating_data[const.TARIFF_COMPARISON_TARIFFS] = (
                    _tariff_codes_to_stored_json(selected)
                )
                return self._create_entry()

        # --- Fetch available tariffs from Octopus ---
        session = aiohttp_client.async_get_clientsession(self.hass)
        available_options: list[dict] = []
        current_code: str | None = None

        try:
            api_key = self._main_data.get(const.OCTOPUS_APIKEY, "")
            account = self._main_data.get(const.OCTOPUS_ACCOUNT_NUMBER, "")
            if api_key and account:
                client = OctopusAgileRatesClient(api_key, account)
                await client._find_current_tariffs(session)  # noqa: SLF001
                current_code = client.import_tariff_code
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Could not resolve current tariff code: %s", exc)

        region = current_code[-1] if current_code else "A"
        try:
            available_options = await _fetch_available_tariffs(session, region)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Could not fetch tariff list: %s", exc)
            errors["base"] = "tariff_fetch_failed"

        # Pre-select current tariff (and any previously saved codes)
        existing_codes = _tariff_codes_from_stored_json(
            self._heating_data.get(const.TARIFF_COMPARISON_TARIFFS, "")
        )
        default_codes = existing_codes or ([current_code] if current_code else [])

        # Ensure pre-selected codes appear in the options list (in case the
        # live fetch failed or the saved code is not in the current catalogue)
        option_values = {o["value"] for o in available_options}
        for code in default_codes:
            if code and code not in option_values:
                available_options.insert(
                    0,
                    {"label": f"{code} (your current tariff)", "value": code},
                )

        return self.async_show_form(
            step_id="tariff_comparison_pick",
            data_schema=_tariff_comparison_pick_schema(
                options=available_options,
                current_codes=default_codes,
            ),
            errors=errors,
            description_placeholders={
                "region": region,
            },
        )

        options = {**self._main_data, **self._heating_data}
        return self.async_create_entry(title=const.TITLE, data={}, options=options)

    def _create_entry(self):
        """Finalise and create the config entry (called from tariff steps)."""
        options = {**self._main_data, **self._heating_data}
        return self.async_create_entry(title=const.TITLE, data={}, options=options)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return BatteryChargCalculatorFlowHandler(config_entry)


# ─────────────────────────── options flow ────────────────────────────────────


class BatteryChargCalculatorFlowHandler(config_entries.OptionsFlow):
    """Multi-step options flow.

    Same step structure as the initial config flow.
    Step 1 (init)              – main settings
    Step 2 (heating)           – heating type selector
    Step 3a/3b/4               – heating-specific fields
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self.options = dict(config_entry.options)

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Step 1 – main / inverter / grid settings."""
        errors = {}

        if user_input is not None:
            try:
                # Strip legacy keys from older integration versions
                self.options.pop(const.OCTOPUS_EXPORT_MPN, None)
                self.options.pop(const.OCTOPUS_MPN, None)
                self.options.update(
                    {
                        const.GIVENERGY_SERIAL_NUMBER: user_input[
                            const.GIVENERGY_SERIAL_NUMBER
                        ],
                        const.GIVENERGY_API_TOKEN: user_input[
                            const.GIVENERGY_API_TOKEN
                        ],
                        const.OCTOPUS_ACCOUNT_NUMBER: user_input[
                            const.OCTOPUS_ACCOUNT_NUMBER
                        ],
                        const.OCTOPUS_APIKEY: user_input[const.OCTOPUS_APIKEY],
                        const.SIMULATE_ONLY: user_input[const.SIMULATE_ONLY],
                        const.INVERTER_SIZE_KW: user_input[const.INVERTER_SIZE_KW],
                        const.INVERTER_EFFICIENCY: user_input[
                            const.INVERTER_EFFICIENCY
                        ],
                        const.BATTERY_CAPACITY_KWH: user_input.get(
                            const.BATTERY_CAPACITY_KWH,
                            const.DEFAULT_BATTERY_CAPACITY_KWH,
                        ),
                        const.BASE_LOAD_KWH_30MIN: user_input.get(
                            const.BASE_LOAD_KWH_30MIN,
                            const.DEFAULT_BASE_LOAD_KWH_30MIN,
                        ),
                    }
                )
                return await self.async_step_heating()
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="init",
            data_schema=get_schema(
                serialno=self.options.get(const.GIVENERGY_SERIAL_NUMBER, ""),
                apitoken=self.options.get(const.GIVENERGY_API_TOKEN, ""),
                octopus_account_number=self.options.get(
                    const.OCTOPUS_ACCOUNT_NUMBER, ""
                ),
                octopus_api_key=self.options.get(const.OCTOPUS_APIKEY, ""),
                simulate=self.options.get(const.SIMULATE_ONLY, False),
                inverter_size_kw=self.options.get(
                    const.INVERTER_SIZE_KW, const.DEFAULT_INVERTER_SIZE_KW
                ),
                inverter_efficiency=self.options.get(
                    const.INVERTER_EFFICIENCY, const.DEFAULT_INVERTER_EFFICIENCY
                ),
                battery_capacity_kwh=self.options.get(
                    const.BATTERY_CAPACITY_KWH, const.DEFAULT_BATTERY_CAPACITY_KWH
                ),
                base_load=self.options.get(
                    const.BASE_LOAD_KWH_30MIN, const.DEFAULT_BASE_LOAD_KWH_30MIN
                ),
            ),
            errors=errors,
        )

    async def async_step_heating(self, user_input=None):
        """Step 2 – select heating type."""
        if user_input is not None:
            heating_type = user_input[const.HEATING_TYPE]
            self.options[const.HEATING_TYPE] = heating_type
            if heating_type == const.HEATING_TYPE_NONE:
                return await self.async_step_ml_settings()
            if heating_type == const.HEATING_TYPE_INTERPOLATION:
                return await self.async_step_heating_interpolation()
            return await self.async_step_heating_electric()

        return self.async_show_form(
            step_id="heating",
            data_schema=_heating_type_schema(
                heating_type=self.options.get(
                    const.HEATING_TYPE, const.DEFAULT_HEATING_TYPE
                )
            ),
        )

    async def async_step_heating_interpolation(self, user_input=None):
        """Step 3a – known (temperature, energy) points."""
        if user_input is not None:
            self.options[const.HEATING_KNOWN_POINTS] = user_input.get(
                const.HEATING_KNOWN_POINTS, const.DEFAULT_HEATING_KNOWN_POINTS
            )
            return await self.async_step_ml_settings()

        return self.async_show_form(
            step_id="heating_interpolation",
            data_schema=_heating_interpolation_schema(
                known_points=self.options.get(
                    const.HEATING_KNOWN_POINTS, const.DEFAULT_HEATING_KNOWN_POINTS
                )
            ),
        )

    async def async_step_heating_electric(self, user_input=None):
        """Step 3b – COP, indoor temperature, and flow temperature."""
        if user_input is not None:
            self.options.update(
                {
                    const.HEATING_COP: user_input.get(
                        const.HEATING_COP, const.DEFAULT_HEATING_COP
                    ),
                    const.HEATING_INDOOR_TEMP: user_input.get(
                        const.HEATING_INDOOR_TEMP, const.DEFAULT_HEATING_INDOOR_TEMP
                    ),
                    const.HEATING_FLOW_TEMP: user_input.get(
                        const.HEATING_FLOW_TEMP, const.DEFAULT_HEATING_FLOW_TEMP
                    ),
                }
            )
            return await self.async_step_heat_loss_method()

        return self.async_show_form(
            step_id="heating_electric",
            data_schema=_heating_electric_schema(
                cop=self.options.get(const.HEATING_COP, const.DEFAULT_HEATING_COP),
                indoor_temp=self.options.get(
                    const.HEATING_INDOOR_TEMP, const.DEFAULT_HEATING_INDOOR_TEMP
                ),
                flow_temp=self.options.get(
                    const.HEATING_FLOW_TEMP, const.DEFAULT_HEATING_FLOW_TEMP
                ),
            ),
        )

    async def async_step_heat_loss_method(self, user_input=None):
        """Step 4 – choose how to supply the building heat loss."""
        if user_input is not None:
            method = user_input[const.HEAT_LOSS_METHOD]
            if method == const.HEAT_LOSS_METHOD_KNOWN:
                return await self.async_step_heat_loss_known()
            if method == const.HEAT_LOSS_METHOD_REPORT:
                return await self.async_step_heat_loss_report()
            return await self.async_step_building_estimate()

        # Pre-select the method based on previously stored data
        if self.options.get(const.BUILDING_FLOOR_AREA):
            current_method = const.HEAT_LOSS_METHOD_ESTIMATE
        elif self.options.get(const.HEAT_LOSS_REPORT_WATTS):
            current_method = const.HEAT_LOSS_METHOD_REPORT
        else:
            current_method = const.HEAT_LOSS_METHOD_KNOWN
        return self.async_show_form(
            step_id="heat_loss_method",
            data_schema=_heat_loss_method_schema(method=current_method),
        )

    async def async_step_heat_loss_report(self, user_input=None):
        """Step 5b – convert report W-at-design-temp to W/°C coefficient."""
        if user_input is not None:
            watts = float(user_input[const.HEAT_LOSS_REPORT_WATTS])
            outdoor = float(user_input[const.HEAT_LOSS_REPORT_OUTDOOR_TEMP])
            indoor = float(user_input[const.HEAT_LOSS_REPORT_INDOOR_TEMP])
            delta_t = indoor - outdoor
            if delta_t <= 0:
                return self.async_show_form(
                    step_id="heat_loss_report",
                    data_schema=_heat_loss_report_schema(watts, outdoor, indoor),
                    errors={"base": "invalid_design_temps"},
                )
            self.options.update(
                {
                    const.HEATING_HEAT_LOSS: round(watts / delta_t, 2),
                    const.HEAT_LOSS_REPORT_WATTS: watts,
                    const.HEAT_LOSS_REPORT_OUTDOOR_TEMP: outdoor,
                    const.HEAT_LOSS_REPORT_INDOOR_TEMP: indoor,
                }
            )
            return await self.async_step_ml_settings()

        return self.async_show_form(
            step_id="heat_loss_report",
            data_schema=_heat_loss_report_schema(
                watts=self.options.get(
                    const.HEAT_LOSS_REPORT_WATTS,
                    const.DEFAULT_HEAT_LOSS_REPORT_WATTS,
                ),
                outdoor_temp=self.options.get(
                    const.HEAT_LOSS_REPORT_OUTDOOR_TEMP,
                    const.DEFAULT_HEAT_LOSS_REPORT_OUTDOOR_TEMP,
                ),
                indoor_temp=self.options.get(
                    const.HEAT_LOSS_REPORT_INDOOR_TEMP,
                    const.DEFAULT_HEAT_LOSS_REPORT_INDOOR_TEMP,
                ),
            ),
        )

    async def async_step_heat_loss_known(self, user_input=None):
        """Step 5c – enter heat loss value directly as W/°C."""
        if user_input is not None:
            self.options[const.HEATING_HEAT_LOSS] = float(
                user_input[const.HEATING_HEAT_LOSS]
            )
            return await self.async_step_ml_settings()

        return self.async_show_form(
            step_id="heat_loss_known",
            data_schema=_heat_loss_known_schema(
                heat_loss=self.options.get(
                    const.HEATING_HEAT_LOSS, const.DEFAULT_HEATING_HEAT_LOSS
                )
            ),
        )

    async def async_step_building_estimate(self, user_input=None):
        """Step 5b – estimate heat loss from building characteristics."""
        if user_input is not None:
            floor_area = float(
                user_input.get(
                    const.BUILDING_FLOOR_AREA, const.DEFAULT_BUILDING_FLOOR_AREA
                )
            )
            age = user_input.get(const.BUILDING_AGE, const.DEFAULT_BUILDING_AGE)
            wall_type = user_input.get(
                const.BUILDING_WALL_TYPE, const.DEFAULT_BUILDING_WALL_TYPE
            )
            glazing = user_input.get(
                const.BUILDING_GLAZING, const.DEFAULT_BUILDING_GLAZING
            )
            self.options.update(
                {
                    const.HEATING_HEAT_LOSS: estimate_heat_loss(
                        floor_area, age, wall_type, glazing
                    ),
                    const.BUILDING_FLOOR_AREA: floor_area,
                    const.BUILDING_AGE: age,
                    const.BUILDING_WALL_TYPE: wall_type,
                    const.BUILDING_GLAZING: glazing,
                }
            )
            return await self.async_step_ml_settings()

        return self.async_show_form(
            step_id="building_estimate",
            data_schema=_building_estimate_schema(
                floor_area=self.options.get(
                    const.BUILDING_FLOOR_AREA, const.DEFAULT_BUILDING_FLOOR_AREA
                ),
                age=self.options.get(const.BUILDING_AGE, const.DEFAULT_BUILDING_AGE),
                wall_type=self.options.get(
                    const.BUILDING_WALL_TYPE, const.DEFAULT_BUILDING_WALL_TYPE
                ),
                glazing=self.options.get(
                    const.BUILDING_GLAZING, const.DEFAULT_BUILDING_GLAZING
                ),
            ),
        )

    async def async_step_ml_settings(self, user_input=None):
        """ML power estimation settings step.

        Configures the optional ML feature.  All fields default to off/defaults
        so existing users are unaffected when this step is introduced (D-16).
        """
        if user_input is not None:
            self.options.update(
                {
                    const.ML_ENABLED: user_input.get(const.ML_ENABLED, False),
                    const.ML_SERVICE_URL: user_input.get(
                        const.ML_SERVICE_URL, const.DEFAULT_ML_SERVICE_URL
                    ),
                    const.ML_SERVICE_API_KEY: user_input.get(
                        const.ML_SERVICE_API_KEY, const.DEFAULT_ML_SERVICE_API_KEY
                    ),
                    const.ML_SERVICE_TLS_FINGERPRINT: user_input.get(
                        const.ML_SERVICE_TLS_FINGERPRINT,
                        const.DEFAULT_ML_SERVICE_TLS_FINGERPRINT,
                    ),
                    const.ML_CONSUMPTION_SOURCE: user_input.get(
                        const.ML_CONSUMPTION_SOURCE, const.DEFAULT_ML_CONSUMPTION_SOURCE
                    ),
                    const.OCTOPUS_MPN: user_input.get(const.OCTOPUS_MPN, ""),
                    const.OCTOPUS_METER_SERIAL: user_input.get(
                        const.OCTOPUS_METER_SERIAL, const.DEFAULT_OCTOPUS_METER_SERIAL
                    ),
                    const.ML_TRAINING_LOOKBACK_DAYS: user_input.get(
                        const.ML_TRAINING_LOOKBACK_DAYS,
                        const.DEFAULT_ML_TRAINING_LOOKBACK_DAYS,
                    ),
                }
            )
            return await self.async_step_tariff_comparison()

        return self.async_show_form(
            step_id="ml_settings",
            data_schema=_ml_settings_schema(
                ml_enabled=self.options.get(const.ML_ENABLED, False),
                service_url=self.options.get(
                    const.ML_SERVICE_URL, const.DEFAULT_ML_SERVICE_URL
                ),
                api_key=self.options.get(
                    const.ML_SERVICE_API_KEY, const.DEFAULT_ML_SERVICE_API_KEY
                ),
                tls_fingerprint=self.options.get(
                    const.ML_SERVICE_TLS_FINGERPRINT,
                    const.DEFAULT_ML_SERVICE_TLS_FINGERPRINT,
                ),
                consumption_source=self.options.get(
                    const.ML_CONSUMPTION_SOURCE, const.DEFAULT_ML_CONSUMPTION_SOURCE
                ),
                octopus_mpan=self.options.get(const.OCTOPUS_MPN, ""),
                octopus_meter_serial=self.options.get(
                    const.OCTOPUS_METER_SERIAL, const.DEFAULT_OCTOPUS_METER_SERIAL
                ),
                lookback_days=self.options.get(
                    const.ML_TRAINING_LOOKBACK_DAYS,
                    const.DEFAULT_ML_TRAINING_LOOKBACK_DAYS,
                ),
            ),
        )

    async def async_step_tariff_comparison(self, user_input=None):
        """Step: enable/disable tariff comparison (options flow)."""
        if user_input is not None:
            enabled = user_input.get(const.TARIFF_COMPARISON_ENABLED, False)
            self.options[const.TARIFF_COMPARISON_ENABLED] = enabled
            if enabled:
                return await self.async_step_tariff_comparison_pick()
            self.options[const.TARIFF_COMPARISON_TARIFFS] = "[]"
            return self._save_and_exit()

        return self.async_show_form(
            step_id="tariff_comparison",
            data_schema=_tariff_comparison_enable_schema(
                enabled=self.options.get(const.TARIFF_COMPARISON_ENABLED, False),
            ),
        )

    async def async_step_tariff_comparison_pick(self, user_input=None):
        """Step: pick tariffs from a live Octopus product list (options flow)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected: list[str] = user_input.get(const.TARIFF_COMPARISON_TARIFFS, [])
            if not selected:
                errors[const.TARIFF_COMPARISON_TARIFFS] = "select_at_least_one"
            else:
                self.options[const.TARIFF_COMPARISON_TARIFFS] = (
                    _tariff_codes_to_stored_json(selected)
                )
                return self._save_and_exit()

        session = aiohttp_client.async_get_clientsession(self.hass)
        available_options: list[dict] = []
        current_code: str | None = None

        try:
            api_key = self.options.get(const.OCTOPUS_APIKEY, "")
            account = self.options.get(const.OCTOPUS_ACCOUNT_NUMBER, "")
            if api_key and account:
                client = OctopusAgileRatesClient(api_key, account)
                await client._find_current_tariffs(session)  # noqa: SLF001
                current_code = client.import_tariff_code
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Could not resolve current tariff code: %s", exc)

        region = current_code[-1] if current_code else "A"
        try:
            available_options = await _fetch_available_tariffs(session, region)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Could not fetch tariff list: %s", exc)
            errors["base"] = "tariff_fetch_failed"

        existing_codes = _tariff_codes_from_stored_json(
            self.options.get(const.TARIFF_COMPARISON_TARIFFS, "")
        )
        default_codes = existing_codes or ([current_code] if current_code else [])

        option_values = {o["value"] for o in available_options}
        for code in default_codes:
            if code and code not in option_values:
                available_options.insert(
                    0,
                    {"label": f"{code} (your current tariff)", "value": code},
                )

        return self.async_show_form(
            step_id="tariff_comparison_pick",
            data_schema=_tariff_comparison_pick_schema(
                options=available_options,
                current_codes=default_codes,
            ),
            errors=errors,
            description_placeholders={"region": region},
        )

    def _save_and_exit(self):
        # async_create_entry saves self.options as the config entry's new options
        # and fires the update_listener (which triggers async_reload in __init__.py).
        # Do NOT also call async_update_entry — that fires the listener a second time,
        # causing a double-reload race condition.
        return self.async_create_entry(title=const.TITLE, data=self.options)
