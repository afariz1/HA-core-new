"""Photoptimizer coordinator."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from numbers import Real
from typing import Any

from forecast_solar import ForecastSolar, ForecastSolarError

from homeassistant.components.recorder.statistics import (
    StatisticsRow,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_EFFICIENCY_ROUND_TRIP,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_SOC_RESERVE_PERCENT,
    CONF_ELECTRICITY_PRICE_ENTITY,
    CONF_EMHASS_TOKEN,
    CONF_EMHASS_URL,
    CONF_HORIZON_HOURS,
    CONF_LOAD_FORECAST_ENTITY,
    CONF_PV_FORECAST_ENTITY,
    CONF_TIMEZONE,
    CONF_WEAR_COST_PER_KWH,
    DEFAULT_BATTERY_EFFICIENCY_ROUND_TRIP,
    DEFAULT_BATTERY_SOC_RESERVE_PERCENT,
    DEFAULT_EMHASS_URL,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_WEAR_COST_PER_KWH,
)
from .emhass_client import EmhassClient
from .models import OptimizationBucket, OptimizationInputs, PublishedEntityState

_LOGGER = logging.getLogger(__name__)


class PhotoptimizerCoordinator(DataUpdateCoordinator[dict]):
    """Aggregate inputs for EMHASS and expose the combined result."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: ForecastSolar
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="photoptimizer forecast",
            update_method=self._async_update_data,
            config_entry=entry,
        )
        self.client = client
        self.entry = entry
        self.emhass_url = entry.data.get(CONF_EMHASS_URL, DEFAULT_EMHASS_URL)
        self.emhass_token = entry.data.get(CONF_EMHASS_TOKEN)
        self.emhass = EmhassClient(
            hass,
            self.emhass_url,
            self.emhass_token,
            battery_capacity_kwh=float(
                entry.data.get(
                    CONF_BATTERY_CAPACITY_KWH,
                    5.0,
                )
            ),
            battery_efficiency=(
                float(
                    entry.data.get(
                        CONF_BATTERY_EFFICIENCY_ROUND_TRIP,
                        DEFAULT_BATTERY_EFFICIENCY_ROUND_TRIP,
                    )
                )
                / 100.0
            ),
            battery_soc_reserve=(
                entry.data.get(
                    CONF_BATTERY_SOC_RESERVE_PERCENT,
                    DEFAULT_BATTERY_SOC_RESERVE_PERCENT,
                )
                / 100.0
            ),
            wear_cost_per_kwh=entry.data.get(
                CONF_WEAR_COST_PER_KWH,
                DEFAULT_WEAR_COST_PER_KWH,
            ),
        )
        self._operation_lock = asyncio.Lock()
        self._last_optimization_utc: datetime | None = None
        self._last_publish_utc: datetime | None = None
        self._last_runtimeparams: dict[str, Any] = {}
        self._last_optimization_response: dict[str, Any] = {}
        self._last_publish_response: dict[str, Any] = {}
        self._last_published_entities: dict[str, PublishedEntityState] = {}
        # Controlled by the integration switch; when disabled we skip scheduled runs.
        self._optimizer_enabled: bool = True
        _LOGGER.debug(
            "Coordinator initialized for entry_id=%s emhass_url=%s token=%s",
            entry.entry_id,
            self.emhass_url,
            "set" if self.emhass_token else "unset",
        )

    @property
    def optimizer_enabled(self) -> bool:
        """Return whether optimization/publish should run."""
        return self._optimizer_enabled

    async def async_set_optimizer_enabled(self, enabled: bool) -> None:
        """Enable/disable optimization runs."""
        self._optimizer_enabled = enabled

    async def async_run_startup_bootstrap(self) -> None:
        """Run daily optimization and then hourly publish."""
        # The startup sequence should respect the current enable flag.
        if not self._optimizer_enabled:
            return

        try:
            await self.async_run_daily_optimization()
        except UpdateFailed as err:
            _LOGGER.warning("Startup EMHASS optimization failed: %s", err)
            return

        try:
            await self.async_run_hourly_publish()
        except UpdateFailed as err:
            _LOGGER.warning("Startup EMHASS publish-data failed: %s", err)

    def _coerce_float(self, value: object) -> float | None:
        """Convert arbitrary value to float when possible."""
        if isinstance(value, str) or (isinstance(value, Real) and not isinstance(value, bool)):
            try:
                return float(value)
            except ValueError:
                return None

        return None

    async def _async_get_state_with_startup_wait(self, entity_id: str) -> State | None:
        """Return entity state, waiting briefly during startup for late entities."""
        state = self.hass.states.get(entity_id)
        if state is not None or self.hass.is_running:
            if state is None:
                _LOGGER.debug(
                    "Entity %s unavailable (system already running)", entity_id
                )
            return state

        _LOGGER.debug("Entity %s missing during startup, waiting up to 8s", entity_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 8.0

        while loop.time() < deadline:
            await asyncio.sleep(0.25)
            state = self.hass.states.get(entity_id)
            if state is not None:
                _LOGGER.debug(
                    "Entity %s became available during startup wait", entity_id
                )
                return state

        _LOGGER.debug("Entity %s not available after startup wait", entity_id)
        return None

    def _extract_would_apply(
        self, published_entities: dict[str, PublishedEntityState]
    ) -> dict[str, Any]:
        """Extract a simple battery command preview from EMHASS published data."""
        battery_entity = published_entities.get("battery_forecast")
        if battery_entity is None:
            return {
                "battery_power_w": None,
                "effective_at": None,
                "source": "missing_battery_forecast",
            }

        current_value = self._coerce_float(battery_entity.state)
        if current_value is not None:
            return {
                "battery_power_w": round(current_value, 2),
                "effective_at": dt_util.utcnow().isoformat(),
                "source": "state",
            }

        nearest_time: datetime | None = None
        nearest_value: float | None = None
        # Round to the minute so we can match EMHASS timestamps that are
        # typically published on exact minute boundaries.
        now_utc = dt_util.utcnow().replace(second=0, microsecond=0)

        # EMHASS exposes future values as nested attributes, e.g.
        # {"p_batt_forecast": {"2026-03-18T22:00:00+00:00": 123.4, ...}}
        # but sometimes it may also be a flat timestamp->value mapping.
        attr_table: dict[str, Any] = {}
        if isinstance(battery_entity.attributes, dict):
            nested = battery_entity.attributes.get("p_batt_forecast")
            if isinstance(nested, dict):
                attr_table = nested
            else:
                # Fallback: if EMHASS uses a different top-level key for the
                # timestamp->value table, pick the first nested dict.
                for maybe_table in battery_entity.attributes.values():
                    if isinstance(maybe_table, dict):
                        attr_table = maybe_table
                        break
                else:
                    attr_table = battery_entity.attributes

        for key, value in attr_table.items():
            parsed = dt_util.parse_datetime(str(key))
            numeric = self._coerce_float(value)
            if parsed is None or numeric is None:
                continue

            parsed_utc = dt_util.as_utc(parsed)
            if parsed_utc < now_utc:
                continue

            if nearest_time is None or parsed_utc < nearest_time:
                nearest_time = parsed_utc
                nearest_value = numeric

        if nearest_value is None and _LOGGER.isEnabledFor(logging.DEBUG):
            if isinstance(battery_entity.attributes, dict):
                attr_keys = list(battery_entity.attributes.keys())
            else:
                attr_keys = []
            _LOGGER.debug(
                "Could not derive would_apply battery power; state=%s attr_keys=%s",
                battery_entity.state,
                attr_keys,
            )

        return {
            "battery_power_w": None
            if nearest_value is None
            else round(nearest_value, 2),
            "effective_at": None if nearest_time is None else nearest_time.isoformat(),
            "source": "forecast_attribute",
        }

    def _build_result(
        self,
        optimization_inputs: OptimizationInputs,
        raw_pv: Any | None,
    ) -> dict[str, Any]:
        """Compose coordinator payload shared by all entities."""
        published_entities = {
            key: entity.as_dict()
            for key, entity in self._last_published_entities.items()
        }

        return {
            "timeline": [bucket.as_dict() for bucket in optimization_inputs.timeline],
            "inputs": {
                "battery_soc": optimization_inputs.battery_soc,
                "prediction_horizon": optimization_inputs.prediction_horizon,
                "optimization_time_step_minutes": optimization_inputs.optimization_time_step_minutes,
            },
            "raw": {"forecast_solar": raw_pv},
            "emhass": {
                "runtimeparams": self._last_runtimeparams,
                "optimization_response": self._last_optimization_response,
                "publish_response": self._last_publish_response,
                "published_entities": published_entities,
            },
            "would_apply": self._extract_would_apply(self._last_published_entities),
            "schedule": {
                "last_optimization_utc": (
                    None
                    if self._last_optimization_utc is None
                    else self._last_optimization_utc.isoformat()
                ),
                "last_publish_utc": (
                    None
                    if self._last_publish_utc is None
                    else self._last_publish_utc.isoformat()
                ),
            },
        }

    async def _async_collect_inputs(self) -> tuple[OptimizationInputs, Any | None]:
        """Collect timeline and plant inputs needed for EMHASS calls."""
        timeline: list[OptimizationBucket] = []
        horizon_hours = self.entry.data.get(CONF_HORIZON_HOURS, DEFAULT_HORIZON_HOURS)
        tz_name = self.entry.data.get(CONF_TIMEZONE) or self.hass.config.time_zone
        tz = dt_util.get_time_zone(tz_name) or dt_util.UTC
        now = dt_util.now(tz).replace(minute=0, second=0, microsecond=0)
        _LOGGER.debug(
            "Collecting inputs: horizon_hours=%s timezone=%s start=%s",
            horizon_hours,
            tz_name,
            now.isoformat(),
        )

        for hour_offset in range(horizon_hours):
            bucket_start = now + timedelta(hours=hour_offset)
            timeline.append(
                OptimizationBucket(
                    start=bucket_start,
                    price=0.0,
                    pv=0.0,
                    load=0.0,
                )
            )

        price_entity = self.entry.data.get(CONF_ELECTRICITY_PRICE_ENTITY)
        if price_entity:
            _LOGGER.debug("Using electricity price entity: %s", price_entity)
            await self._hourly_from_price_entity(price_entity, timeline)
        else:
            _LOGGER.debug("No electricity price entity configured")

        pv_entity = self.entry.data.get(CONF_PV_FORECAST_ENTITY)
        raw_pv = None
        if pv_entity:
            _LOGGER.debug("Using PV forecast entity: %s", pv_entity)
            await self._hourly_from_pv_entity(pv_entity, timeline)
        else:
            _LOGGER.debug("Using Forecast.Solar client for PV forecast")
            raw_pv = await self.client.estimate()
            await self._hourly_from_forecast_solar(timeline, raw_pv)

        load_entity = self.entry.data.get(CONF_LOAD_FORECAST_ENTITY)
        if load_entity:
            _LOGGER.debug("Using load forecast entity: %s", load_entity)
            await self._hourly_from_load_entity(load_entity, timeline)
        else:
            _LOGGER.debug("No load forecast entity configured, building profile")
            load_profile = await self._build_load_profile(None)
            await self._hourly_from_load_profile(timeline, load_profile)

        _LOGGER.debug(
            "Input collection finished with %s timeline buckets", len(timeline)
        )

        return (
            OptimizationInputs(
                timeline=timeline,
                battery_soc=self._read_battery_soc(),
                raw_forecast_solar=raw_pv,
            ),
            raw_pv,
        )

    async def _hourly_from_price_entity(
        self, entity_id: str, buckets: list[OptimizationBucket]
    ) -> None:
        state = await self._async_get_state_with_startup_wait(entity_id)

        if state is None:
            raise UpdateFailed(f"Price entity {entity_id} not found")

        tz_name = self.entry.data.get(CONF_TIMEZONE) or self.hass.config.time_zone
        tz = dt_util.get_time_zone(tz_name) or dt_util.UTC

        bucket_index = {bucket.start: bucket for bucket in buckets}
        mapped_points = 0

        for key, value in state.attributes.items():
            if isinstance(value, (int, float)):
                dt = dt_util.parse_datetime(str(key))
                if dt is None:
                    continue
                dt_local = dt_util.as_local(dt).astimezone(tz)
                hour_start = dt_local.replace(minute=0, second=0, microsecond=0)
                if hour_start in bucket_index:
                    bucket_index[hour_start].price = float(value)
                    mapped_points += 1

        last_price: float | None = None
        filled_points = 0
        for bucket in buckets:
            if bucket.price != 0.0:
                last_price = bucket.price
            elif last_price is not None:
                bucket.price = last_price
                filled_points += 1

        _LOGGER.debug(
            "Price timeline populated from %s: mapped=%s forward_filled=%s",
            entity_id,
            mapped_points,
            filled_points,
        )

    async def _hourly_from_load_entity(
        self, entity_id: str, buckets: list[OptimizationBucket]
    ) -> None:
        state = await self._async_get_state_with_startup_wait(entity_id)

        if state is None:
            raise UpdateFailed(f"Load entity {entity_id} not found")

        tz_name = self.entry.data.get(CONF_TIMEZONE) or self.hass.config.time_zone
        tz = dt_util.get_time_zone(tz_name) or dt_util.UTC

        bucket_index = {bucket.start: bucket for bucket in buckets}
        mapped_points = 0

        for key, value in state.attributes.items():
            if not isinstance(value, (int, float)):
                continue
            dt = dt_util.parse_datetime(str(key))
            if dt is None:
                continue
            dt_local = dt_util.as_local(dt).astimezone(tz)
            hour_start = dt_local.replace(minute=0, second=0, microsecond=0)
            if hour_start in bucket_index:
                bucket_index[hour_start].load = float(value) / 1000.0
                mapped_points += 1

        last_load: float | None = None
        filled_points = 0
        for bucket in buckets:
            if bucket.load != 0.0:
                last_load = bucket.load
            elif last_load is not None:
                bucket.load = last_load
                filled_points += 1

        _LOGGER.debug(
            "Load timeline populated from %s: mapped=%s forward_filled=%s",
            entity_id,
            mapped_points,
            filled_points,
        )

    async def _hourly_from_forecast_solar(
        self, buckets: list[OptimizationBucket], raw_pv
    ) -> None:
        tz_name = self.entry.data.get(CONF_TIMEZONE) or self.hass.config.time_zone
        tz = dt_util.get_time_zone(tz_name) or dt_util.UTC

        bucket_index = {bucket.start: bucket for bucket in buckets}
        mapped_points = 0

        for dt_utc, wh in raw_pv.wh_period.items():
            dt_local = dt_util.as_local(dt_utc).astimezone(tz)
            hour_start = dt_local.replace(minute=0, second=0, microsecond=0)
            if hour_start in bucket_index:
                bucket_index[hour_start].pv += float(wh) / 1000.0
            mapped_points += 1

        _LOGGER.debug(
            "PV timeline populated from Forecast.Solar: mapped=%s", mapped_points
        )

    async def _hourly_from_pv_entity(
        self, entity_id: str, buckets: list[OptimizationBucket]
    ) -> None:
        """Fill PV from a forecast entity with datetime-keyed attributes."""

        state = await self._async_get_state_with_startup_wait(entity_id)
        if state is None:
            raise UpdateFailed(f"PV forecast entity {entity_id} not found")

        tz_name = self.entry.data.get(CONF_TIMEZONE) or self.hass.config.time_zone
        tz = dt_util.get_time_zone(tz_name) or dt_util.UTC
        bucket_index = {bucket.start: bucket for bucket in buckets}
        mapped_points = 0

        for key, value in state.attributes.items():
            if not isinstance(value, (int, float)):
                continue
            dt = dt_util.parse_datetime(str(key))
            if dt is None:
                continue
            dt_local = dt_util.as_local(dt).astimezone(tz)
            hour_start = dt_local.replace(minute=0, second=0, microsecond=0)
            if hour_start in bucket_index:
                val = float(value)
                if val > 50:
                    val = val / 1000.0
                bucket_index[hour_start].pv = val
                mapped_points += 1

        _LOGGER.debug(
            "PV timeline populated from %s: mapped=%s", entity_id, mapped_points
        )

    async def _hourly_from_load_profile(
        self, buckets: list[OptimizationBucket], profile: list[float]
    ) -> None:
        """Fill load buckets from a 24-hour profile."""

        for bucket in buckets:
            bucket.load = profile[bucket.start.hour]

        _LOGGER.debug(
            "Load timeline populated from profile with %s hourly values",
            len(profile),
        )

    async def _build_load_profile(self, entity_id: str | None) -> list[float]:
        """Build a simple 24h load profile from history; fallback to defaults.

        - Pull hourly statistics for the last 7 days (mean).
        - Average by hour-of-day to get 24 values.
        - Convert W to kWh for 1h buckets; if we get sums, treat them as kWh.
        - Fill gaps with a baked-in default profile.
        """
        default_profile = [
            0.4,
            0.35,
            0.35,
            0.35,
            0.35,
            0.4,
            0.45,
            0.5,
            0.55,
            0.6,
            0.65,
            0.7,
            0.7,
            0.7,
            0.75,
            0.8,
            0.9,
            1.2,
            1.4,
            1.5,
            1.3,
            1.0,
            0.8,
            0.6,
        ]

        if not entity_id:
            _LOGGER.debug("Using default load profile (no entity configured)")
            return default_profile

        start = dt_util.utcnow() - timedelta(days=7)

        def _get_stats(
            hass: HomeAssistant,
        ) -> dict[str, list[StatisticsRow]]:
            return statistics_during_period(
                hass,
                start,
                None,
                {entity_id},
                "hour",
                None,
                {"mean", "sum"},
            )

        try:
            stats = await self.hass.async_add_executor_job(_get_stats, self.hass)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Load stats query failed: %s", err)
            return default_profile

        rows = stats.get(entity_id) or []
        _LOGGER.debug("Load stats query returned %s rows for %s", len(rows), entity_id)
        hourly_totals = [0.0] * 24
        hourly_counts = [0] * 24

        for row in rows:
            start_ts = row.get("start")
            if start_ts is None or not isinstance(start_ts, datetime):
                continue
            hour = dt_util.as_local(start_ts).hour
            # Prefer mean; fall back to sum/state
            value = row.get("mean")
            if value is None:
                value = row.get("sum")
            if value is None:
                value = row.get("state")
            if value is None:
                continue
            # Treat values > 30 as Watts and convert
            val = float(value)
            if val > 30:  # assume W average over the hour
                val = val / 1000.0
            hourly_totals[hour] += val
            hourly_counts[hour] += 1

        profile: list[float] = []
        for hour in range(24):
            if hourly_counts[hour]:
                profile.append(hourly_totals[hour] / hourly_counts[hour])
            else:
                profile.append(default_profile[hour])

        _LOGGER.debug(
            "Load profile built from stats for %s (filled_hours=%s)",
            entity_id,
            sum(1 for count in hourly_counts if count),
        )

        return profile

    def _read_battery_soc(self) -> float:
        """Read and normalize the current battery SOC from Home Assistant."""
        soc_entity = self.entry.data.get(CONF_BATTERY_SOC_ENTITY)
        soc_state = self.hass.states.get(soc_entity) if soc_entity else None
        try:
            soc_value = float(soc_state.state) if soc_state and soc_state.state else 0.0
        except TypeError, ValueError:
            soc_value = 0.0

        if soc_value > 1:
            soc_value = soc_value / 100.0

        normalized_soc = max(0.0, min(1.0, soc_value))
        _LOGGER.debug(
            "Battery SOC read from %s: raw=%s normalized=%s",
            soc_entity,
            None if soc_state is None else soc_state.state,
            normalized_soc,
        )
        return normalized_soc

    def _log_timeline(self, timeline: list[OptimizationBucket]) -> None:
        """Log the aggregated inputs passed into EMHASS."""
        _LOGGER.debug(
            "Photoptimizer inputs (%d hours):\n  %s\n%s",
            len(timeline),
            f"{'Hour':<17} {'Price':>9} {'PV (kWh)':>10} {'Load (kWh)':>11}",
            "\n".join(
                f"  {bucket.start.strftime('%Y-%m-%d %H:%M')} "
                f"{bucket.price:9.4f} "
                f"{bucket.pv:10.3f} "
                f"{bucket.load:11.3f}"
                for bucket in timeline
            ),
        )

    async def _async_update_data(self) -> dict:
        """Collect input data without forcing EMHASS operations.

        Scheduled tasks call optimization and publish actions explicitly.
        """
        try:
            _LOGGER.debug("Coordinator refresh started")
            async with asyncio.timeout(30):
                optimization_inputs, raw_pv = await self._async_collect_inputs()
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    self._log_timeline(optimization_inputs.timeline)
                result = self._build_result(optimization_inputs, raw_pv)
                _LOGGER.debug(
                    "Coordinator refresh finished: horizon=%s step=%s",
                    optimization_inputs.prediction_horizon,
                    optimization_inputs.optimization_time_step_minutes,
                )
                return result

        except ForecastSolarError as err:
            raise UpdateFailed(f"Forecast.Solar API error: {err}") from err

    async def async_run_daily_optimization(self) -> None:
        """Run the daily EMHASS optimization at 17:00 schedule."""
        async with self._operation_lock:
            _LOGGER.debug("Daily optimization started")
            if not self._optimizer_enabled:
                _LOGGER.debug("Optimization disabled; skipping daily optimization")
                return
            try:
                async with asyncio.timeout(90):
                    optimization_inputs, raw_pv = await self._async_collect_inputs()
                    optimization_result = (
                        await self.emhass.async_run_naive_optimization(
                            optimization_inputs
                        )
                    )
                    if optimization_result is None:
                        raise UpdateFailed("EMHASS daily optimization failed")

                    self._last_runtimeparams = optimization_result["runtimeparams"]
                    self._last_optimization_response = optimization_result[
                        "optimization_response"
                    ]
                    self._last_optimization_utc = dt_util.utcnow()

                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        self._log_timeline(optimization_inputs.timeline)

                    self.async_set_updated_data(
                        self._build_result(optimization_inputs, raw_pv)
                    )
                    _LOGGER.debug(
                        "Daily optimization finished: runtimeparams_keys=%s response_keys=%s",
                        sorted(self._last_runtimeparams.keys()),
                        sorted(self._last_optimization_response.keys()),
                    )
            except ForecastSolarError as err:
                raise UpdateFailed(f"Forecast.Solar API error: {err}") from err

    async def async_run_hourly_publish(self) -> None:
        """Run the hourly EMHASS publish-data step."""
        async with self._operation_lock:
            _LOGGER.debug("Hourly publish started")
            if not self._optimizer_enabled:
                _LOGGER.debug("Optimization disabled; skipping hourly publish")
                return
            try:
                async with asyncio.timeout(60):
                    optimization_inputs, raw_pv = await self._async_collect_inputs()
                    publish_result = await self.emhass.async_publish_data(
                        optimization_inputs.optimization_time_step_minutes
                    )
                    if publish_result is None:
                        raise UpdateFailed("EMHASS hourly publish-data failed")

                    self._last_publish_response = publish_result["publish_response"]
                    self._last_published_entities = publish_result["published_entities"]
                    self._last_publish_utc = dt_util.utcnow()

                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        self._log_timeline(optimization_inputs.timeline)

                    self.async_set_updated_data(
                        self._build_result(optimization_inputs, raw_pv)
                    )
                    _LOGGER.debug(
                        "Hourly publish finished: published_entities=%s response_keys=%s",
                        sorted(self._last_published_entities.keys()),
                        sorted(self._last_publish_response.keys()),
                    )
            except ForecastSolarError as err:
                raise UpdateFailed(f"Forecast.Solar API error: {err}") from err
