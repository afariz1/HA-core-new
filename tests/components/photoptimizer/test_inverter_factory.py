"""Tests for inverter factory adapter creation."""

from __future__ import annotations

from homeassistant.components.photoptimizer.const import (
    CONF_INVERTER_DISCHARGE_POWER_ENTITY,
    CONF_INVERTER_MODE_ENTITY,
    CONF_INVERTER_TYPE,
    DOMAIN,
    INVERTER_TYPE_GOODWE,
    INVERTER_TYPE_GROWATT,
)
from homeassistant.components.photoptimizer.goodwe_control import GoodweControlAdapter
from homeassistant.components.photoptimizer.growatt_control import GrowattControlAdapter
from homeassistant.components.photoptimizer.inverter_factory import (
    NoopControlAdapter,
    create_inverter_adapter,
)
from homeassistant.components.photoptimizer.models import (
    ExecutionSlotCommand,
    OperationMode,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from tests.common import MockConfigEntry


async def test_factory_creates_goodwe_adapter(hass: HomeAssistant) -> None:
    """Factory returns GoodweControlAdapter for INVERTER_TYPE_GOODWE."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_INVERTER_TYPE: INVERTER_TYPE_GOODWE,
            CONF_INVERTER_MODE_ENTITY: "select.goodwe_mode",
            CONF_INVERTER_DISCHARGE_POWER_ENTITY: "number.goodwe_discharge_power",
        },
    )

    adapter = create_inverter_adapter(hass, entry)

    assert isinstance(adapter, GoodweControlAdapter)


async def test_factory_creates_growatt_adapter(hass: HomeAssistant) -> None:
    """Factory returns GrowattControlAdapter for INVERTER_TYPE_GROWATT."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_INVERTER_TYPE: INVERTER_TYPE_GROWATT,
            CONF_INVERTER_MODE_ENTITY: "select.growatt_mode",
            CONF_INVERTER_DISCHARGE_POWER_ENTITY: "number.growatt_discharge_power",
        },
    )

    adapter = create_inverter_adapter(hass, entry)

    assert isinstance(adapter, GrowattControlAdapter)


async def test_factory_fallback_noop_for_unknown_type(hass: HomeAssistant) -> None:
    """Factory returns NoopControlAdapter for unknown inverter type."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_INVERTER_TYPE: "unsupported_vendor",
            CONF_INVERTER_MODE_ENTITY: "select.mode",
            CONF_INVERTER_DISCHARGE_POWER_ENTITY: "number.discharge",
        },
    )

    adapter = create_inverter_adapter(hass, entry)

    assert isinstance(adapter, NoopControlAdapter)


async def test_factory_noop_adapter_safe_fallback(hass: HomeAssistant) -> None:
    """NoopControlAdapter.async_apply logs safely without exception."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_INVERTER_TYPE: "unknown",
        },
    )

    adapter = create_inverter_adapter(hass, entry)
    command = ExecutionSlotCommand(
        slot_start=dt_util.utcnow(),
        p_bat_cmd=500,
        soc_target=45,
        grid_limit=0,
        op_mode=OperationMode.FORCED_DISCHARGE,
    )

    # Should not raise.
    await adapter.async_apply(command)
