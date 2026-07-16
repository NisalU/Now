"""Regression-fit trendline detection."""
from __future__ import annotations

from typing import Any

from trading.analysis.helpers import atr, linear_regression, swing_points


def fit_trendlines(
    candles: list[dict[str, Any]],
    lookback: int = 60,
) -> dict[str, Any]:
    """Fit ascending and descending trendlines from recent swing points.

    Returns trendline parameters and whether price is above/below each.
    """
    recent = candles[-lookback:] if len(candles) > lookback else candles
    highs, lows = swing_points(recent, lookback=3)
    atr_val = atr(candles) or candles[-1]["close"] * 0.005
    price = candles[-1]["close"]
    n = len(recent)

    up_trendline: dict[str, Any] | None = None
    down_trendline: dict[str, Any] | None = None

    if len(lows) >= 2:
        xs = [float(idx) for idx, _ in lows[-6:]]
        ys = [p for _, p in lows[-6:]]
        slope, intercept = linear_regression(xs, ys)
        # Current value of the trendline
        current = slope * (n - 1) + intercept
        if slope > 0:
            up_trendline = {
                "slope": slope,
                "intercept": intercept,
                "current_value": current,
                "type": "ascending_support",
                "price_above": price > current,
                "distance_atr": round(abs(price - current) / atr_val, 2) if atr_val else 0,
                "touch_count": len(lows),
            }

    if len(highs) >= 2:
        xs = [float(idx) for idx, _ in highs[-6:]]
        ys = [p for _, p in highs[-6:]]
        slope, intercept = linear_regression(xs, ys)
        current = slope * (n - 1) + intercept
        if slope < 0:
            down_trendline = {
                "slope": slope,
                "intercept": intercept,
                "current_value": current,
                "type": "descending_resistance",
                "price_below": price < current,
                "distance_atr": round(abs(price - current) / atr_val, 2) if atr_val else 0,
                "touch_count": len(highs),
            }

    return {
        "up_trendline": up_trendline,
        "down_trendline": down_trendline,
        "channel": up_trendline is not None and down_trendline is not None,
    }
