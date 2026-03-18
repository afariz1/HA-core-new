"""The Photoptimizer integration."""

from __future__ import annotations

from datetime import datetime
import logging

from forecast_solar import ForecastSolar, ForecastSolarError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN
from .coordinator import PhotoptimizerCoordinator

_LOGGER = logging.getLogger(__name__)
_PLATFORMS: list[Platform] = [Platform.SELECT, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Photoptimizer from a config entry.

    Create Forecast.Solar client, coordinator and store it so platforms can use it.
    """
    session = async_get_clientsession(hass)

    latitude = entry.data.get("latitude") or hass.config.latitude
    longitude = entry.data.get("longitude") or hass.config.longitude
    declination = entry.data.get("tilt") or entry.data.get("declination") or 0.0
    azimuth = entry.data.get("azimuth") or 0.0
    kwp = entry.data.get("kwp") or 0.0
    api_key = entry.data.get("api_key")

    if latitude is None or longitude is None:
        raise ConfigEntryNotReady("Latitude and longitude are required")

    client = ForecastSolar(
        session=session,
        latitude=float(latitude),
        longitude=float(longitude),
        declination=float(declination),
        azimuth=float(azimuth),
        kwp=float(kwp),
        damping=0,
        api_key=api_key,
    )

    try:
        await client.estimate()
    except ForecastSolarError as err:
        _LOGGER.debug("ForecastSolar validation failed: %s", err)
        raise ConfigEntryNotReady(
            f"Unable to connect to Forecast.Solar: {err}"
        ) from err
    except Exception as err:
        _LOGGER.debug("Unexpected error during Forecast.Solar validation: %s", err)
        raise ConfigEntryNotReady(
            f"Unexpected error connecting to Forecast.Solar: {err}"
        ) from err

    coordinator = PhotoptimizerCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)

    async def _async_handle_daily_optimization() -> None:
        """Run daily optimization with error isolation."""
        try:
            await coordinator.async_run_daily_optimization()
        except UpdateFailed as err:
            _LOGGER.warning("Daily EMHASS optimization failed: %s", err)

    async def _async_handle_hourly_publish() -> None:
        """Run hourly publish-data with error isolation."""
        try:
            await coordinator.async_run_hourly_publish()
        except UpdateFailed as err:
            _LOGGER.warning("Hourly EMHASS publish-data failed: %s", err)

    async def _async_handle_startup_bootstrap() -> None:
        """Run startup sequence in strict order: optimize first, publish second."""
        try:
            await coordinator.async_run_daily_optimization()
        except UpdateFailed as err:
            _LOGGER.warning("Startup EMHASS optimization failed: %s", err)
            return

        try:
            await coordinator.async_run_hourly_publish()
        except UpdateFailed as err:
            _LOGGER.warning("Startup EMHASS publish-data failed: %s", err)

    @callback
    def _daily_schedule_listener(_: datetime) -> None:
        """Schedule daily optimization task at 17:00."""
        hass.async_create_task(_async_handle_daily_optimization())

    @callback
    def _hourly_schedule_listener(_: datetime) -> None:
        """Schedule hourly publish task on each full hour."""
        hass.async_create_task(_async_handle_hourly_publish())

    entry.async_on_unload(
        async_track_time_change(
            hass,
            _daily_schedule_listener,
            hour=17,
            minute=0,
            second=0,
        )
    )
    entry.async_on_unload(
        async_track_time_change(
            hass,
            _hourly_schedule_listener,
            minute=0,
            second=0,
        )
    )

    # Prime data right after setup: first generate a fresh optimization result,
    # then publish entities from that result.
    hass.async_create_task(_async_handle_startup_bootstrap())

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        entry.runtime_data = None
    return unload_ok
