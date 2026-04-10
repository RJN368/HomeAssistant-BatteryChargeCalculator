"""Unit tests for the config_flow module."""

from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from custom_components.battery_charge_calculator import const
from custom_components.battery_charge_calculator.config_flow import (
    BatteryChargCalculatorConfigFlow,
    BatteryChargCalculatorFlowHandler,
    get_schema,
    estimate_heat_loss,
)


# ---------------------------------------------------------------------------
# get_schema helper  (main/grid step only — heating is a separate step)
# ---------------------------------------------------------------------------


class TestGetSchema:
    def test_returns_schema_with_main_fields(self):
        schema = get_schema()
        keys = [str(k) for k in schema.schema]
        assert any(const.GIVENERGY_SERIAL_NUMBER in k for k in keys)
        assert any(const.GIVENERGY_API_TOKEN in k for k in keys)
        assert any(const.OCTOPUS_ACCOUNT_NUMBER in k for k in keys)
        assert any(const.OCTOPUS_APIKEY in k for k in keys)
        assert any(const.SIMULATE_ONLY in k for k in keys)
        assert any(const.INVERTER_SIZE_KW in k for k in keys)
        assert any(const.INVERTER_EFFICIENCY in k for k in keys)
        assert any(const.BATTERY_CAPACITY_KWH in k for k in keys)
        assert any(const.BASE_LOAD_KWH_30MIN in k for k in keys)

    def test_battery_capacity_field_is_optional(self):
        """battery_capacity_kwh is Optional — omitting it uses the default."""
        schema = get_schema()
        result = schema(
            {
                const.GIVENERGY_SERIAL_NUMBER: "SN",
                const.GIVENERGY_API_TOKEN: "TOKEN",
                const.OCTOPUS_ACCOUNT_NUMBER: "A-1234",
                const.OCTOPUS_APIKEY: "KEY",
                const.SIMULATE_ONLY: False,
                const.INVERTER_SIZE_KW: const.DEFAULT_INVERTER_SIZE_KW,
                const.INVERTER_EFFICIENCY: const.DEFAULT_INVERTER_EFFICIENCY,
                # BATTERY_CAPACITY_KWH intentionally omitted
            }
        )
        assert result[const.BATTERY_CAPACITY_KWH] == const.DEFAULT_BATTERY_CAPACITY_KWH

    def test_battery_capacity_custom_value_accepted(self):
        """A custom battery_capacity_kwh value is accepted and preserved."""
        schema = get_schema()
        result = schema(
            {
                const.GIVENERGY_SERIAL_NUMBER: "SN",
                const.GIVENERGY_API_TOKEN: "TOKEN",
                const.OCTOPUS_ACCOUNT_NUMBER: "A-1234",
                const.OCTOPUS_APIKEY: "KEY",
                const.SIMULATE_ONLY: False,
                const.INVERTER_SIZE_KW: const.DEFAULT_INVERTER_SIZE_KW,
                const.INVERTER_EFFICIENCY: const.DEFAULT_INVERTER_EFFICIENCY,
                const.BATTERY_CAPACITY_KWH: 12.0,
            }
        )
        assert result[const.BATTERY_CAPACITY_KWH] == 12.0

    def test_inverter_fields_use_supplied_defaults(self):
        schema = get_schema(
            serialno="SN001",
            apitoken="TOKEN",
            octopus_account_number="A-1234",
            octopus_api_key="KEY",
            simulate=False,
            inverter_size_kw=5.0,
            inverter_efficiency=0.85,
        )
        result = schema(
            {
                const.GIVENERGY_SERIAL_NUMBER: "SN001",
                const.GIVENERGY_API_TOKEN: "TOKEN",
                const.OCTOPUS_ACCOUNT_NUMBER: "A-1234",
                const.OCTOPUS_APIKEY: "KEY",
                const.SIMULATE_ONLY: False,
                const.INVERTER_SIZE_KW: 5.0,
                const.INVERTER_EFFICIENCY: 0.85,
            }
        )
        assert result[const.INVERTER_SIZE_KW] == 5.0
        assert result[const.INVERTER_EFFICIENCY] == 0.85

    def test_defaults_populated(self):
        schema = get_schema(
            serialno="SN001",
            apitoken="TOKEN",
            octopus_account_number="A-1234",
            octopus_api_key="KEY",
            simulate=True,
        )
        result = schema(
            {
                const.GIVENERGY_SERIAL_NUMBER: "SN001",
                const.GIVENERGY_API_TOKEN: "TOKEN",
                const.OCTOPUS_ACCOUNT_NUMBER: "A-1234",
                const.OCTOPUS_APIKEY: "KEY",
                const.SIMULATE_ONLY: True,
                const.INVERTER_SIZE_KW: const.DEFAULT_INVERTER_SIZE_KW,
                const.INVERTER_EFFICIENCY: const.DEFAULT_INVERTER_EFFICIENCY,
            }
        )
        assert result[const.GIVENERGY_SERIAL_NUMBER] == "SN001"
        assert result[const.SIMULATE_ONLY] is True

    def test_schema_rejects_wrong_type_for_boolean(self):
        """simulate_only must be a boolean; a non-coercible string should fail."""
        import voluptuous as vol

        schema = get_schema()
        with pytest.raises((vol.Invalid, Exception)):
            schema(
                {
                    const.GIVENERGY_SERIAL_NUMBER: "SN",
                    const.GIVENERGY_API_TOKEN: "TOKEN",
                    const.OCTOPUS_ACCOUNT_NUMBER: "A-1234",
                    const.OCTOPUS_APIKEY: "KEY",
                    const.SIMULATE_ONLY: "not_a_bool",  # invalid
                }
            )

    def test_legacy_heating_kwargs_are_ignored(self):
        """Old heating params passed to get_schema() are silently ignored."""
        schema = get_schema(heating_type="custom", heating_cop=3.0)
        keys = [str(k) for k in schema.schema]
        assert not any(const.HEATING_TYPE in k for k in keys)
        assert not any(const.HEATING_COP in k for k in keys)


# ---------------------------------------------------------------------------
# estimate_heat_loss helper
# ---------------------------------------------------------------------------


class TestEstimateHeatLoss:
    def test_returns_positive_value(self):
        result = estimate_heat_loss(100, "1930_1975", "cavity_uninsulated", "double")
        assert result > 0

    def test_larger_house_higher_heat_loss(self):
        small = estimate_heat_loss(50, "1930_1975", "cavity_uninsulated", "double")
        large = estimate_heat_loss(200, "1930_1975", "cavity_uninsulated", "double")
        assert large > small

    def test_modern_insulated_lower_than_solid_uninsulated(self):
        old = estimate_heat_loss(100, "pre_1930", "solid_uninsulated", "single")
        new = estimate_heat_loss(100, "post_2000", "modern_insulated", "triple")
        assert new < old

    def test_unknown_keys_use_fallback(self):
        result = estimate_heat_loss(
            100, "unknown_age", "unknown_wall", "unknown_glazing"
        )
        assert result > 0


# ---------------------------------------------------------------------------
# BatteryChargCalculatorConfigFlow – step user (Step 1)
# ---------------------------------------------------------------------------


class TestConfigFlowAsyncStepUser:
    def _make_flow(self):
        flow = BatteryChargCalculatorConfigFlow()
        flow.hass = MagicMock()
        flow.context = {"source": "user"}
        flow._async_current_entries = MagicMock(return_value=[])
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow.async_show_form = MagicMock(
            return_value={"type": "form", "step_id": "user"}
        )
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        flow.async_abort = MagicMock(return_value={"type": "abort"})
        return flow

    @pytest.mark.asyncio
    async def test_shows_form_when_no_input(self):
        flow = self._make_flow()
        result = await flow.async_step_user(user_input=None)
        flow.async_show_form.assert_called_once()
        assert flow.async_create_entry.call_count == 0

    @pytest.mark.asyncio
    async def test_stores_main_data_and_transitions_to_heating(self):
        """Step 1 saves main data to _main_data and transitions to heating step."""
        flow = self._make_flow()
        # Mock async_step_heating so we don't need to drive the full chain
        flow.async_step_heating = AsyncMock(
            return_value={"type": "form", "step_id": "heating"}
        )
        user_input = {
            const.GIVENERGY_SERIAL_NUMBER: "SN123",
            const.GIVENERGY_API_TOKEN: "TOKEN",
            const.OCTOPUS_ACCOUNT_NUMBER: "A-ACCT",
            const.OCTOPUS_APIKEY: "OCTKEY",
            const.SIMULATE_ONLY: False,
            const.INVERTER_SIZE_KW: 5.0,
            const.INVERTER_EFFICIENCY: 0.9,
        }
        await flow.async_step_user(user_input=user_input)
        assert flow._main_data[const.GIVENERGY_SERIAL_NUMBER] == "SN123"
        assert flow._main_data[const.INVERTER_SIZE_KW] == 5.0
        flow.async_step_heating.assert_called_once()

    @pytest.mark.asyncio
    async def test_unique_id_set_to_domain(self):
        flow = self._make_flow()
        await flow.async_step_user(user_input=None)
        flow.async_set_unique_id.assert_called_once_with(const.DOMAIN)

    @pytest.mark.asyncio
    async def test_abort_checked_after_unique_id(self):
        flow = self._make_flow()
        await flow.async_step_user(user_input=None)
        flow._abort_if_unique_id_configured.assert_called_once()


# ---------------------------------------------------------------------------
# BatteryChargCalculatorConfigFlow – heating steps
# ---------------------------------------------------------------------------


class TestConfigFlowHeatingSteps:
    def _make_flow(self):
        flow = BatteryChargCalculatorConfigFlow()
        flow.hass = MagicMock()
        flow.async_show_form = MagicMock(return_value={"type": "form"})
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        # The ML settings step is the final step before saving.  Tests that
        # exercise earlier steps should treat it as a passthrough so the flow
        # completes and async_create_entry is called.
        flow.async_step_ml_settings = AsyncMock(
            side_effect=lambda user_input=None: flow._create_entry()
        )
        return flow

    @pytest.mark.asyncio
    async def test_heating_none_creates_entry(self):
        flow = self._make_flow()
        flow._main_data = {const.GIVENERGY_SERIAL_NUMBER: "SN"}
        await flow.async_step_heating(
            user_input={const.HEATING_TYPE: const.HEATING_TYPE_NONE}
        )
        flow.async_create_entry.assert_called_once()
        options = flow.async_create_entry.call_args[1]["options"]
        assert options[const.HEATING_TYPE] == const.HEATING_TYPE_NONE

    @pytest.mark.asyncio
    async def test_heating_interpolation_transitions_to_interpolation_step(self):
        flow = self._make_flow()
        flow._main_data = {}
        flow.async_step_heating_interpolation = AsyncMock(
            return_value={"type": "form", "step_id": "heating_interpolation"}
        )
        await flow.async_step_heating(
            user_input={const.HEATING_TYPE: const.HEATING_TYPE_INTERPOLATION}
        )
        flow.async_step_heating_interpolation.assert_called_once()

    @pytest.mark.asyncio
    async def test_heating_electric_transitions_to_electric_step(self):
        flow = self._make_flow()
        flow._main_data = {}
        flow.async_step_heating_electric = AsyncMock(
            return_value={"type": "form", "step_id": "heating_electric"}
        )
        await flow.async_step_heating(
            user_input={const.HEATING_TYPE: const.HEATING_TYPE_ELECTRIC}
        )
        flow.async_step_heating_electric.assert_called_once()

    @pytest.mark.asyncio
    async def test_heating_electric_stores_cop_and_transitions_to_loss_method(self):
        flow = self._make_flow()
        flow._main_data = {}
        flow._heating_data = {const.HEATING_TYPE: const.HEATING_TYPE_ELECTRIC}
        flow.async_step_heat_loss_method = AsyncMock(return_value={"type": "form"})
        await flow.async_step_heating_electric(
            user_input={const.HEATING_COP: 2.5, const.HEATING_INDOOR_TEMP: 21.0}
        )
        assert flow._heating_data[const.HEATING_COP] == 2.5
        assert flow._heating_data[const.HEATING_INDOOR_TEMP] == 21.0
        flow.async_step_heat_loss_method.assert_called_once()

    @pytest.mark.asyncio
    async def test_loss_method_known_transitions_to_heat_loss_known(self):
        flow = self._make_flow()
        flow._main_data = {}
        flow._heating_data = {}
        flow.async_step_heat_loss_known = AsyncMock(return_value={"type": "form"})
        await flow.async_step_heat_loss_method(
            user_input={const.HEAT_LOSS_METHOD: const.HEAT_LOSS_METHOD_KNOWN}
        )
        flow.async_step_heat_loss_known.assert_called_once()

    @pytest.mark.asyncio
    async def test_loss_method_estimate_transitions_to_building_estimate(self):
        flow = self._make_flow()
        flow._main_data = {}
        flow._heating_data = {}
        flow.async_step_building_estimate = AsyncMock(return_value={"type": "form"})
        await flow.async_step_heat_loss_method(
            user_input={const.HEAT_LOSS_METHOD: const.HEAT_LOSS_METHOD_ESTIMATE}
        )
        flow.async_step_building_estimate.assert_called_once()

    @pytest.mark.asyncio
    async def test_loss_method_report_transitions_to_heat_loss_report(self):
        flow = self._make_flow()
        flow._main_data = {}
        flow._heating_data = {}
        flow.async_step_heat_loss_report = AsyncMock(return_value={"type": "form"})
        await flow.async_step_heat_loss_method(
            user_input={const.HEAT_LOSS_METHOD: const.HEAT_LOSS_METHOD_REPORT}
        )
        flow.async_step_heat_loss_report.assert_called_once()

    @pytest.mark.asyncio
    async def test_heat_loss_report_derives_coefficient_and_creates_entry(self):
        """8300 W ÷ (20 − (−3.2)) = 357.76 W/°C."""
        flow = self._make_flow()
        flow._main_data = {}
        flow._heating_data = {const.HEATING_TYPE: const.HEATING_TYPE_ELECTRIC}
        await flow.async_step_heat_loss_report(
            user_input={
                const.HEAT_LOSS_REPORT_WATTS: 8300.0,
                const.HEAT_LOSS_REPORT_OUTDOOR_TEMP: -3.2,
                const.HEAT_LOSS_REPORT_INDOOR_TEMP: 20.0,
            }
        )
        flow.async_create_entry.assert_called_once()
        options = flow.async_create_entry.call_args[1]["options"]
        expected = round(8300.0 / (20.0 - (-3.2)), 2)
        assert abs(options[const.HEATING_HEAT_LOSS] - expected) < 0.01

    @pytest.mark.asyncio
    async def test_heat_loss_report_invalid_temps_shows_error(self):
        """Indoor temp <= outdoor temp must show a form with an error, not create an entry."""
        flow = self._make_flow()
        flow._main_data = {}
        flow._heating_data = {}
        result = await flow.async_step_heat_loss_report(
            user_input={
                const.HEAT_LOSS_REPORT_WATTS: 8300.0,
                const.HEAT_LOSS_REPORT_OUTDOOR_TEMP: 20.0,
                const.HEAT_LOSS_REPORT_INDOOR_TEMP: 10.0,  # colder than outdoor — invalid
            }
        )
        flow.async_create_entry.assert_not_called()
        assert result["type"] == "form"

    @pytest.mark.asyncio
    async def test_heat_loss_known_creates_entry(self):
        flow = self._make_flow()
        flow._main_data = {}
        flow._heating_data = {const.HEATING_TYPE: const.HEATING_TYPE_ELECTRIC}
        await flow.async_step_heat_loss_known(
            user_input={const.HEATING_HEAT_LOSS: 150.0}
        )
        flow.async_create_entry.assert_called_once()
        options = flow.async_create_entry.call_args[1]["options"]
        assert options[const.HEATING_HEAT_LOSS] == 150.0

    @pytest.mark.asyncio
    async def test_building_estimate_calculates_heat_loss_and_creates_entry(self):
        flow = self._make_flow()
        flow._main_data = {}
        flow._heating_data = {const.HEATING_TYPE: const.HEATING_TYPE_ELECTRIC}
        await flow.async_step_building_estimate(
            user_input={
                const.BUILDING_FLOOR_AREA: 100.0,
                const.BUILDING_AGE: "1930_1975",
                const.BUILDING_WALL_TYPE: "cavity_uninsulated",
                const.BUILDING_GLAZING: "double",
            }
        )
        flow.async_create_entry.assert_called_once()
        options = flow.async_create_entry.call_args[1]["options"]
        assert options[const.HEATING_HEAT_LOSS] > 0

    @pytest.mark.asyncio
    async def test_interpolation_step_creates_entry_with_known_points(self):
        flow = self._make_flow()
        flow._main_data = {}
        flow._heating_data = {const.HEATING_TYPE: const.HEATING_TYPE_INTERPOLATION}
        await flow.async_step_heating_interpolation(
            user_input={const.HEATING_KNOWN_POINTS: "[[-6, 60], [0, 45]]"}
        )
        flow.async_create_entry.assert_called_once()
        options = flow.async_create_entry.call_args[1]["options"]
        assert options[const.HEATING_KNOWN_POINTS] == "[[-6, 60], [0, 45]]"


# ---------------------------------------------------------------------------
# BatteryChargCalculatorFlowHandler (options flow)
# ---------------------------------------------------------------------------


class TestOptionsFlow:
    def _make_options_flow(self, existing_options=None):
        config_entry = MagicMock()
        config_entry.options = existing_options or {
            const.GIVENERGY_SERIAL_NUMBER: "SN_EXISTING",
            const.GIVENERGY_API_TOKEN: "TOKEN_EXISTING",
            const.OCTOPUS_ACCOUNT_NUMBER: "A-OLD",
            const.OCTOPUS_APIKEY: "KEY_OLD",
            const.SIMULATE_ONLY: False,
        }
        handler = BatteryChargCalculatorFlowHandler(config_entry)
        handler.hass = MagicMock()
        handler.hass.config_entries.async_update_entry = MagicMock()
        handler.async_show_form = MagicMock(return_value={"type": "form"})
        handler.async_create_entry = MagicMock(return_value={"type": "create_entry"})
        # The ML settings step is the final step before saving.  Tests that
        # exercise earlier steps (heating, heat-loss) should treat it as a
        # passthrough so the flow completes and async_create_entry is called.
        handler.async_step_ml_settings = AsyncMock(
            side_effect=lambda user_input=None: handler.async_create_entry(
                title="", data=handler.options
            )
        )
        return handler, config_entry

    @pytest.mark.asyncio
    async def test_shows_form_when_no_input(self):
        handler, _ = self._make_options_flow()
        result = await handler.async_step_init(user_input=None)
        handler.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_saves_new_options_and_transitions_to_heating(self):
        """async_step_init stores options and moves to heating step."""
        handler, config_entry = self._make_options_flow()
        handler.async_step_heating = AsyncMock(return_value={"type": "form"})
        new_input = {
            const.GIVENERGY_SERIAL_NUMBER: "SN_NEW",
            const.GIVENERGY_API_TOKEN: "TOKEN_NEW",
            const.OCTOPUS_ACCOUNT_NUMBER: "A-NEW",
            const.OCTOPUS_APIKEY: "KEY_NEW",
            const.SIMULATE_ONLY: True,
            const.INVERTER_SIZE_KW: 6.0,
            const.INVERTER_EFFICIENCY: 0.95,
        }
        await handler.async_step_init(user_input=new_input)
        handler.async_step_heating.assert_called_once()
        assert handler.options[const.GIVENERGY_SERIAL_NUMBER] == "SN_NEW"
        assert handler.options[const.SIMULATE_ONLY] is True
        assert handler.options[const.INVERTER_SIZE_KW] == 6.0

    @pytest.mark.asyncio
    async def test_strips_legacy_keys(self):
        """Old MPN keys should be removed from options on save."""
        handler, _ = self._make_options_flow(
            existing_options={
                const.GIVENERGY_SERIAL_NUMBER: "SN",
                const.GIVENERGY_API_TOKEN: "TOK",
                const.OCTOPUS_ACCOUNT_NUMBER: "A-OLD",
                const.OCTOPUS_APIKEY: "KEY",
                const.SIMULATE_ONLY: False,
                const.OCTOPUS_MPN: "legacy_mpn",
                const.OCTOPUS_EXPORT_MPN: "legacy_export_mpn",
            }
        )
        handler.async_step_heating = AsyncMock(return_value={"type": "form"})
        new_input = {
            const.GIVENERGY_SERIAL_NUMBER: "SN",
            const.GIVENERGY_API_TOKEN: "TOK",
            const.OCTOPUS_ACCOUNT_NUMBER: "A-NEW",
            const.OCTOPUS_APIKEY: "KEY",
            const.SIMULATE_ONLY: False,
            const.INVERTER_SIZE_KW: const.DEFAULT_INVERTER_SIZE_KW,
            const.INVERTER_EFFICIENCY: const.DEFAULT_INVERTER_EFFICIENCY,
        }
        await handler.async_step_init(user_input=new_input)
        assert const.OCTOPUS_MPN not in handler.options
        assert const.OCTOPUS_EXPORT_MPN not in handler.options

    @pytest.mark.asyncio
    async def test_shows_form_on_exception(self):
        """If an exception is raised, the form is re-shown with errors."""
        handler, config_entry = self._make_options_flow()
        # Force an exception by making the options dict raise on update
        handler.options = MagicMock()
        handler.options.pop = MagicMock(side_effect=Exception("boom"))
        new_input = {
            const.GIVENERGY_SERIAL_NUMBER: "SN",
            const.GIVENERGY_API_TOKEN: "TOK",
            const.OCTOPUS_ACCOUNT_NUMBER: "A-NEW",
            const.OCTOPUS_APIKEY: "KEY",
            const.SIMULATE_ONLY: False,
            const.INVERTER_SIZE_KW: const.DEFAULT_INVERTER_SIZE_KW,
            const.INVERTER_EFFICIENCY: const.DEFAULT_INVERTER_EFFICIENCY,
        }
        result = await handler.async_step_init(user_input=new_input)
        handler.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_options_heating_none_saves_and_exits(self):
        handler, _ = self._make_options_flow()
        await handler.async_step_heating(
            user_input={const.HEATING_TYPE: const.HEATING_TYPE_NONE}
        )
        handler.async_create_entry.assert_called_once()
        assert handler.options[const.HEATING_TYPE] == const.HEATING_TYPE_NONE

    @pytest.mark.asyncio
    async def test_options_heating_electric_transitions_to_electric_step(self):
        handler, _ = self._make_options_flow()
        handler.async_step_heating_electric = AsyncMock(return_value={"type": "form"})
        handler.options[const.HEATING_TYPE] = const.HEATING_TYPE_ELECTRIC
        await handler.async_step_heating(
            user_input={const.HEATING_TYPE: const.HEATING_TYPE_ELECTRIC}
        )
        handler.async_step_heating_electric.assert_called_once()

    @pytest.mark.asyncio
    async def test_options_loss_method_report_goes_to_heat_loss_report(self):
        handler, _ = self._make_options_flow()
        handler.async_step_heat_loss_report = AsyncMock(return_value={"type": "form"})
        await handler.async_step_heat_loss_method(
            user_input={const.HEAT_LOSS_METHOD: const.HEAT_LOSS_METHOD_REPORT}
        )
        handler.async_step_heat_loss_report.assert_called_once()

    @pytest.mark.asyncio
    async def test_options_heat_loss_report_derives_coefficient(self):
        handler, _ = self._make_options_flow()
        await handler.async_step_heat_loss_report(
            user_input={
                const.HEAT_LOSS_REPORT_WATTS: 8300.0,
                const.HEAT_LOSS_REPORT_OUTDOOR_TEMP: -3.2,
                const.HEAT_LOSS_REPORT_INDOOR_TEMP: 20.0,
            }
        )
        handler.async_create_entry.assert_called_once()
        expected = round(8300.0 / (20.0 - (-3.2)), 2)
        assert abs(handler.options[const.HEATING_HEAT_LOSS] - expected) < 0.01

    @pytest.mark.asyncio
    async def test_options_loss_method_known_goes_to_heat_loss_known(self):
        handler, _ = self._make_options_flow()
        handler.async_step_heat_loss_known = AsyncMock(return_value={"type": "form"})
        await handler.async_step_heat_loss_method(
            user_input={const.HEAT_LOSS_METHOD: const.HEAT_LOSS_METHOD_KNOWN}
        )
        handler.async_step_heat_loss_known.assert_called_once()

    @pytest.mark.asyncio
    async def test_options_loss_method_estimate_goes_to_building_estimate(self):
        handler, _ = self._make_options_flow()
        handler.async_step_building_estimate = AsyncMock(return_value={"type": "form"})
        await handler.async_step_heat_loss_method(
            user_input={const.HEAT_LOSS_METHOD: const.HEAT_LOSS_METHOD_ESTIMATE}
        )
        handler.async_step_building_estimate.assert_called_once()

    @pytest.mark.asyncio
    async def test_options_heat_loss_known_saves_and_exits(self):
        handler, _ = self._make_options_flow()
        await handler.async_step_heat_loss_known(
            user_input={const.HEATING_HEAT_LOSS: 200.0}
        )
        handler.async_create_entry.assert_called_once()
        assert handler.options[const.HEATING_HEAT_LOSS] == 200.0

    @pytest.mark.asyncio
    async def test_options_building_estimate_sets_heat_loss(self):
        handler, _ = self._make_options_flow()
        await handler.async_step_building_estimate(
            user_input={
                const.BUILDING_FLOOR_AREA: 80.0,
                const.BUILDING_AGE: "post_2000",
                const.BUILDING_WALL_TYPE: "modern_insulated",
                const.BUILDING_GLAZING: "triple",
            }
        )
        handler.async_create_entry.assert_called_once()
        assert handler.options[const.HEATING_HEAT_LOSS] > 0

    def test_battery_capacity_field_is_optional(self):
        """battery_capacity_kwh is Optional — omitting it uses the default."""
        schema = get_schema()
        result = schema(
            {
                const.GIVENERGY_SERIAL_NUMBER: "SN",
                const.GIVENERGY_API_TOKEN: "TOKEN",
                const.OCTOPUS_ACCOUNT_NUMBER: "A-1234",
                const.OCTOPUS_APIKEY: "KEY",
                const.SIMULATE_ONLY: False,
                const.INVERTER_SIZE_KW: const.DEFAULT_INVERTER_SIZE_KW,
                const.INVERTER_EFFICIENCY: const.DEFAULT_INVERTER_EFFICIENCY,
                # BATTERY_CAPACITY_KWH intentionally omitted
            }
        )
        assert result[const.BATTERY_CAPACITY_KWH] == const.DEFAULT_BATTERY_CAPACITY_KWH

    def test_battery_capacity_custom_value_accepted(self):
        """A custom battery_capacity_kwh value is accepted and preserved."""
        schema = get_schema()
        result = schema(
            {
                const.GIVENERGY_SERIAL_NUMBER: "SN",
                const.GIVENERGY_API_TOKEN: "TOKEN",
                const.OCTOPUS_ACCOUNT_NUMBER: "A-1234",
                const.OCTOPUS_APIKEY: "KEY",
                const.SIMULATE_ONLY: False,
                const.INVERTER_SIZE_KW: const.DEFAULT_INVERTER_SIZE_KW,
                const.INVERTER_EFFICIENCY: const.DEFAULT_INVERTER_EFFICIENCY,
                const.BATTERY_CAPACITY_KWH: 12.0,
            }
        )
        assert result[const.BATTERY_CAPACITY_KWH] == 12.0

    def test_inverter_fields_use_supplied_defaults(self):
        schema = get_schema(
            serialno="SN001",
            apitoken="TOKEN",
            octopus_account_number="A-1234",
            octopus_api_key="KEY",
            simulate=False,
            inverter_size_kw=5.0,
            inverter_efficiency=0.85,
        )
        result = schema(
            {
                const.GIVENERGY_SERIAL_NUMBER: "SN001",
                const.GIVENERGY_API_TOKEN: "TOKEN",
                const.OCTOPUS_ACCOUNT_NUMBER: "A-1234",
                const.OCTOPUS_APIKEY: "KEY",
                const.SIMULATE_ONLY: False,
                const.INVERTER_SIZE_KW: 5.0,
                const.INVERTER_EFFICIENCY: 0.85,
            }
        )
        assert result[const.INVERTER_SIZE_KW] == 5.0
        assert result[const.INVERTER_EFFICIENCY] == 0.85

    def test_defaults_populated(self):
        schema = get_schema(
            serialno="SN001",
            apitoken="TOKEN",
            octopus_account_number="A-1234",
            octopus_api_key="KEY",
            simulate=True,
        )
        # Validate using the schema with those exact values
        result = schema(
            {
                const.GIVENERGY_SERIAL_NUMBER: "SN001",
                const.GIVENERGY_API_TOKEN: "TOKEN",
                const.OCTOPUS_ACCOUNT_NUMBER: "A-1234",
                const.OCTOPUS_APIKEY: "KEY",
                const.SIMULATE_ONLY: True,
                const.INVERTER_SIZE_KW: const.DEFAULT_INVERTER_SIZE_KW,
                const.INVERTER_EFFICIENCY: const.DEFAULT_INVERTER_EFFICIENCY,
            }
        )
        assert result[const.GIVENERGY_SERIAL_NUMBER] == "SN001"
        assert result[const.SIMULATE_ONLY] is True

    def test_schema_rejects_wrong_type_for_boolean(self):
        """simulate_only must be a boolean; a non-coercible string should fail."""
        import voluptuous as vol

        schema = get_schema()
        with pytest.raises((vol.Invalid, Exception)):
            schema(
                {
                    const.GIVENERGY_SERIAL_NUMBER: "SN",
                    const.GIVENERGY_API_TOKEN: "TOKEN",
                    const.OCTOPUS_ACCOUNT_NUMBER: "A-1234",
                    const.OCTOPUS_APIKEY: "KEY",
                    const.SIMULATE_ONLY: "not_a_bool",  # invalid
                }
            )
