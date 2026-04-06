"""Tests for Photoptimizer executor layer."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.photoptimizer.const import (
    CONF_INVERTER_DISCHARGE_POWER_ENTITY,
    CONF_INVERTER_MODE_ENTITY,
    CONF_INVERTER_TYPE,
    DOMAIN,
    INVERTER_TYPE_GOODWE,
)
from homeassistant.components.photoptimizer.executor import PhotoptimizerExecutor
from homeassistant.components.photoptimizer.models import (
    ExecutionPlan,
    ExecutionSlotCommand,
    OperationMode,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from tests.common import MockConfigEntry, async_mock_service


def _build_executor(hass: HomeAssistant) -> PhotoptimizerExecutor:
    """Create executor with minimal GoodWe mapping."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_INVERTER_TYPE: INVERTER_TYPE_GOODWE,
            CONF_INVERTER_MODE_ENTITY: "select.goodwe_mode",
            CONF_INVERTER_DISCHARGE_POWER_ENTITY: "number.goodwe_discharge_power",
        },
    )
    return PhotoptimizerExecutor(hass, entry)


async def test_executor_applies_goodwe_discharge_command(hass: HomeAssistant) -> None:
    """Apply discharge command from normalized execution plan."""
    executor = _build_executor(hass)
    execution_plan = ExecutionPlan(
        slots=[
            ExecutionSlotCommand(
                slot_start=dt_util.utcnow(),
                p_bat_cmd=500,
                soc_target=45,
                grid_limit=0,
                op_mode=OperationMode.FORCED_DISCHARGE,
            )
        ],
        step_minutes=5,
        timestamp=dt_util.utcnow(),
        valid=True,
    )

    number_calls = async_mock_service(hass, "number", "set_value")
    select_calls = async_mock_service(hass, "select", "select_option")
    applied = await executor.async_execute_plan(execution_plan)

    assert applied is True
    assert len(number_calls) == 1
    assert number_calls[0].data == {
        "entity_id": "number.goodwe_discharge_power",
        "value": 10,
    }
    assert len(select_calls) == 1
    assert select_calls[0].data == {
        "entity_id": "select.goodwe_mode",
        "option": "eco_discharge",
    }


async def test_executor_skips_stale_schedule(hass: HomeAssistant) -> None:
    """Skip command application when nearest schedule slot is stale."""
    executor = _build_executor(hass)
    stale_time = dt_util.utcnow() - timedelta(minutes=45)
    execution_plan = ExecutionPlan(
        slots=[
            ExecutionSlotCommand(
                slot_start=stale_time,
                p_bat_cmd=700,
                soc_target=50,
                grid_limit=0,
                op_mode=OperationMode.FORCED_DISCHARGE,
            )
        ],
        step_minutes=5,
        timestamp=dt_util.utcnow(),
        valid=True,
    )

    number_calls = async_mock_service(hass, "number", "set_value")
    select_calls = async_mock_service(hass, "select", "select_option")
    applied = await executor.async_execute_plan(execution_plan)

    assert applied is False
    assert number_calls == []
    assert select_calls == []


async def test_executor_fallbacks_to_auto_on_non_optimal_status(
    hass: HomeAssistant,
) -> None:
    """Use safe fallback command when execution plan is marked invalid."""
    executor = _build_executor(hass)
    execution_plan = ExecutionPlan(
        slots=[],
        step_minutes=5,
        timestamp=dt_util.utcnow(),
        valid=False,
        source="emhass_publish_status",
    )

    number_calls = async_mock_service(hass, "number", "set_value")
    select_calls = async_mock_service(hass, "select", "select_option")
    applied = await executor.async_execute_plan(execution_plan)

    assert applied is True
    assert len(select_calls) == 1
    assert select_calls[0].data == {
        "entity_id": "select.goodwe_mode",
        "option": "general",
    }
    assert len(number_calls) == 1
    assert number_calls[0].data == {
        "entity_id": "number.goodwe_discharge_power",
        "value": 0,
    }
