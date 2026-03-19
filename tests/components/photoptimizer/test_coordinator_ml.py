"""Tests for Photoptimizer ML load forecasting coordinator behavior."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.components.photoptimizer.const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CURRENT_CONSUMPTION_ENTITY,
    DOMAIN,
)
from homeassistant.components.photoptimizer.coordinator import PhotoptimizerCoordinator
from homeassistant.components.photoptimizer.models import OptimizationBucket, OptimizationInputs
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
        OptimizationBucket(start=start + timedelta(hours=1), price=0.0, pv=0.0, load=0.0),
    ]


@pytest.mark.asyncio
async def test_ml_load_forecast_success(hass: HomeAssistant) -> None:
    """Use EMHASS ML forecast when history and prediction are available."""
    coordinator = _build_coordinator(hass)
    buckets = _build_buckets()

    coordinator._async_has_sufficient_history = AsyncMock(return_value=True)
    coordinator._async_train_load_forecast_model = AsyncMock(return_value=True)
    coordinator._build_load_profile = AsyncMock(return_value=[0.3] * 24)
    coordinator.emhass.async_forecast_model_predict = AsyncMock(
        return_value=[1200.0, 800.0]
    )

    await coordinator._hourly_from_emhass_ml_or_profile("sensor.house_load", buckets)

    assert buckets[0].load == 1.2
    assert buckets[1].load == 0.8
    coordinator._build_load_profile.assert_not_called()


@pytest.mark.asyncio
async def test_ml_load_forecast_fallback_on_predict_failure(hass: HomeAssistant) -> None:
    """Fallback to load profile when ML predict fails."""
    coordinator = _build_coordinator(hass)
    buckets = _build_buckets()

    coordinator._async_has_sufficient_history = AsyncMock(return_value=True)
    coordinator._async_train_load_forecast_model = AsyncMock(return_value=True)
    coordinator._build_load_profile = AsyncMock(return_value=[0.55] * 24)
    coordinator.emhass.async_forecast_model_predict = AsyncMock(return_value=None)

    await coordinator._hourly_from_emhass_ml_or_profile("sensor.house_load", buckets)

    assert buckets[0].load == 0.55
    assert buckets[1].load == 0.55
    coordinator._build_load_profile.assert_called_once_with("sensor.house_load")


@pytest.mark.asyncio
async def test_ml_load_forecast_fallback_on_insufficient_history(
    hass: HomeAssistant,
) -> None:
    """Fallback to load profile when history is insufficient for ML."""
    coordinator = _build_coordinator(hass)
    buckets = _build_buckets()

    coordinator._async_has_sufficient_history = AsyncMock(return_value=False)
    coordinator._build_load_profile = AsyncMock(return_value=[0.42] * 24)
    coordinator.emhass.async_forecast_model_predict = AsyncMock(return_value=[9999.0, 9999.0])

    await coordinator._hourly_from_emhass_ml_or_profile("sensor.house_load", buckets)

    assert buckets[0].load == 0.42
    assert buckets[1].load == 0.42
    coordinator.emhass.async_forecast_model_predict.assert_not_called()


@pytest.mark.asyncio
async def test_daily_optimization_trains_ml_once_daily(hass: HomeAssistant) -> None:
    """Daily optimization triggers forced ML fit before optimization."""
    coordinator = _build_coordinator(hass)
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    timeline = [OptimizationBucket(start=now, price=1.0, pv=0.0, load=0.5)]

    coordinator._async_has_sufficient_history = AsyncMock(return_value=True)
    coordinator._async_train_load_forecast_model = AsyncMock(return_value=True)
    coordinator._async_collect_inputs = AsyncMock(
        return_value=(OptimizationInputs(timeline=timeline, battery_soc=0.5), None)
    )
    coordinator.emhass.async_run_naive_optimization = AsyncMock(
        return_value={"runtimeparams": {"k": "v"}, "optimization_response": {"ok": True}}
    )

    await coordinator.async_run_daily_optimization()

    coordinator._async_train_load_forecast_model.assert_called_once_with(
        "sensor.house_load",
        force=True,
    )
