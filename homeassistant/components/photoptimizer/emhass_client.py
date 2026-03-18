"""Client for EMHASS communication.

This module owns the full HTTP interaction contract with EMHASS:

- Building runtime parameters from coordinator inputs.
- Calling optimization and publish endpoints in the correct order.
- Reading back published entities from Home Assistant state machine.

The coordinator intentionally does not know any endpoint names or payload
shapes. Keeping that mapping here makes protocol updates easier and localized.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientError, ClientTimeout

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .models import EmhassExecutionResult, OptimizationInputs, PublishedEntityState

_LOGGER = logging.getLogger(__name__)

DEFAULT_BATTERY_CHARGE_POWER_MAX = 1000.0
DEFAULT_BATTERY_DISCHARGE_POWER_MAX = 1000.0
DEFAULT_BATTERY_MAXIMUM_STATE_OF_CHARGE = 0.9


class EmhassClient:
    """Own the complete EMHASS workflow for one Photoptimizer config entry.

    The coordinator prepares neutral aggregated inputs only. This client is the
    only place that knows how those inputs map to EMHASS runtimeparams, which
    actions need to be called, and where the published EMHASS entities live.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        url: str,
        token: str | None = None,
        *,
        battery_capacity_kwh: float,
        battery_efficiency: float,
        battery_soc_reserve: float,
        wear_cost_per_kwh: float,
    ) -> None:
        """Store session, base URL, publish targets, and runtime defaults."""
        self._hass = hass
        # Strip trailing slash once so all endpoint builders can be simple and
        # avoid accidental double-slashes.
        self._url = url.rstrip("/")
        self._session = async_get_clientsession(hass)
        self._token = token

        # Keep conservative power limits until integration exposes real inverter
        # capabilities. Values are in Watts because EMHASS expects W.
        self._battery_charge_power_max = DEFAULT_BATTERY_CHARGE_POWER_MAX
        self._battery_discharge_power_max = DEFAULT_BATTERY_DISCHARGE_POWER_MAX

        # EMHASS expects capacity in Wh. If user input is invalid/non-positive,
        # use a reasonable fallback capacity to keep optimization operational.
        self._battery_nominal_energy_capacity = (
            battery_capacity_kwh * 1000.0 if battery_capacity_kwh > 0 else 5000.0
        )

        # Clamp efficiency into a safe interval to avoid invalid optimization
        # coefficients and division edge cases on the EMHASS side.
        self._battery_efficiency = min(
            max(battery_efficiency, 0.01),
            1.0,
        )
        self._battery_soc_reserve = battery_soc_reserve
        self._wear_cost_per_kwh = wear_cost_per_kwh

        # Use documented EMHASS default entity IDs.
        self._published_entities: dict[str, dict[str, str]] = {
            "pv_forecast": {
                "entity_id": "sensor.p_pv_forecast",
                "unit_of_measurement": "W",
                "friendly_name": "PV Power Forecast",
            },
            "load_forecast": {
                "entity_id": "sensor.p_load_forecast",
                "unit_of_measurement": "W",
                "friendly_name": "Load Power Forecast",
            },
            "battery_forecast": {
                "entity_id": "sensor.p_batt_forecast",
                "unit_of_measurement": "W",
                "friendly_name": "Battery Power Forecast",
            },
            "battery_soc_forecast": {
                "entity_id": "sensor.soc_batt_forecast",
                "unit_of_measurement": "%",
                "friendly_name": "Battery SOC Forecast",
            },
            "grid_forecast": {
                "entity_id": "sensor.p_grid_forecast",
                "unit_of_measurement": "W",
                "friendly_name": "Grid Power Forecast",
            },
            "unit_load_cost": {
                "entity_id": "sensor.unit_load_cost",
                "unit_of_measurement": "currency/kWh",
                "friendly_name": "Unit Load Cost",
            },
            "unit_prod_price": {
                "entity_id": "sensor.unit_prod_price",
                "unit_of_measurement": "currency/kWh",
                "friendly_name": "Unit Prod Price",
            },
            "cost_fun": {
                "entity_id": "sensor.total_cost_fun_value",
                "unit_of_measurement": "currency",
                "friendly_name": "Total cost function value",
            },
            "optim_status": {
                "entity_id": "sensor.optim_status",
                "unit_of_measurement": "",
                "friendly_name": "EMHASS optimization status",
            },
        }

    def _headers(self) -> dict[str, str]:
        """Return headers for EMHASS HTTP requests."""
        headers = {"Content-Type": "application/json"}
        # Token is optional; support both protected and open local EMHASS setups.
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _async_check_url(self, url: str, label: str) -> bool:
        """Lightweight reachability check before hitting EMHASS.

        We treat any non-5xx response as reachable:
        - 2xx/3xx means healthy
        - 4xx often means auth/config mismatch, but server is up
        """
        try:
            async with self._session.get(
                url,
                headers=self._headers(),
                timeout=ClientTimeout(total=5),
            ) as response:
                if response.status < 500:
                    return True
                _LOGGER.error("%s unreachable (%s)", label, response.status)
        except ClientError as err:
            _LOGGER.error("%s connection error at %s: %s", label, url, err)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("%s unexpected error at %s: %s", label, url, err)
        return False

    async def _async_post_action(
        self,
        action: str,
        payload: dict[str, Any],
        *,
        timeout: int,
    ) -> dict[str, Any] | None:
        """Post one action to the EMHASS web server.

        Returns parsed JSON/dict-like response on success and ``None`` on hard
        failures where follow-up processing should stop.
        """
        endpoint = f"{self._url}/action/{action}"

        try:
            async with self._session.post(
                endpoint,
                json=payload,
                headers=self._headers(),
                timeout=ClientTimeout(total=timeout),
            ) as response:
                text = await response.text()

                # EMHASS API behavior is endpoint-specific:
                # - 5xx means server-side exception -> hard failure.
                # - publish-data can return 400 with warning logs while still
                #   publishing entities; treat as partial success.
                if response.status not in (200, 201):
                    if response.status >= 500:
                        _LOGGER.error(
                            "EMHASS internal error at %s. Check the EMHASS add-on logs for the Python traceback",
                            endpoint,
                        )
                        _LOGGER.error("EMHASS error %s: %s", response.status, text)
                        return None
                    # 400 from publish-data means "published with warnings" — the
                    # response body is a JSON array of log lines. Log it and continue.
                    _LOGGER.warning(
                        "EMHASS %s returned %s (treating as partial success): %s",
                        action,
                        response.status,
                        text,
                    )

                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    data = await response.json()
                    if isinstance(data, dict):
                        _LOGGER.debug("EMHASS %s response keys: %s", action, list(data))
                        return data
                    # Normalize non-dict JSON so callers can handle a consistent
                    # mapping return type.
                    return {"data": data, "status": response.status}

                _LOGGER.debug("EMHASS %s success %s: %s", action, response.status, text)
                return {"message": text, "status": response.status}
        except ClientError as err:
            _LOGGER.error("EMHASS connection error at %s: %s", endpoint, err)
            return None
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("EMHASS unexpected error at %s: %s", endpoint, err)
            return None

    def _build_runtimeparams(self, inputs: OptimizationInputs) -> dict[str, Any]:
        """Translate aggregated coordinator data into EMHASS runtimeparams.

        Coordinator buckets are stored in kWh for one-hour intervals; EMHASS
        expects power-oriented forecasts in W for each step. Therefore we scale
        PV/load forecasts by ``*1000``.
        """
        runtimeparams: dict[str, Any] = {
            "pv_power_forecast": [bucket.pv * 1000.0 for bucket in inputs.timeline],
            "load_power_forecast": [bucket.load * 1000.0 for bucket in inputs.timeline],
            "load_cost_forecast": [bucket.price for bucket in inputs.timeline],
            # Future enhancement: replace the fixed 90% export tariff heuristic
            # with a dedicated export price source exposed by the integration.
            "prod_price_forecast": [bucket.price * 0.9 for bucket in inputs.timeline],
            # Explicitly enable battery mode and provide plant parameters so the
            # optimization does not depend on static EMHASS config defaults.
            "set_use_pv": True,
            "set_use_battery": True,
            "battery_discharge_power_max": self._battery_discharge_power_max,
            "battery_charge_power_max": self._battery_charge_power_max,
            "battery_discharge_efficiency": self._battery_efficiency,
            "battery_charge_efficiency": self._battery_efficiency,
            "battery_nominal_energy_capacity": self._battery_nominal_energy_capacity,
            "prediction_horizon": inputs.prediction_horizon,
            "optimization_time_step": inputs.optimization_time_step_minutes,
            "soc_init": inputs.battery_soc,
            # Keep final SOC aligned with initial SOC for this basic policy.
            "soc_final": inputs.battery_soc,
            "battery_target_state_of_charge": inputs.battery_soc,
            "battery_minimum_state_of_charge": self._battery_soc_reserve,
            "battery_maximum_state_of_charge": DEFAULT_BATTERY_MAXIMUM_STATE_OF_CHARGE,
            "number_of_deferrable_loads": 0,
            "continual_publish": False,
        }

        # Optional battery degradation penalty; if zero, EMHASS defaults apply.
        if self._wear_cost_per_kwh > 0:
            runtimeparams["weight_battery_charge"] = self._wear_cost_per_kwh
            runtimeparams["weight_battery_discharge"] = self._wear_cost_per_kwh

        # Future enhancement: map real inverter and battery power limits once the
        # integration exposes charge/discharge capability data.
        return runtimeparams

    def _build_publish_payload(
        self, optimization_time_step_minutes: int
    ) -> dict[str, Any]:
        """Build the publish-data payload with custom entity targets.

        We explicitly pass entity descriptors so EMHASS writes predictable
        entities that this integration can read afterwards.
        """
        return {
            # Both must match the values used during naive-mpc-optim so that
            # publish-data reads opt_res_latest.csv with the correct frequency
            # and does not try to publish deferrable load columns that don't exist.
            "optimization_time_step": optimization_time_step_minutes,
            "number_of_deferrable_loads": 0,
            "custom_pv_forecast_id": self._published_entities["pv_forecast"],
            "custom_load_forecast_id": self._published_entities["load_forecast"],
            "custom_batt_forecast_id": self._published_entities["battery_forecast"],
            "custom_batt_soc_forecast_id": self._published_entities[
                "battery_soc_forecast"
            ],
            "custom_grid_forecast_id": self._published_entities["grid_forecast"],
            "custom_unit_load_cost_id": self._published_entities["unit_load_cost"],
            "custom_unit_prod_price_id": self._published_entities["unit_prod_price"],
            "custom_cost_fun_id": self._published_entities["cost_fun"],
            "custom_optim_status_id": self._published_entities["optim_status"],
        }

    def _read_published_entities(self) -> dict[str, PublishedEntityState]:
        """Read EMHASS-published entities from the local HA state machine.

        This snapshot lets the coordinator expose a structured execution result
        without adding direct dependencies on entity objects.
        """
        snapshots: dict[str, PublishedEntityState] = {}

        # Future enhancement: if EMHASS publishes into a different Home Assistant
        # instance, replace this local state lookup with a remote bridge.
        for key, descriptor in self._published_entities.items():
            entity_id = descriptor["entity_id"]
            state = self._hass.states.get(entity_id)
            snapshots[key] = PublishedEntityState(
                entity_id=entity_id,
                state=None if state is None else state.state,
                attributes={} if state is None else dict(state.attributes),
            )

        return snapshots

    def _log_published_entities(
        self, published_entities: dict[str, PublishedEntityState]
    ) -> None:
        """Log a compact summary of entities published by EMHASS."""
        _LOGGER.debug(
            "EMHASS published entities:\n%s",
            "\n".join(
                f"  {key}: {entity.state} ({entity.entity_id})"
                for key, entity in published_entities.items()
            ),
        )

    async def async_run_naive_optimization(
        self, inputs: OptimizationInputs
    ) -> dict[str, Any] | None:
        """Run only the EMHASS naive optimization step.

        This endpoint updates EMHASS optimization artifacts (for example
        opt_res_latest.csv) but does not publish entities to Home Assistant.
        """
        if not await self._async_check_url(self._url, "EMHASS base"):
            return None

        runtimeparams = self._build_runtimeparams(inputs)
        optimization_response = await self._async_post_action(
            "naive-mpc-optim",
            runtimeparams,
            timeout=60,
        )
        if optimization_response is None:
            return None

        return {
            "runtimeparams": runtimeparams,
            "optimization_response": optimization_response,
        }

    async def async_publish_data(
        self, optimization_time_step_minutes: int
    ) -> dict[str, Any] | None:
        """Run only the EMHASS publish step and read back published entities."""
        if not await self._async_check_url(self._url, "EMHASS base"):
            return None

        publish_response = await self._async_post_action(
            "publish-data",
            self._build_publish_payload(optimization_time_step_minutes),
            timeout=30,
        )
        if publish_response is None:
            return None

        await self._hass.async_block_till_done()
        published_entities = self._read_published_entities()
        self._log_published_entities(published_entities)

        return {
            "publish_response": publish_response,
            "published_entities": published_entities,
        }

    async def async_run_naive_mpc(
        self, inputs: OptimizationInputs
    ) -> EmhassExecutionResult | None:
        """Run one EMHASS naive MPC cycle and publish resulting entities.

        Execution sequence:
        1. Reachability check.
        2. POST ``naive-mpc-optim`` with runtimeparams.
        3. POST ``publish-data`` with entity mapping.
        4. Wait for HA state machine to process updates.
        5. Read and return published entity snapshots.

        Returns ``None`` if any mandatory step fails.
        """
        optimization_result = await self.async_run_naive_optimization(inputs)
        if optimization_result is None:
            return None

        publish_result = await self.async_publish_data(
            inputs.optimization_time_step_minutes
        )
        if publish_result is None:
            return None

        return EmhassExecutionResult(
            runtimeparams=optimization_result["runtimeparams"],
            optimization_response=optimization_result["optimization_response"],
            publish_response=publish_result["publish_response"],
            published_entities=publish_result["published_entities"],
        )
