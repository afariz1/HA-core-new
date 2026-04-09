"""Test the Photoptimizer config flow."""

from unittest.mock import AsyncMock

from homeassistant import config_entries
from homeassistant.components.photoptimizer.const import (
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
    CONF_DEFERRABLE_LOAD_ENTITY,
    CONF_DEFERRABLE_LOAD_NAME,
    CONF_DEFERRABLE_LOAD_NOMINAL_POWER,
    CONF_DEFERRABLE_LOAD_OPERATING_MINUTES,
    CONF_DEFERRABLE_LOADS,
    CONF_ELECTRICITY_PRICE_ENTITY,
    CONF_EMHASS_TOKEN,
    CONF_EMHASS_URL,
    CONF_HORIZON_HOURS,
    CONF_INVERTER_DISCHARGE_POWER_ENTITY,
    CONF_INVERTER_MODE_ENTITY,
    CONF_INVERTER_TYPE,
    CONF_KWP,
    CONF_RESOLUTION,
    CONF_WEAR_COST_PER_KWH,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_RESOLUTION,
    DOMAIN,
    INVERTER_TYPE_GOODWE,
)
from homeassistant.components.recorder import Recorder
from homeassistant.const import CONF_API_KEY, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType


async def test_full_user_flow(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
) -> None:
    """Test full multi-step user flow creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "electricity_price"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ELECTRICITY_PRICE_ENTITY: "sensor.electricity_price",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pv_forecast"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LATITUDE: 49.5962536,
            CONF_LONGITUDE: 18.3395664,
            CONF_AZIMUTH: 124,
            CONF_DECLINATION: 40,
            CONF_KWP: 6.44,
            CONF_API_KEY: "api-key",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "inverter_type"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_INVERTER_TYPE: INVERTER_TYPE_GOODWE,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "inverter"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_CURRENT_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production",
            CONF_CURRENT_CONSUMPTION_ENTITY: "sensor.home_consumption",
            CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
            CONF_BATTERY_CAPACITY_KWH: 10.0,
            CONF_BATTERY_SOC_RESERVE_PERCENT: 20.0,
            CONF_BATTERY_TARGET_SOC_PERCENT: 60.0,
            CONF_BATTERY_EFFICIENCY_ROUND_TRIP: 95.0,
            CONF_BATTERY_CHARGE_POWER_MAX: 1500.0,
            CONF_BATTERY_DISCHARGE_POWER_MAX: 2200.0,
            CONF_WEAR_COST_PER_KWH: 0.01,
            CONF_EMHASS_URL: "http://localhost:5000",
            CONF_EMHASS_TOKEN: "secret-token",
            CONF_INVERTER_MODE_ENTITY: "select.goodwe_mode",
            CONF_INVERTER_DISCHARGE_POWER_ENTITY: "number.goodwe_discharge_power",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "deferrable_load_count"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"deferrable_load_count": 1},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "deferrable_load"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_DEFERRABLE_LOAD_NAME: "Dishwasher",
            CONF_DEFERRABLE_LOAD_ENTITY: "switch.dishwasher",
            CONF_DEFERRABLE_LOAD_NOMINAL_POWER: 1200.0,
            CONF_DEFERRABLE_LOAD_OPERATING_MINUTES: 90,
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Photoptimizer"
    assert result["data"][CONF_HORIZON_HOURS] == DEFAULT_HORIZON_HOURS
    assert result["data"][CONF_RESOLUTION] == DEFAULT_RESOLUTION
    assert result["data"][CONF_ELECTRICITY_PRICE_ENTITY] == "sensor.electricity_price"
    assert result["data"][CONF_LATITUDE] == 49.5962536
    assert result["data"][CONF_LONGITUDE] == 18.3395664
    assert result["data"][CONF_AZIMUTH] == 124
    assert result["data"][CONF_DECLINATION] == 40
    assert result["data"][CONF_KWP] == 6.44
    assert result["data"][CONF_API_KEY] == "api-key"
    assert result["data"][CONF_INVERTER_TYPE] == INVERTER_TYPE_GOODWE
    assert (
        result["data"][CONF_CURRENT_SOLAR_PRODUCTION_ENTITY]
        == "sensor.solar_production"
    )
    assert result["data"][CONF_CURRENT_CONSUMPTION_ENTITY] == "sensor.home_consumption"
    assert result["data"][CONF_BATTERY_SOC_ENTITY] == "sensor.battery_soc"
    assert result["data"][CONF_BATTERY_CAPACITY_KWH] == 10.0
    assert result["data"][CONF_BATTERY_SOC_RESERVE_PERCENT] == 20.0
    assert result["data"][CONF_BATTERY_TARGET_SOC_PERCENT] == 60.0
    assert result["data"][CONF_BATTERY_EFFICIENCY_ROUND_TRIP] == 95.0
    assert result["data"][CONF_BATTERY_CHARGE_POWER_MAX] == 1500.0
    assert result["data"][CONF_BATTERY_DISCHARGE_POWER_MAX] == 2200.0
    assert result["data"][CONF_WEAR_COST_PER_KWH] == 0.01
    assert result["data"][CONF_EMHASS_URL] == "http://localhost:5000"
    assert result["data"][CONF_EMHASS_TOKEN] == "secret-token"
    assert result["data"][CONF_INVERTER_MODE_ENTITY] == "select.goodwe_mode"
    assert (
        result["data"][CONF_INVERTER_DISCHARGE_POWER_ENTITY]
        == "number.goodwe_discharge_power"
    )
    assert result["data"][CONF_DEFERRABLE_LOADS] == [
        {
            CONF_DEFERRABLE_LOAD_NAME: "Dishwasher",
            CONF_DEFERRABLE_LOAD_ENTITY: "switch.dishwasher",
            CONF_DEFERRABLE_LOAD_NOMINAL_POWER: 1200.0,
            CONF_DEFERRABLE_LOAD_OPERATING_MINUTES: 90,
        }
    ]
    assert len(mock_setup_entry.mock_calls) == 1


async def test_abort_when_already_configured(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
) -> None:
    """Test flow aborts when unique ID already exists."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ELECTRICITY_PRICE_ENTITY: "sensor.electricity_price",
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LATITUDE: 49.0,
            CONF_LONGITUDE: 18.0,
            CONF_AZIMUTH: 180,
            CONF_DECLINATION: 40,
            CONF_KWP: 5.0,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_INVERTER_TYPE: INVERTER_TYPE_GOODWE,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_CURRENT_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production",
            CONF_CURRENT_CONSUMPTION_ENTITY: "sensor.home_consumption",
            CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
            CONF_BATTERY_CAPACITY_KWH: 10.0,
            CONF_BATTERY_SOC_RESERVE_PERCENT: 20.0,
            CONF_BATTERY_TARGET_SOC_PERCENT: 60.0,
            CONF_BATTERY_EFFICIENCY_ROUND_TRIP: 95.0,
            CONF_BATTERY_CHARGE_POWER_MAX: 1500.0,
            CONF_BATTERY_DISCHARGE_POWER_MAX: 2200.0,
            CONF_WEAR_COST_PER_KWH: 0.01,
            CONF_EMHASS_URL: "http://localhost:5000",
            CONF_INVERTER_MODE_ENTITY: "select.goodwe_mode",
            CONF_INVERTER_DISCHARGE_POWER_ENTITY: "number.goodwe_discharge_power",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "deferrable_load_count"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"deferrable_load_count": 0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ELECTRICITY_PRICE_ENTITY: "sensor.electricity_price_2",
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LATITUDE: 49.0,
            CONF_LONGITUDE: 18.0,
            CONF_AZIMUTH: 90,
            CONF_DECLINATION: 30,
            CONF_KWP: 5.0,
        },
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(mock_setup_entry.mock_calls) == 1


async def test_options_flow_updates_deferrable_loads(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    mock_setup_entry: AsyncMock,
) -> None:
    """Test options flow can update deferrable load definitions."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ELECTRICITY_PRICE_ENTITY: "sensor.electricity_price",
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LATITUDE: 49.0,
            CONF_LONGITUDE: 18.0,
            CONF_AZIMUTH: 180,
            CONF_DECLINATION: 40,
            CONF_KWP: 5.0,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_INVERTER_TYPE: INVERTER_TYPE_GOODWE,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_CURRENT_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production",
            CONF_CURRENT_CONSUMPTION_ENTITY: "sensor.home_consumption",
            CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
            CONF_BATTERY_CAPACITY_KWH: 10.0,
            CONF_BATTERY_SOC_RESERVE_PERCENT: 20.0,
            CONF_BATTERY_TARGET_SOC_PERCENT: 60.0,
            CONF_BATTERY_EFFICIENCY_ROUND_TRIP: 95.0,
            CONF_BATTERY_CHARGE_POWER_MAX: 1500.0,
            CONF_BATTERY_DISCHARGE_POWER_MAX: 2200.0,
            CONF_WEAR_COST_PER_KWH: 0.01,
            CONF_EMHASS_URL: "http://localhost:5000",
            CONF_INVERTER_MODE_ENTITY: "select.goodwe_mode",
            CONF_INVERTER_DISCHARGE_POWER_ENTITY: "number.goodwe_discharge_power",
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"deferrable_load_count": 0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    config_entry = hass.config_entries.async_entries(DOMAIN)[0]
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"deferrable_load_count": 1},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "deferrable_load"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_DEFERRABLE_LOAD_NAME: "Water heater",
            CONF_DEFERRABLE_LOAD_ENTITY: "switch.water_heater",
            CONF_DEFERRABLE_LOAD_NOMINAL_POWER: 1500.0,
            CONF_DEFERRABLE_LOAD_OPERATING_MINUTES: 120,
        },
    )

    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_DEFERRABLE_LOADS] == [
        {
            CONF_DEFERRABLE_LOAD_NAME: "Water heater",
            CONF_DEFERRABLE_LOAD_ENTITY: "switch.water_heater",
            CONF_DEFERRABLE_LOAD_NOMINAL_POWER: 1500.0,
            CONF_DEFERRABLE_LOAD_OPERATING_MINUTES: 120,
        }
    ]
