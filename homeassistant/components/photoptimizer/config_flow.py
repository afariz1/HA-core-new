"""Config flow for the Photoptimizer integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.helpers import selector

from .const import (
    CONF_AZIMUTH,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CHARGE_POWER_MAX,
    CONF_BATTERY_DISCHARGE_POWER_MAX,
    CONF_BATTERY_EFFICIENCY_ROUND_TRIP,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_SOC_RESERVE_PERCENT,
    CONF_BATTERY_TARGET_SOC_PERCENT,
    CONF_CURRENT_CONSUMPTION_ENTITY,
    CONF_CURRENT_SOLAR_PRODUCTION_ENTITY,
    CONF_DECLINATION,
    CONF_ELECTRICITY_PRICE_ENTITY,
    CONF_EMHASS_TOKEN,
    CONF_EMHASS_URL,
    CONF_GROWATT_AC_CHARGE_SWITCH_ENTITY,
    CONF_GROWATT_DEVICE_ID,
    CONF_GROWATT_INVERTER_VARIANT,
    CONF_HORIZON_HOURS,
    CONF_INVERTER_CHARGE_POWER_ENTITY,
    CONF_INVERTER_DISCHARGE_POWER_ENTITY,
    CONF_INVERTER_MODE_ENTITY,
    CONF_INVERTER_TYPE,
    CONF_KWP,
    CONF_RESOLUTION,
    CONF_WEAR_COST_PER_KWH,
    DEFAULT_BATTERY_CHARGE_POWER_MAX,
    DEFAULT_BATTERY_DISCHARGE_POWER_MAX,
    DEFAULT_BATTERY_EFFICIENCY_ROUND_TRIP,
    DEFAULT_BATTERY_SOC_RESERVE_PERCENT,
    DEFAULT_BATTERY_TARGET_SOC_PERCENT,
    DEFAULT_EMHASS_URL,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_RESOLUTION,
    DEFAULT_WEAR_COST_PER_KWH,
    DOMAIN,
    GROWATT_VARIANT_AUTO,
    GROWATT_VARIANT_MIN,
    GROWATT_VARIANT_SPH,
    INVERTER_TYPE_GOODWE,
    INVERTER_TYPE_GROWATT,
)

_LOGGER = logging.getLogger(__name__)
_SENSITIVE_FIELDS = {CONF_API_KEY, CONF_EMHASS_TOKEN}


def _redact_user_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Return log-safe copy of flow user input."""
    return {
        key: ("***" if key in _SENSITIVE_FIELDS and value else value)
        for key, value in user_input.items()
    }


class PhotoptimizerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Photoptimizer config flow."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Initial step, use defaults and continue."""
        _LOGGER.debug("Config flow user step started")
        self._data[CONF_HORIZON_HOURS] = DEFAULT_HORIZON_HOURS
        self._data[CONF_RESOLUTION] = DEFAULT_RESOLUTION
        _LOGGER.debug(
            "Config flow defaults set: horizon_hours=%s resolution=%s",
            self._data[CONF_HORIZON_HOURS],
            self._data[CONF_RESOLUTION],
        )
        return await self.async_step_electricity_price()

    async def async_step_electricity_price(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle electricity price configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            _LOGGER.debug(
                "Electricity price step received input: %s",
                _redact_user_input(user_input),
            )
            self._data.update(user_input)
            _LOGGER.debug("Electricity price step completed")

            return await self.async_step_pv_forecast()

        _LOGGER.debug("Showing electricity price form")

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ELECTRICITY_PRICE_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["sensor"],
                        multiple=False,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="electricity_price",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_pv_forecast(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """PV forecast step using Forecast.Solar settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            _LOGGER.debug(
                "PV forecast step received input: %s",
                _redact_user_input(user_input),
            )
            self._data.update(user_input)
            _LOGGER.debug("PV forecast step validating unique ID")

            unique_id = f"{user_input[CONF_LATITUDE]}_{user_input[CONF_LONGITUDE]}_{user_input[CONF_KWP]}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            _LOGGER.debug("PV forecast step completed with unique_id=%s", unique_id)

            return await self.async_step_inverter_type()

        _LOGGER.debug("Showing PV forecast form")

        data_schema = vol.Schema(
            {
                vol.Required(CONF_LATITUDE, default=49.5962536): vol.Coerce(float),
                vol.Required(CONF_AZIMUTH, default=124): vol.Coerce(int),
                vol.Required(CONF_LONGITUDE, default=18.3395664): vol.Coerce(float),
                vol.Required(CONF_KWP, default=6.44): vol.Coerce(float),
                vol.Required(CONF_DECLINATION, default=40): vol.Coerce(int),
                vol.Optional(CONF_API_KEY): str,
            }
        )

        return self.async_show_form(
            step_id="pv_forecast",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_inverter_type(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle inverter type selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            _LOGGER.debug(
                "Inverter type step received input: %s",
                _redact_user_input(user_input),
            )
            self._data.update(user_input)
            return await self.async_step_inverter()

        data_schema = vol.Schema(
            {
                vol.Required(CONF_INVERTER_TYPE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=INVERTER_TYPE_GOODWE,
                                label="GoodWe",
                            ),
                            selector.SelectOptionDict(
                                value=INVERTER_TYPE_GROWATT,
                                label="Growatt",
                            ),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="inverter_type",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_inverter(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle inverter configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            _LOGGER.debug(
                "Inverter step received input: %s",
                _redact_user_input(user_input),
            )
            self._data.update(user_input)
            _LOGGER.debug("Inverter data step completed")

            _LOGGER.info("Creating Photoptimizer config entry")
            _LOGGER.debug(
                "Config entry data keys: %s",
                sorted(self._data.keys()),
            )
            return self.async_create_entry(title="Photoptimizer", data=self._data)

        _LOGGER.debug("Showing inverter form")

        data_fields: dict[Any, Any] = {
            vol.Required(CONF_CURRENT_SOLAR_PRODUCTION_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["sensor"],
                    multiple=False,
                )
            ),
            vol.Required(CONF_CURRENT_CONSUMPTION_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["sensor"],
                    multiple=False,
                )
            ),
            vol.Required(CONF_BATTERY_SOC_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["sensor"],
                    multiple=False,
                )
            ),
            vol.Required(CONF_BATTERY_CAPACITY_KWH): vol.Coerce(float),
            vol.Required(
                CONF_BATTERY_SOC_RESERVE_PERCENT,
                default=DEFAULT_BATTERY_SOC_RESERVE_PERCENT,
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
            vol.Required(
                CONF_BATTERY_EFFICIENCY_ROUND_TRIP,
                default=DEFAULT_BATTERY_EFFICIENCY_ROUND_TRIP,
            ): vol.All(vol.Coerce(float), vol.Range(min=1, max=100)),
            vol.Required(
                CONF_BATTERY_TARGET_SOC_PERCENT,
                default=DEFAULT_BATTERY_TARGET_SOC_PERCENT,
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
            vol.Required(
                CONF_BATTERY_CHARGE_POWER_MAX,
                default=DEFAULT_BATTERY_CHARGE_POWER_MAX,
            ): vol.All(vol.Coerce(float), vol.Range(min=1)),
            vol.Required(
                CONF_BATTERY_DISCHARGE_POWER_MAX,
                default=DEFAULT_BATTERY_DISCHARGE_POWER_MAX,
            ): vol.All(vol.Coerce(float), vol.Range(min=1)),
            vol.Required(
                CONF_WEAR_COST_PER_KWH,
                default=DEFAULT_WEAR_COST_PER_KWH,
            ): vol.Coerce(float),
            vol.Required(CONF_EMHASS_URL, default=DEFAULT_EMHASS_URL): str,
            vol.Optional(CONF_EMHASS_TOKEN): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }

        inverter_type = self._data.get(CONF_INVERTER_TYPE)
        data_fields.update(
            {
                vol.Required(CONF_INVERTER_MODE_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["select"],
                        multiple=False,
                    )
                ),
                vol.Optional(
                    CONF_INVERTER_CHARGE_POWER_ENTITY
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["number"],
                        multiple=False,
                    )
                ),
                vol.Required(
                    CONF_INVERTER_DISCHARGE_POWER_ENTITY
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["number"],
                        multiple=False,
                    )
                ),
            }
        )

        if inverter_type == INVERTER_TYPE_GROWATT:
            data_fields.update(
                {
                    vol.Optional(
                        CONF_GROWATT_AC_CHARGE_SWITCH_ENTITY
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain=["switch"],
                            multiple=False,
                        )
                    ),
                    vol.Optional(CONF_GROWATT_DEVICE_ID): selector.DeviceSelector(
                        selector.DeviceSelectorConfig(integration="growatt_server")
                    ),
                    vol.Optional(
                        CONF_GROWATT_INVERTER_VARIANT,
                        default=GROWATT_VARIANT_AUTO,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=GROWATT_VARIANT_AUTO,
                                    label="Auto",
                                ),
                                selector.SelectOptionDict(
                                    value=GROWATT_VARIANT_MIN,
                                    label="MIN",
                                ),
                                selector.SelectOptionDict(
                                    value=GROWATT_VARIANT_SPH,
                                    label="SPH",
                                ),
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            )

        data_schema = vol.Schema(data_fields)

        return self.async_show_form(
            step_id="inverter",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle reconfiguration of EMHASS connection settings."""
        entry = self._get_reconfigure_entry()
        _LOGGER.debug("Reconfigure step opened for entry_id=%s", entry.entry_id)

        if user_input is not None:
            _LOGGER.debug(
                "Reconfigure step received input: %s",
                _redact_user_input(user_input),
            )
            return self.async_update_reload_and_abort(
                entry,
                data_updates={
                    CONF_EMHASS_URL: user_input[CONF_EMHASS_URL],
                    CONF_EMHASS_TOKEN: user_input.get(CONF_EMHASS_TOKEN) or None,
                },
            )

        _LOGGER.debug("Showing reconfigure form for entry_id=%s", entry.entry_id)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_EMHASS_URL): str,
                        vol.Optional(CONF_EMHASS_TOKEN): selector.TextSelector(
                            selector.TextSelectorConfig(
                                type=selector.TextSelectorType.PASSWORD
                            )
                        ),
                    }
                ),
                {
                    CONF_EMHASS_URL: entry.data.get(
                        CONF_EMHASS_URL,
                        DEFAULT_EMHASS_URL,
                    ),
                    CONF_EMHASS_TOKEN: entry.data.get(CONF_EMHASS_TOKEN),
                },
            ),
            errors={},
        )
