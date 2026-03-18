"""Platform for sensor integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
from numbers import Real

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import PhotoptimizerCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PhotoptimizerSensorEntityDescription(SensorEntityDescription):
    """Sensor description extended with a value extractor."""

    value_fn: Callable[[dict], StateType] = lambda _: None


def _solar(attr: str) -> Callable[[dict], StateType]:
    """Read a named attribute from the forecast_solar estimate."""

    def _fn(data: dict) -> StateType:
        raw = (data.get("raw") or {}).get("forecast_solar")
        return getattr(raw, attr, None) if raw is not None else None

    return _fn


def _timeline_now(field: str) -> Callable[[dict], StateType]:
    """Read a field from the first (current-hour) timeline bucket."""

    def _fn(data: dict) -> StateType:
        tl: list[dict] = data.get("timeline") or []
        if not tl:
            return None
        value = tl[0].get(field)
        return round(float(value), 4) if value is not None else None

    return _fn


def _timeline_sum(field: str) -> Callable[[dict], StateType]:
    """Sum a field across all timeline buckets."""

    def _fn(data: dict) -> StateType:
        tl: list[dict] = data.get("timeline") or []
        if not tl:
            return None
        return round(sum(b.get(field, 0.0) for b in tl), 3)

    return _fn


def _emhass_table(index: int, field: str) -> Callable[[dict], StateType]:
    """Read a future value from a published EMHASS entity attribute table."""

    def _coerce_float(value: object) -> float | None:
        if isinstance(value, str) or (isinstance(value, Real) and not isinstance(value, bool)):
            try:
                return float(value)
            except ValueError:
                return None

        return None

    def _sorted_schedule(attributes: dict[str, object]) -> list[tuple[datetime, float]]:
        schedule: list[tuple[datetime, float]] = []
        # EMHASS publishes on exact minute boundaries; rounding avoids missing
        # the "current" bucket due to seconds drift.
        now = dt_util.utcnow().replace(second=0, microsecond=0)

        for key, value in attributes.items():
            dt_value = dt_util.parse_datetime(str(key))
            numeric = _coerce_float(value)
            if dt_value is None or numeric is None:
                continue

            dt_utc = dt_util.as_utc(dt_value)
            if dt_utc < now:
                continue

            schedule.append((dt_utc, numeric))

        schedule.sort(key=lambda item: item[0])
        return schedule

    def _fn(data: dict) -> StateType:
        published_entities = (data.get("emhass") or {}).get("published_entities") or {}
        battery_forecast = published_entities.get("battery_forecast") or {}
        attributes = battery_forecast.get("attributes") or {}
        # EMHASS future values are exposed as nested attributes, e.g.
        # {"p_batt_forecast": {"2026-03-18T22:00:00+00:00": 123.4, ...}}
        # but we fall back to flat timestamp->value mapping if needed.
        if isinstance(attributes, dict):
            nested = attributes.get(field)
            if isinstance(nested, dict):
                table = nested
            else:
                # Fallback: if EMHASS uses a different top-level key, pick the
                # first nested timestamp->value dict.
                for maybe_table in attributes.values():
                    if isinstance(maybe_table, dict):
                        table = maybe_table
                        break
                else:
                    table = attributes
        else:
            table = attributes

        schedule = _sorted_schedule(table)
        if len(schedule) <= index:
            return None

        _, value = schedule[index]
        return round(value, 2)

    return _fn


def _battery_soc(data: dict) -> StateType:
    """Return the SOC percentage used at optimization time."""
    inputs = data.get("inputs")
    if inputs is None:
        return None
    soc_init = inputs.get("battery_soc")
    if soc_init is None:
        return None
    return round(float(soc_init) * 100, 1)


def _emhass_current_state(key: str) -> Callable[[dict], StateType]:
    """Read the current state of a published EMHASS entity."""

    def _fn(data: dict) -> StateType:
        published_entities = (data.get("emhass") or {}).get("published_entities") or {}
        entity = published_entities.get(key) or {}
        state = entity.get("state")
        try:
            return round(float(state), 2) if state is not None else None
        except TypeError, ValueError:
            return None

    return _fn


def _emhass_current_state_str(key: str) -> Callable[[dict], StateType]:
    """Read the current state of a published EMHASS entity as-is."""

    def _fn(data: dict) -> StateType:
        published_entities = (data.get("emhass") or {}).get("published_entities") or {}
        entity = published_entities.get(key) or {}
        state = entity.get("state")
        return state if state is not None else None

    return _fn


def _would_apply_battery_power(data: dict) -> StateType:
    """Return the battery power command that would be applied now (preview only)."""
    would_apply = data.get("would_apply") or {}
    value = would_apply.get("battery_power_w")
    try:
        return round(float(value), 2) if value is not None else None
    except TypeError, ValueError:
        return None


# ── Sensor catalogue ─────────────────────────────────────────────────────────
#
# Inputs
#   current_hour_price          – price for the current hour (CZK/kWh)
#   current_hour_pv_forecast    – PV yield expected this hour (kWh)
#   current_hour_load_forecast  – consumption expected this hour (kWh)
#   total_pv_forecast           – sum of PV over the full optimisation horizon
#   total_load_forecast         – sum of load over the full optimisation horizon
#   battery_soc_initial         – battery SOC fed into EMHASS (%)
#
# Forecast.Solar
#   energy_production_today     – total estimated PV yield today (Wh)
#   energy_production_tomorrow  – total estimated PV yield tomorrow (Wh)
#   power_production_now        – estimated PV power right now (W)
#
# EMHASS outputs
#   emhass_battery_power_now        – battery command for the current hour (W)
#   emhass_battery_power_next_hour  – battery command for the next hour (W)
#   would_apply_battery_power       – command preview that would be applied (W)
# ─────────────────────────────────────────────────────────────────────────────
SENSOR_TYPES: tuple[PhotoptimizerSensorEntityDescription, ...] = (
    # Input: current-hour snapshot
    PhotoptimizerSensorEntityDescription(
        key="current_hour_price",
        name="Photoptimizer current hour electricity price",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="CZK/kWh",
        value_fn=_timeline_now("price"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="current_hour_pv_forecast",
        name="Photoptimizer current hour PV forecast",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=_timeline_now("pv"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="current_hour_load_forecast",
        name="Photoptimizer current hour load forecast",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=_timeline_now("load"),
    ),
    # Input: horizon aggregates
    PhotoptimizerSensorEntityDescription(
        key="total_pv_forecast",
        name="Photoptimizer total PV forecast (horizon)",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=_timeline_sum("pv"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="total_load_forecast",
        name="Photoptimizer total load forecast (horizon)",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=_timeline_sum("load"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="battery_soc_initial",
        name="Photoptimizer battery SOC at optimization time",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=_battery_soc,
    ),
    # Forecast.Solar
    PhotoptimizerSensorEntityDescription(
        key="energy_production_today",
        name="Photoptimizer energy production today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        value_fn=_solar("energy_production_today"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="energy_production_tomorrow",
        name="Photoptimizer energy production tomorrow",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        value_fn=_solar("energy_production_tomorrow"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="power_production_now",
        name="Photoptimizer power production now",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=_solar("power_production_now"),
    ),
    # EMHASS outputs
    PhotoptimizerSensorEntityDescription(
        key="emhass_battery_power_now",
        name="Photoptimizer EMHASS battery power command (now)",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=_emhass_table(0, "p_batt_forecast"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="emhass_battery_power_next_hour",
        name="Photoptimizer EMHASS battery power command (next hour)",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=_emhass_table(1, "p_batt_forecast"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="emhass_pv_forecast_now",
        name="Photoptimizer EMHASS PV power forecast (now)",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=_emhass_current_state("pv_forecast"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="emhass_load_forecast_now",
        name="Photoptimizer EMHASS load power forecast (now)",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=_emhass_current_state("load_forecast"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="emhass_grid_forecast_now",
        name="Photoptimizer EMHASS grid power forecast (now)",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=_emhass_current_state("grid_forecast"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="emhass_battery_soc_forecast_now",
        name="Photoptimizer EMHASS battery SOC forecast (now)",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=_emhass_current_state("battery_soc_forecast"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="emhass_unit_load_cost",
        name="Photoptimizer EMHASS unit load cost",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="currency/kWh",
        value_fn=_emhass_current_state("unit_load_cost"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="emhass_unit_prod_price",
        name="Photoptimizer EMHASS unit production price",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="currency/kWh",
        value_fn=_emhass_current_state("unit_prod_price"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="emhass_total_cost_fun_value",
        name="Photoptimizer EMHASS total cost function value",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="currency",
        value_fn=_emhass_current_state("cost_fun"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="emhass_optim_status",
        name="Photoptimizer EMHASS optimization status",
        native_unit_of_measurement="",
        value_fn=_emhass_current_state_str("optim_status"),
    ),
    PhotoptimizerSensorEntityDescription(
        key="would_apply_battery_power",
        name="Photoptimizer battery power that would be applied",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=_would_apply_battery_power,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Photoptimizer sensor entities."""
    coordinator: PhotoptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    _LOGGER.debug(
        "Setting up sensor platform for entry_id=%s with %s sensors",
        entry.entry_id,
        len(SENSOR_TYPES),
    )

    async_add_entities(
        PhotoptimizerSensor(coordinator, entry, description)
        for description in SENSOR_TYPES
    )


class PhotoptimizerSensor(CoordinatorEntity[PhotoptimizerCoordinator], SensorEntity):
    """Representation of a Photoptimizer sensor."""

    _attr_has_entity_name = True
    entity_description: PhotoptimizerSensorEntityDescription

    def __init__(
        self,
        coordinator: PhotoptimizerCoordinator,
        entry: ConfigEntry,
        description: PhotoptimizerSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        _LOGGER.debug(
            "Created sensor entity key=%s unique_id=%s",
            description.key,
            self._attr_unique_id,
        )

    @property
    def native_value(self) -> StateType:
        """Return the sensor value by calling the description's value_fn."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
