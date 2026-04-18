"""Tests for Photoptimizer executor layer."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.photoptimizer.const import (
    CONF_GROWATT_AC_CHARGE_SWITCH_ENTITY,
    CONF_GROWATT_DEVICE_ID,
    CONF_GROWATT_INVERTER_VARIANT,
    CONF_INVERTER_CHARGE_POWER_ENTITY,
    CONF_INVERTER_DISCHARGE_POWER_ENTITY,
    CONF_INVERTER_MODE_ENTITY,
    CONF_INVERTER_TYPE,
    DOMAIN,
    GROWATT_VARIANT_SPH,
    INVERTER_TYPE_GOODWE,
    INVERTER_TYPE_GROWATT,
)
from homeassistant.components.photoptimizer.executor import PhotoptimizerExecutor
from homeassistant.components.photoptimizer.models import (
    DeferrableLoadDefinition,
    ExecutionPlan,
    ExecutionSlotCommand,
    OperationMode,
    PublishedEntityState,
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


def _build_growatt_executor(hass: HomeAssistant) -> PhotoptimizerExecutor:
    """Create executor with Growatt mapping."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_INVERTER_TYPE: INVERTER_TYPE_GROWATT,
            CONF_INVERTER_MODE_ENTITY: "select.growatt_mode",
            CONF_INVERTER_DISCHARGE_POWER_ENTITY: "number.growatt_discharge_power",
            CONF_INVERTER_CHARGE_POWER_ENTITY: "number.growatt_charge_power",
            CONF_GROWATT_AC_CHARGE_SWITCH_ENTITY: "switch.growatt_ac_charge",
            CONF_GROWATT_DEVICE_ID: "test_device_123",
            CONF_GROWATT_INVERTER_VARIANT: GROWATT_VARIANT_SPH,
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
    async_mock_service(hass, "switch", "turn_off")
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
    async_mock_service(hass, "switch", "turn_off")
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
    async_mock_service(hass, "switch", "turn_off")
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


async def test_executor_applies_deferrable_load_switch_state(
    hass: HomeAssistant,
) -> None:
    """Apply EMHASS deferrable-load output to a switch entity."""
    executor = _build_executor(hass)
    published_entities = {
        "deferrable_load_0": PublishedEntityState(
            entity_id="sensor.p_deferrable0",
            state="500",
            attributes={},
        )
    }
    deferrable_loads = [
        DeferrableLoadDefinition(
            name="Dishwasher",
            entity_id="switch.dishwasher",
            nominal_power_w=1200.0,
            operating_minutes=90,
        )
    ]

    switch_calls = async_mock_service(hass, "switch", "turn_on")
    applied = await executor.async_execute_deferrable_loads(
        published_entities,
        deferrable_loads,
    )

    assert applied is True
    assert len(switch_calls) == 1
    assert switch_calls[0].data == {"entity_id": "switch.dishwasher"}


async def test_executor_applies_growatt_discharge_command(hass: HomeAssistant) -> None:
    """Apply Growatt discharge command via select/number service calls."""
    executor = _build_growatt_executor(hass)
    execution_plan = ExecutionPlan(
        slots=[
            ExecutionSlotCommand(
                slot_start=dt_util.utcnow(),
                p_bat_cmd=700,
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
    switch_calls = async_mock_service(hass, "switch", "turn_off")
    growatt_service_calls = async_mock_service(
        hass, "growatt_server", "write_ac_discharge_times"
    )
    applied = await executor.async_execute_plan(execution_plan)

    assert applied is True
    # Should set discharge power and mode.
    assert len(number_calls) == 1
    assert number_calls[0].data["entity_id"] == "number.growatt_discharge_power"
    assert number_calls[0].data["value"] == 14  # (700 / 5000) * 100
    assert len(select_calls) == 1
    assert select_calls[0].data == {
        "entity_id": "select.growatt_mode",
        "option": "eco_discharge",
    }
    # Should turn off AC charge.
    assert len(switch_calls) >= 1
    assert any(s.data["entity_id"] == "switch.growatt_ac_charge" for s in switch_calls)
    # Should call Growatt variant service.
    assert len(growatt_service_calls) >= 1


async def test_executor_applies_growatt_charge_command(hass: HomeAssistant) -> None:
    """Apply Growatt charge command via select/number service calls."""
    executor = _build_growatt_executor(hass)
    execution_plan = ExecutionPlan(
        slots=[
            ExecutionSlotCommand(
                slot_start=dt_util.utcnow(),
                p_bat_cmd=-600,
                soc_target=90,
                grid_limit=0,
                op_mode=OperationMode.FORCED_CHARGE,
            )
        ],
        step_minutes=5,
        timestamp=dt_util.utcnow(),
        valid=True,
    )

    number_calls = async_mock_service(hass, "number", "set_value")
    select_calls = async_mock_service(hass, "select", "select_option")
    switch_calls = async_mock_service(hass, "switch", "turn_on")
    growatt_service_calls = async_mock_service(
        hass, "growatt_server", "write_ac_charge_times"
    )
    applied = await executor.async_execute_plan(execution_plan)

    assert applied is True
    # Should set charge power and mode.
    assert len(number_calls) == 1
    assert number_calls[0].data["entity_id"] == "number.growatt_charge_power"
    assert number_calls[0].data["value"] == 12  # (600 / 5000) * 100
    assert len(select_calls) == 1
    assert select_calls[0].data == {
        "entity_id": "select.growatt_mode",
        "option": "eco_charge",
    }
    # Should turn on AC charge.
    assert len(switch_calls) >= 1
    assert any(s.data["entity_id"] == "switch.growatt_ac_charge" for s in switch_calls)
    # Should call Growatt variant service.
    assert len(growatt_service_calls) >= 1


async def test_executor_applies_growatt_idle_command(hass: HomeAssistant) -> None:
    """Apply Growatt idle command (AUTO mode)."""
    executor = _build_growatt_executor(hass)
    execution_plan = ExecutionPlan(
        slots=[
            ExecutionSlotCommand(
                slot_start=dt_util.utcnow(),
                p_bat_cmd=0,
                soc_target=50,
                grid_limit=0,
                op_mode=OperationMode.AUTO,
            )
        ],
        step_minutes=5,
        timestamp=dt_util.utcnow(),
        valid=True,
    )

    number_calls = async_mock_service(hass, "number", "set_value")
    select_calls = async_mock_service(hass, "select", "select_option")
    async_mock_service(hass, "switch", "turn_off")
    applied = await executor.async_execute_plan(execution_plan)

    assert applied is True
    # Should set mode to general (idle/auto).
    assert len(select_calls) == 1
    assert select_calls[0].data == {
        "entity_id": "select.growatt_mode",
        "option": "general",
    }
    # Should zero out power settings.
    assert len(number_calls) == 2
    powers_set = [c.data["value"] for c in number_calls]
    assert all(v == 0 for v in powers_set)
