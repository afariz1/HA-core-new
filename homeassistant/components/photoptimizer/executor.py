"""Executor that applies EMHASS output to configured inverter controls."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .inverter_factory import create_inverter_adapter
from .models import (
    DeferrableLoadDefinition,
    ExecutionPlan,
    ExecutionSlotCommand,
    OperationMode,
    PublishedEntityState,
)

_LOGGER = logging.getLogger(__name__)
_MAX_ACCEPTABLE_SLOT_AGE = timedelta(minutes=30)
_LOAD_POWER_THRESHOLD_W = 50.0


class PhotoptimizerExecutor:
    """Execute current-slot battery command from normalized execution plan."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize executor for one config entry."""
        self._hass = hass
        self._entry = entry
        self._last_signature: tuple[datetime, int, str] | None = None
        self._last_deferrable_signatures: dict[str, bool] = {}
        self._controller = create_inverter_adapter(hass, entry)

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

    async def async_execute_deferrable_loads(
        self,
        published_entities: dict[str, PublishedEntityState],
        deferrable_loads: list[DeferrableLoadDefinition],
    ) -> bool:
        """Apply the current EMHASS deferrable-load state to switch entities."""
        applied = False

        for index, load in enumerate(deferrable_loads):
            entity_key = f"deferrable_load_{index}"
            published_entity = published_entities.get(entity_key)
            if published_entity is None:
                continue

            desired_on = self._current_load_state(published_entity)
            if desired_on is None:
                continue

            if self._last_deferrable_signatures.get(load.entity_id) == desired_on:
                continue

            service_name = "turn_on" if desired_on else "turn_off"
            await self._hass.services.async_call(
                "switch",
                service_name,
                {"entity_id": load.entity_id},
                blocking=True,
            )
            self._last_deferrable_signatures[load.entity_id] = desired_on
            applied = True

            _LOGGER.debug(
                "Applied deferrable load %s -> %s via %s",
                load.name,
                service_name,
                load.entity_id,
            )

        return applied

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

    def _current_load_state(
        self, published_entity: PublishedEntityState
    ) -> bool | None:
        """Return desired on/off state from EMHASS load forecast."""
        power = self._coerce_load_power(published_entity.state)
        if power is None:
            return None

        return power > _LOAD_POWER_THRESHOLD_W

    def _coerce_load_power(self, value: str | None) -> float | None:
        """Convert EMHASS load forecast state to Watts when possible."""
        if value is None:
            return None

        try:
            numeric = float(value)
        except TypeError, ValueError:
            return None

        return numeric
