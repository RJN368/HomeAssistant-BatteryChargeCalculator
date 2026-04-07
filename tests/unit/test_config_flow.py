"""Unit tests for the config_flow module."""

from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from custom_components.battery_charge_calculator import const
from custom_components.battery_charge_calculator.config_flow import (
    BatteryChargCalculatorConfigFlow,
    BatteryChargCalculatorFlowHandler,
    get_schema,
)


# ---------------------------------------------------------------------------
# get_schema helper
# ---------------------------------------------------------------------------


class TestGetSchema:
    def test_returns_schema_with_all_fields(self):
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


# ---------------------------------------------------------------------------
# BatteryChargCalculatorConfigFlow.async_step_user
# ---------------------------------------------------------------------------


class TestConfigFlowAsyncStepUser:
    def _make_flow(self, existing_entries=None):
        flow = BatteryChargCalculatorConfigFlow()
        flow.hass = MagicMock()
        flow.context = {"source": "user"}
        flow._async_current_entries = MagicMock(return_value=existing_entries or [])
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
    async def test_creates_entry_with_user_input(self):
        flow = self._make_flow()
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
        flow.async_create_entry.assert_called_once()
        call_kwargs = flow.async_create_entry.call_args[1]
        options = call_kwargs["options"]
        assert options[const.GIVENERGY_SERIAL_NUMBER] == "SN123"
        assert options[const.GIVENERGY_API_TOKEN] == "TOKEN"
        assert options[const.OCTOPUS_ACCOUNT_NUMBER] == "A-ACCT"
        assert options[const.OCTOPUS_APIKEY] == "OCTKEY"
        assert options[const.SIMULATE_ONLY] is False
        assert options[const.INVERTER_SIZE_KW] == 5.0
        assert options[const.INVERTER_EFFICIENCY] == 0.9

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
        return handler, config_entry

    @pytest.mark.asyncio
    async def test_shows_form_when_no_input(self):
        handler, _ = self._make_options_flow()
        result = await handler.async_step_init(user_input=None)
        handler.async_show_form.assert_called_once()

    @pytest.mark.asyncio
    async def test_saves_new_options(self):
        handler, config_entry = self._make_options_flow()
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
        handler.async_create_entry.assert_called_once()
        saved_data = handler.async_create_entry.call_args[1]["data"]
        assert saved_data[const.GIVENERGY_SERIAL_NUMBER] == "SN_NEW"
        assert saved_data[const.SIMULATE_ONLY] is True
        assert saved_data[const.INVERTER_SIZE_KW] == 6.0
        assert saved_data[const.INVERTER_EFFICIENCY] == 0.95

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
        saved_data = handler.async_create_entry.call_args[1]["data"]
        assert const.OCTOPUS_MPN not in saved_data
        assert const.OCTOPUS_EXPORT_MPN not in saved_data

    @pytest.mark.asyncio
    async def test_shows_form_with_prefilled_values_on_error(self):
        handler, config_entry = self._make_options_flow()
        # Simulate an exception by breaking async_create_entry
        handler.async_create_entry = MagicMock(side_effect=Exception("boom"))
        handler.async_show_form = MagicMock(return_value={"type": "form"})
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
