"""Tests for price-driven optimization horizon in Photoptimizer coordinator."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.photoptimizer.const import (
    CONF_ELECTRICITY_PRICE_ENTITY,
    CONF_TIMEZONE,
    DOMAIN,
)
from homeassistant.components.photoptimizer.coordinator import PhotoptimizerCoordinator
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from tests.common import MockConfigEntry


def _build_coordinator(
    hass: HomeAssistant,
) -> tuple[PhotoptimizerCoordinator, MagicMock]:
    """Create coordinator instance with price entity configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ELECTRICITY_PRICE_ENTITY: "sensor.current_spot_electricity_price",
            CONF_TIMEZONE: "UTC",
        },
    )
    entry.add_to_hass(hass)

    forecast_client = MagicMock()
    forecast_client.estimate = AsyncMock(
        return_value=SimpleNamespace(
            wh_period={
                datetime(2026, 4, 18, 10, tzinfo=dt_util.UTC): 1000.0,
                datetime(2026, 4, 18, 11, tzinfo=dt_util.UTC): 900.0,
                datetime(2026, 4, 18, 12, tzinfo=dt_util.UTC): 800.0,
            }
        )
    )

    return PhotoptimizerCoordinator(hass, entry, forecast_client), forecast_client


@pytest.mark.asyncio
async def test_collect_inputs_limits_horizon_to_available_price_hours(
    hass: HomeAssistant,
) -> None:
    """Use only contiguous future hours available in spot-price attributes."""
    coordinator, forecast_client = _build_coordinator(hass)
    fixed_now = datetime(2026, 4, 18, 10, 7, tzinfo=dt_util.UTC)

    hass.states.async_set(
        "sensor.current_spot_electricity_price",
        "0",
        {
            datetime(2026, 4, 18, 10, tzinfo=dt_util.UTC).isoformat(): 100.0,
            datetime(2026, 4, 18, 11, tzinfo=dt_util.UTC).isoformat(): 90.0,
            datetime(2026, 4, 18, 12, tzinfo=dt_util.UTC).isoformat(): 80.0,
            datetime(2026, 4, 18, 9, tzinfo=dt_util.UTC).isoformat(): 130.0,
        },
    )

    with patch("homeassistant.components.photoptimizer.coordinator.dt_util.now") as now:
        now.return_value = fixed_now
        optimization_inputs, _ = await coordinator._async_collect_inputs()

    assert optimization_inputs.prediction_horizon == 12
    assert len(optimization_inputs.timeline) == 12
    assert optimization_inputs.timeline[0].start == datetime(
        2026, 4, 18, 10, 0, tzinfo=dt_util.UTC
    )
    assert optimization_inputs.timeline[-1].start == datetime(
        2026, 4, 18, 12, 45, tzinfo=dt_util.UTC
    )
    assert all(bucket.price == 100.0 for bucket in optimization_inputs.timeline[:4])
    assert all(bucket.price == 90.0 for bucket in optimization_inputs.timeline[4:8])
    assert all(bucket.price == 80.0 for bucket in optimization_inputs.timeline[8:12])
    forecast_client.estimate.assert_awaited_once()


@pytest.mark.asyncio
async def test_collect_inputs_fails_when_no_future_price_data(
    hass: HomeAssistant,
) -> None:
    """Fail safely and skip optimization when no future spot-price data exists."""
    coordinator, forecast_client = _build_coordinator(hass)
    fixed_now = datetime(2026, 4, 18, 10, 7, tzinfo=dt_util.UTC)

    hass.states.async_set(
        "sensor.current_spot_electricity_price",
        "0",
        {
            (fixed_now - timedelta(hours=2)).replace(minute=0).isoformat(): 120.0,
            (fixed_now - timedelta(hours=1)).replace(minute=0).isoformat(): 110.0,
        },
    )

    with patch("homeassistant.components.photoptimizer.coordinator.dt_util.now") as now:
        now.return_value = fixed_now
        with pytest.raises(UpdateFailed, match="no future spot-price data"):
            await coordinator._async_collect_inputs()

    forecast_client.estimate.assert_not_called()
