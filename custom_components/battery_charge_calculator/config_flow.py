"""Config flow for the battery_manager component."""

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from . import const


def get_schema(
    serialno="",
    apitoken="",
    octopus_account_number="",
    octopus_api_key="",
    simulate=False,
):
    return vol.Schema(
        {
            vol.Required(const.GIVENERGY_SERIAL_NUMBER, default=serialno): cv.string,
            vol.Required(const.GIVENERGY_API_TOKEN, default=apitoken): cv.string,
            vol.Required(
                const.OCTOPUS_ACCOUNT_NUMBER, default=octopus_account_number
            ): cv.string,
            vol.Required(const.OCTOPUS_APIKEY, default=octopus_api_key): cv.string,
            vol.Required(const.SIMULATE_ONLY, default=simulate): cv.boolean,
        }
    )


class BatteryChargCalculatorConfigFlow(config_entries.ConfigFlow, domain=const.DOMAIN):
    """Config flow for Scheduler."""

    VERSION = 2
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""

        # Only a single instance of the integration
        await self.async_set_unique_id(const.DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(
                title=const.TITLE,
                data={},
                options={
                    const.GIVENERGY_API_TOKEN: user_input[const.GIVENERGY_API_TOKEN],
                    const.GIVENERGY_SERIAL_NUMBER: user_input[
                        const.GIVENERGY_SERIAL_NUMBER
                    ],
                    const.OCTOPUS_ACCOUNT_NUMBER: user_input[
                        const.OCTOPUS_ACCOUNT_NUMBER
                    ],
                    const.OCTOPUS_APIKEY: user_input[const.OCTOPUS_APIKEY],
                    const.SIMULATE_ONLY: user_input[const.SIMULATE_ONLY],
                },
            )

        return self.async_show_form(step_id="user", data_schema=get_schema())

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return BatteryChargCalculatorFlowHandler(config_entry)


class BatteryChargCalculatorFlowHandler(config_entries.OptionsFlow):
    """Handles options flow for the component."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self.options = dict(config_entry.options)

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Manage the options for the custom component."""

        errors = {}
        serial_number = self._config_entry.options.get(const.GIVENERGY_SERIAL_NUMBER)
        api_token = self._config_entry.options.get(const.GIVENERGY_API_TOKEN)
        octopus_account_number = self._config_entry.options.get(
            const.OCTOPUS_ACCOUNT_NUMBER
        )
        octopus_api_key = self._config_entry.options.get(const.OCTOPUS_APIKEY)
        simulate = self._config_entry.options.get(const.SIMULATE_ONLY)

        if user_input is not None:
            try:
                all_config_data = {**self._config_entry.options}

                ##clear up old data from previous versions of the integration that is no longer used
                all_config_data.pop(const.OCTOPUS_EXPORT_MPN, None)
                all_config_data.pop(const.OCTOPUS_MPN, None)

                serial_number = user_input[const.GIVENERGY_SERIAL_NUMBER]
                api_token = user_input[const.GIVENERGY_API_TOKEN]
                octopus_account_number = user_input[const.OCTOPUS_ACCOUNT_NUMBER]
                octopus_api_key = user_input[const.OCTOPUS_APIKEY]
                simulate = user_input[const.SIMULATE_ONLY]

                all_config_data[const.GIVENERGY_SERIAL_NUMBER] = serial_number
                all_config_data[const.GIVENERGY_API_TOKEN] = api_token
                all_config_data[const.OCTOPUS_ACCOUNT_NUMBER] = octopus_account_number
                all_config_data[const.OCTOPUS_APIKEY] = octopus_api_key
                all_config_data[const.SIMULATE_ONLY] = simulate

                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    title=const.TITLE,
                    options=all_config_data,
                )

                return self.async_create_entry(title=const.TITLE, data=all_config_data)

            except Exception as e:
                errors["base"] = "Error saving configuration settings"

        return self.async_show_form(
            step_id="init",
            data_schema=get_schema(
                serial_number,
                api_token,
                octopus_account_number,
                octopus_api_key,
                simulate,
            ),
            errors=errors,
        )
