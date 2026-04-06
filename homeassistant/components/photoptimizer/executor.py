"""Executor that applies EMHASS output to configured inverter controls."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Protocol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_CHARGE_POWER_MAX,
    CONF_BATTERY_DISCHARGE_POWER_MAX,
    CONF_GROWATT_AC_CHARGE_SWITCH_ENTITY,
    CONF_GROWATT_DEVICE_ID,
    CONF_GROWATT_INVERTER_VARIANT,
    CONF_INVERTER_CHARGE_POWER_ENTITY,
    CONF_INVERTER_DISCHARGE_POWER_ENTITY,
    CONF_INVERTER_MODE_ENTITY,
    CONF_INVERTER_TYPE,
    DEFAULT_BATTERY_CHARGE_POWER_MAX,
    DEFAULT_BATTERY_DISCHARGE_POWER_MAX,
    GROWATT_VARIANT_AUTO,
    INVERTER_TYPE_GOODWE,
    INVERTER_TYPE_GROWATT,
)
from .goodwe_control import GoodweControlAdapter
from .growatt_control import GrowattControlAdapter
from .models import ExecutionPlan, ExecutionSlotCommand, OperationMode

_LOGGER = logging.getLogger(__name__)
_MAX_ACCEPTABLE_SLOT_AGE = timedelta(minutes=30)


class _InverterControlAdapter(Protocol):
    """Protocol for inverter-specific control adapters."""

    async def async_apply(self, command: ExecutionSlotCommand) -> None:
        """Apply one normalized command."""


class PhotoptimizerExecutor:
    """Execute current-slot battery command from normalized execution plan."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize executor for one config entry."""
        self._hass = hass
        self._entry = entry
        self._last_signature: tuple[datetime, int, str] | None = None
        self._controller = self._build_controller()

    async def async_execute_plan(self, execution_plan: ExecutionPlan | None) -> bool:
        """Apply current command from normalized EMHASS execution plan.

        Returns True when a command was effectively sent.
        """
        if execution_plan is None:
            return False

        command = self._select_current_command(execution_plan)
        if command is None:
            if not execution_plan.valid:
                command = self._fallback_command()
            else:
                return False

        signature = (command.slot_start, command.p_bat_cmd, command.op_mode.value)
        if signature == self._last_signature:
            return False

        await self._controller.async_apply(command)
        self._last_signature = signature
        return True

    def _build_controller(self) -> _InverterControlAdapter:
        inverter_type = self._entry.data.get(CONF_INVERTER_TYPE)
        mode_entity = self._entry.data.get(CONF_INVERTER_MODE_ENTITY)
        charge_entity = self._entry.data.get(CONF_INVERTER_CHARGE_POWER_ENTITY)
        discharge_entity = self._entry.data.get(CONF_INVERTER_DISCHARGE_POWER_ENTITY)
        max_charge_w = float(
            self._entry.data.get(
                CONF_BATTERY_CHARGE_POWER_MAX,
                DEFAULT_BATTERY_CHARGE_POWER_MAX,
            )
        )
        max_discharge_w = float(
            self._entry.data.get(
                CONF_BATTERY_DISCHARGE_POWER_MAX,
                DEFAULT_BATTERY_DISCHARGE_POWER_MAX,
            )
        )

        if inverter_type == INVERTER_TYPE_GROWATT:
            return GrowattControlAdapter(
                self._hass,
                mode_entity_id=mode_entity,
                charge_power_entity_id=charge_entity,
                discharge_power_entity_id=discharge_entity,
                ac_charge_switch_entity_id=self._entry.data.get(
                    CONF_GROWATT_AC_CHARGE_SWITCH_ENTITY
                ),
                growatt_device_id=self._entry.data.get(CONF_GROWATT_DEVICE_ID),
                growatt_variant=self._entry.data.get(
                    CONF_GROWATT_INVERTER_VARIANT,
                    GROWATT_VARIANT_AUTO,
                ),
                max_charge_power_w=max_charge_w,
                max_discharge_power_w=max_discharge_w,
            )

        if inverter_type == INVERTER_TYPE_GOODWE:
            return GoodweControlAdapter(
                self._hass,
                mode_entity_id=mode_entity,
                charge_power_entity_id=charge_entity,
                discharge_power_entity_id=discharge_entity,
                max_charge_power_w=max_charge_w,
                max_discharge_power_w=max_discharge_w,
            )

        _LOGGER.warning(
            "Executor disabled: unsupported inverter type '%s'", inverter_type
        )
        return _NoopControlAdapter()

    def _select_current_command(
        self,
        execution_plan: ExecutionPlan,
    ) -> ExecutionSlotCommand | None:
        """Select the nearest command slot from a normalized execution plan."""
        if not execution_plan.slots:
            return None

        now_utc = dt_util.utcnow()
        for slot in execution_plan.slots:
            if now_utc - slot.slot_start <= _MAX_ACCEPTABLE_SLOT_AGE:
                return slot

        _LOGGER.warning(
            "Executor skipped stale plan slots: plan_ts=%s now=%s source=%s",
            execution_plan.timestamp.isoformat(),
            now_utc.isoformat(),
            execution_plan.source,
        )
        return None

    def _fallback_command(self) -> ExecutionSlotCommand:
        return ExecutionSlotCommand(
            slot_start=dt_util.utcnow(),
            p_bat_cmd=0,
            soc_target=0,
            grid_limit=0,
            op_mode=OperationMode.AUTO,
        )


class _NoopControlAdapter:
    """Safe fallback adapter when control is not configured."""

    async def async_apply(self, command: ExecutionSlotCommand) -> None:
        _LOGGER.debug(
            "Noop control adapter: ignoring command mode=%s power=%s",
            command.op_mode.value,
            command.p_bat_cmd,
        )
