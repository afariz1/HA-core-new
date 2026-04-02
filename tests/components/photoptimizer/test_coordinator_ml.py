"""Tests for Photoptimizer ML load forecasting coordinator behavior."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.components.photoptimizer.const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CURRENT_CONSUMPTION_ENTITY,
    CONF_CURRENT_SOLAR_PRODUCTION_ENTITY,
    DOMAIN,
)
from homeassistant.components.photoptimizer.coordinator import PhotoptimizerCoordinator
from homeassistant.components.photoptimizer.models import (
    OptimizationBucket,
    OptimizationInputs,
    PublishedEntityState,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from tests.common import MockConfigEntry


def _build_coordinator(hass: HomeAssistant) -> PhotoptimizerCoordinator:
    """Create coordinator instance with minimal config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BATTERY_CAPACITY_KWH: 10.0,
            CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
            CONF_CURRENT_CONSUMPTION_ENTITY: "sensor.house_load",
            CONF_CURRENT_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production_now",
        },
    )
    entry.add_to_hass(hass)
    forecast_client = MagicMock()
    return PhotoptimizerCoordinator(hass, entry, forecast_client)


def _build_buckets() -> list[OptimizationBucket]:
    """Create 2 hourly buckets for load filling tests."""
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    return [
        OptimizationBucket(start=start, price=0.0, pv=0.0, load=0.0),
        OptimizationBucket(
            start=start + timedelta(hours=1), price=0.0, pv=0.0, load=0.0
        ),
    ]


@pytest.mark.asyncio
async def test_ml_load_forecast_success(hass: HomeAssistant) -> None:
    """Use EMHASS ML forecast when history and prediction are available."""
    coordinator = _build_coordinator(hass)
    buckets = _build_buckets()

    coordinator.ml_forecast.async_has_sufficient_history = AsyncMock(return_value=True)
    coordinator.ml_forecast.async_train_model = AsyncMock(return_value=True)
    coordinator.emhass.async_forecast_model_predict = AsyncMock(
        return_value=[1200.0, 800.0]
    )

    await coordinator.ml_forecast.async_populate_load_from_ml_or_profile(
        "sensor.house_load",
        buckets,
    )

    assert buckets[0].load == 1.2
    assert buckets[1].load == 0.8
    coordinator.async_build_load_profile.assert_not_called()


@pytest.mark.asyncio
async def test_ml_load_forecast_fallback_on_predict_failure(
    hass: HomeAssistant,
) -> None:
    """Fallback to load profile when ML predict fails."""
    coordinator = _build_coordinator(hass)
    buckets = _build_buckets()

    coordinator.ml_forecast.async_has_sufficient_history = AsyncMock(return_value=True)
    coordinator.ml_forecast.async_train_model = AsyncMock(return_value=True)
    coordinator.async_build_load_profile = AsyncMock(return_value=[0.55] * 24)
    coordinator.emhass.async_forecast_model_predict = AsyncMock(return_value=None)

    await coordinator.ml_forecast.async_populate_load_from_ml_or_profile(
        "sensor.house_load",
        buckets,
    )

    assert buckets[0].load == 0.55
    assert buckets[1].load == 0.55
    coordinator.async_build_load_profile.assert_called_once_with("sensor.house_load")


@pytest.mark.asyncio
async def test_ml_load_forecast_fallback_on_insufficient_history(
    hass: HomeAssistant,
) -> None:
    """Fallback to load profile when history is insufficient for ML."""
    coordinator = _build_coordinator(hass)
    buckets = _build_buckets()

    coordinator.ml_forecast.async_has_sufficient_history = AsyncMock(return_value=False)
    coordinator.async_build_load_profile = AsyncMock(return_value=[0.42] * 24)
    coordinator.emhass.async_forecast_model_predict = AsyncMock(
        return_value=[9999.0, 9999.0]
    )

    await coordinator.ml_forecast.async_populate_load_from_ml_or_profile(
        "sensor.house_load",
        buckets,
    )

    assert buckets[0].load == 0.42
    assert buckets[1].load == 0.42
    coordinator.emhass.async_forecast_model_predict.assert_not_called()


@pytest.mark.asyncio
async def test_mpc_optimization_trains_ml_once_daily(hass: HomeAssistant) -> None:
    """MPC optimization triggers forced ML fit before optimization."""
    coordinator = _build_coordinator(hass)
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    timeline = [OptimizationBucket(start=now, price=1.0, pv=0.0, load=0.5)]

    coordinator.ml_forecast.async_has_sufficient_history = AsyncMock(return_value=True)
    coordinator.ml_forecast.async_train_model = AsyncMock(return_value=True)
    coordinator._async_collect_inputs = AsyncMock(
        return_value=(OptimizationInputs(timeline=timeline, battery_soc=0.5), None)
    )
    coordinator.emhass.async_run_naive_optimization = AsyncMock(
        return_value={
            "runtimeparams": {"k": "v"},
            "optimization_response": {"ok": True},
        }
    )

    await coordinator.async_run_mpc_optimization()

    coordinator.ml_forecast.async_train_model.assert_called_once_with(
        "sensor.house_load",
        force=True,
    )


@pytest.mark.asyncio
async def test_current_solar_bias_correction_adjusts_near_term_buckets(
    hass: HomeAssistant,
) -> None:
    """Adjust first buckets when current production deviates from forecast."""
    coordinator = _build_coordinator(hass)
    buckets = _build_buckets()
    buckets[0].pv = 1.0
    buckets[1].pv = 1.0

    hass.states.async_set(
        "sensor.solar_production_now", "800", {"unit_of_measurement": "W"}
    )

    await coordinator._apply_current_solar_bias_correction(buckets)

    assert buckets[0].pv == 0.8
    assert buckets[1].pv == 0.8


@pytest.mark.asyncio
async def test_current_solar_bias_correction_ignores_low_forecast(
    hass: HomeAssistant,
) -> None:
    """Skip correction when current forecast is too small/noisy."""
    coordinator = _build_coordinator(hass)
    buckets = _build_buckets()
    buckets[0].pv = 0.01
    buckets[1].pv = 0.01

    hass.states.async_set(
        "sensor.solar_production_now", "500", {"unit_of_measurement": "W"}
    )

    await coordinator._apply_current_solar_bias_correction(buckets)

    assert buckets[0].pv == 0.01
    assert buckets[1].pv == 0.01


@pytest.mark.asyncio
async def test_extract_would_apply_supports_battery_scheduled_power_list(
    hass: HomeAssistant,
) -> None:
    """Parse list payload shape used by newer EMHASS battery publish output."""
    coordinator = _build_coordinator(hass)
    next_slot = dt_util.utcnow().replace(second=0, microsecond=0) + timedelta(minutes=5)
    published = {
        "battery_forecast": PublishedEntityState(
            entity_id="sensor.p_batt_forecast",
            state=None,
            attributes={
                "battery_scheduled_power": [
                    {
                        "date": next_slot.isoformat(),
                        "p_batt_forecast": "321.5",
                    }
                ]
            },
        )
    }

    result = coordinator._extract_would_apply(published)

    assert result["battery_power_w"] == 321.5
    assert result["source"] == "forecast_attribute"
