"""Market structure analysis: Swing High/Low, BOS, CHoCH, trend detection."""
from __future__ import annotations

from typing import Any

from trading.analysis.helpers import swing_points


def _detect_trend(
    highs: list[tuple[int, float]],
    lows: list[tuple[int, float]],
) -> int:
    """Return 1 (bullish), -1 (bearish), 0 (neutral) from last two swings."""
    if len(highs) < 2 or len(lows) < 2:
        return 0
    hh = highs[-1][1] > highs[-2][1]
    hl = lows[-1][1] > lows[-2][1]
    lh = highs[-1][1] < highs[-2][1]
    ll = lows[-1][1] < lows[-2][1]
    if hh and hl:
        return 1
    if lh and ll:
        return -1
    return 0


def detect_bos_choch(
    candles: list[dict[str, Any]],
    lookback: int = 3,
) -> dict[str, Any]:
    """Detect Break of Structure (BOS) and Change of Character (CHoCH).

    BOS: price closes beyond the most recent swing in the direction of the trend.
    CHoCH: price closes beyond the most recent swing AGAINST the prior trend.

    Returns a structured dict with trend, events, last_bos, last_choch.
    """
    highs, lows = swing_points(candles, lookback=lookback)
    price = candles[-1]["close"]
    trend = _detect_trend(highs, lows)

    events: list[dict[str, Any]] = []

    if highs and price > highs[-1][1]:
        kind = "CHoCH" if trend == -1 else "BOS"
        events.append(
            {
                "type": kind,
                "direction": 1,
                "level": highs[-1][1],
                "time": candles[highs[-1][0]]["time"],
                "description": f"{kind} above swing high {highs[-1][1]:.6g}",
            }
        )

    if lows and price < lows[-1][1]:
        kind = "CHoCH" if trend == 1 else "BOS"
        events.append(
            {
                "type": kind,
                "direction": -1,
                "level": lows[-1][1],
                "time": candles[lows[-1][0]]["time"],
                "description": f"{kind} below swing low {lows[-1][1]:.6g}",
            }
        )

    trend_label = "bullish" if trend == 1 else "bearish" if trend == -1 else "neutral"

    last_bos = next(
        (e for e in reversed(events) if e["type"] == "BOS"), None
    )
    last_choch = next(
        (e for e in reversed(events) if e["type"] == "CHoCH"), None
    )

    # Recent swing levels for context
    recent_highs = [
        {"index": i, "price": p, "time": candles[i]["time"]}
        for i, p in highs[-5:]
    ]
    recent_lows = [
        {"index": i, "price": p, "time": candles[i]["time"]}
        for i, p in lows[-5:]
    ]

    return {
        "trend": trend_label,
        "trend_int": trend,
        "events": events,
        "last_bos": last_bos,
        "last_choch": last_choch,
        "swing_highs": recent_highs,
        "swing_lows": recent_lows,
        "has_structure_change": bool(events),
    }
