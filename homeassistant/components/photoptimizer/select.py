"""Photoptimizer select."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the select platform."""
    _LOGGER.debug("Setting up select platform for entry_id=%s", entry.entry_id)
    async_add_entities([PhotoptimizerSelect()])


class PhotoptimizerSelect(SelectEntity):
    """Representation of a Photoptimizer select entity."""

    _attr_has_entity_name = True
    _attr_options = ["Normal", "Survival", "Economic"]
    _attr_current_option = "Normal"
    _attr_icon = "mdi:cog-box"

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        _LOGGER.debug("Select option requested: %s", option)
        if option in self._attr_options:
            self._attr_current_option = option
            self.async_write_ha_state()
            _LOGGER.debug("Select option updated to: %s", option)
        else:
            _LOGGER.debug("Select option ignored (invalid): %s", option)
