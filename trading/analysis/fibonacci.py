"""Fibonacci retracement and extension levels."""
from __future__ import annotations

from typing import Any

from trading.analysis.helpers import atr, swing_points

RETRACEMENT_RATIOS = [0.236, 0.382, 0.5, 0.618, 0.786]
EXTENSION_RATIOS = [1.0, 1.272, 1.414, 1.618, 2.0]


def compute_fibonacci(candles: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Compute Fibonacci retracements of the dominant recent swing.

    Returns retracement levels, extensions, and which level price is near.
    """
    highs, lows = swing_points(candles, lookback=3)
    if not highs or not lows:
        return None

    # Find dominant swing (largest range in recent history)
    hi_i, hi_p = max(highs[-8:], key=lambda x: x[1])
    lo_i, lo_p = min(lows[-8:], key=lambda x: x[1])
    price = candles[-1]["close"]
    rng = hi_p - lo_p
    if rng <= 0:
        return None

    up_leg = lo_i < hi_i  # swing low first → impulse up

    atr_val = atr(candles) or price * 0.005

    def _level(r: float) -> float:
        return (hi_p - rng * r) if up_leg else (lo_p + rng * r)

    retracement_levels = [
        {
            "ratio": r,
            "price": _level(r),
            "is_golden_zone": r in (0.5, 0.618),
            "near_price": abs(price - _level(r)) < atr_val * 0.8,
        }
        for r in RETRACEMENT_RATIOS
    ]

    extension_levels = [
        {
            "ratio": r,
            "price": (hi_p + rng * (r - 1)) if up_leg else (lo_p - rng * (r - 1)),
        }
        for r in EXTENSION_RATIOS
    ]

    # Which level is price closest to?
    nearest = min(retracement_levels, key=lambda l: abs(price - l["price"]))
    near_golden_zone = any(
        l["near_price"] and l["is_golden_zone"] for l in retracement_levels
    )

    return {
        "direction": "up" if up_leg else "down",
        "swing_high": hi_p,
        "swing_low": lo_p,
        "range": rng,
        "retracement_levels": retracement_levels,
        "extension_levels": extension_levels,
        "nearest_level": nearest,
        "near_golden_zone": near_golden_zone,
        "price_in_retracement": lo_p <= price <= hi_p,
    }
