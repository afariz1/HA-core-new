"""Test the Photoptimizer config flow."""

from unittest.mock import AsyncMock

from homeassistant import config_entries
from homeassistant.components.photoptimizer.const import (
    CONF_AZIMUTH,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_EFFICIENCY_ROUND_TRIP,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_SOC_RESERVE_PERCENT,
    CONF_CURRENT_CONSUMPTION_ENTITY,
    CONF_CURRENT_SOLAR_PRODUCTION_ENTITY,
    CONF_DECLINATION,
    CONF_ELECTRICITY_PRICE_ENTITY,
    CONF_EMHASS_TOKEN,
    CONF_EMHASS_URL,
    CONF_GRID_POWER_ENTITY,
    CONF_HORIZON_HOURS,
    CONF_KWP,
    CONF_LOAD_FORECAST_ENTITY,
    CONF_RESOLUTION,
    CONF_WEAR_COST_PER_KWH,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_RESOLUTION,
    DOMAIN,
)
from homeassistant.const import CONF_API_KEY, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType


async def test_full_user_flow(hass: HomeAssistant, mock_setup_entry: AsyncMock) -> None:
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
    assert result["step_id"] == "load_forecast"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LOAD_FORECAST_ENTITY: "sensor.load_forecast",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "inverter"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_CURRENT_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production",
            CONF_CURRENT_CONSUMPTION_ENTITY: "sensor.home_consumption",
            CONF_GRID_POWER_ENTITY: "sensor.grid_power",
            CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
            CONF_BATTERY_CAPACITY_KWH: 10.0,
            CONF_BATTERY_SOC_RESERVE_PERCENT: 20.0,
            CONF_BATTERY_EFFICIENCY_ROUND_TRIP: 95.0,
            CONF_WEAR_COST_PER_KWH: 0.01,
            CONF_EMHASS_URL: "http://localhost:5000",
            CONF_EMHASS_TOKEN: "secret-token",
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
    assert result["data"][CONF_LOAD_FORECAST_ENTITY] == "sensor.load_forecast"
    assert (
        result["data"][CONF_CURRENT_SOLAR_PRODUCTION_ENTITY]
        == "sensor.solar_production"
    )
    assert result["data"][CONF_CURRENT_CONSUMPTION_ENTITY] == "sensor.home_consumption"
    assert result["data"][CONF_GRID_POWER_ENTITY] == "sensor.grid_power"
    assert result["data"][CONF_BATTERY_SOC_ENTITY] == "sensor.battery_soc"
    assert result["data"][CONF_BATTERY_CAPACITY_KWH] == 10.0
    assert result["data"][CONF_BATTERY_SOC_RESERVE_PERCENT] == 20.0
    assert result["data"][CONF_BATTERY_EFFICIENCY_ROUND_TRIP] == 95.0
    assert result["data"][CONF_WEAR_COST_PER_KWH] == 0.01
    assert result["data"][CONF_EMHASS_URL] == "http://localhost:5000"
    assert result["data"][CONF_EMHASS_TOKEN] == "secret-token"
    assert len(mock_setup_entry.mock_calls) == 1


async def test_abort_when_already_configured(
    hass: HomeAssistant, mock_setup_entry: AsyncMock
) -> None:
    """Test flow aborts when unique ID already exists."""

    # Create first entry with unique ID based on latitude/longitude/kwp.
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
        {},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_CURRENT_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production",
            CONF_CURRENT_CONSUMPTION_ENTITY: "sensor.home_consumption",
            CONF_GRID_POWER_ENTITY: "sensor.grid_power",
            CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
            CONF_BATTERY_CAPACITY_KWH: 10.0,
            CONF_BATTERY_SOC_RESERVE_PERCENT: 20.0,
            CONF_BATTERY_EFFICIENCY_ROUND_TRIP: 95.0,
            CONF_WEAR_COST_PER_KWH: 0.01,
            CONF_EMHASS_URL: "http://localhost:5000",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    # Start a second flow with the same unique ID tuple.
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
