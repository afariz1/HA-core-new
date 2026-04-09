"""Tests for Photoptimizer EMHASS client."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.photoptimizer.emhass_client import EmhassClient
from homeassistant.components.photoptimizer.models import (
    DeferrableLoadDefinition,
    OptimizationBucket,
    OptimizationInputs,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util


def _build_client(hass: HomeAssistant) -> EmhassClient:
    """Build an EMHASS client with one deferrable load."""
    return EmhassClient(
        hass,
        "http://localhost:5000",
        battery_capacity_kwh=10.0,
        battery_efficiency=0.95,
        battery_soc_reserve=0.2,
        battery_target_soc=0.6,
        battery_charge_power_max_w=1500.0,
        battery_discharge_power_max_w=2200.0,
        wear_cost_per_kwh=0.0,
        deferrable_loads=[
            DeferrableLoadDefinition(
                name="Dishwasher",
                entity_id="switch.dishwasher",
                nominal_power_w=1200.0,
                operating_minutes=90,
            )
        ],
    )


def _build_inputs() -> OptimizationInputs:
    """Build minimal optimization inputs for the client."""
    start = dt_util.utcnow().replace(second=0, microsecond=0)
    return OptimizationInputs(
        timeline=[
            OptimizationBucket(start=start, price=0.2, pv=0.0, load=0.0),
            OptimizationBucket(
                start=start + timedelta(minutes=15), price=0.2, pv=0.0, load=0.0
            ),
        ],
        battery_soc=0.5,
        deferrable_loads=[],
    )


async def test_build_runtimeparams_includes_deferrable_loads(
    hass: HomeAssistant,
) -> None:
    """Runtimeparams should include configured deferrable loads."""
    hass.states.async_set("switch.dishwasher", "on")
    client = _build_client(hass)

    runtimeparams = client._build_runtimeparams(_build_inputs())

    assert runtimeparams["number_of_deferrable_loads"] == 1
    assert runtimeparams["nominal_power_of_deferrable_loads"] == [1200.0]
    assert runtimeparams["operating_timesteps_of_each_deferrable_load"] == [6]
    assert runtimeparams["def_current_state"] == [True]


async def test_build_publish_payload_includes_deferrable_loads(
    hass: HomeAssistant,
) -> None:
    """Publish payload should include one load config entry per deferrable load."""
    client = _build_client(hass)

    payload = client._build_publish_payload(15)

    assert payload["number_of_deferrable_loads"] == 1
    assert payload["def_load_config"] == [{}]
