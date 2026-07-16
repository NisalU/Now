"""Support and Resistance level detection from clustered swing points."""
from __future__ import annotations

from typing import Any

from trading.analysis.helpers import atr, cluster_levels, swing_points


def detect_sr_levels(
    candles: list[dict[str, Any]],
    min_touches: int = 2,
    max_levels: int = 5,
) -> dict[str, Any]:
    """Identify support and resistance zones from clustered swing highs/lows.

    Returns:
        support: list of {price, touches, distance_atr, is_near}
        resistance: list of {price, touches, distance_atr, is_near}
        nearest_support: float | None
        nearest_resistance: float | None
    """
    highs, lows = swing_points(candles, lookback=3)
    atr_val = atr(candles) or candles[-1]["close"] * 0.005
    tol = atr_val * 0.8
    price = candles[-1]["close"]

    resistance_clusters = [
        lv for lv in cluster_levels(highs, tol) if lv["touches"] >= min_touches
    ]
    support_clusters = [
        lv for lv in cluster_levels(lows, tol) if lv["touches"] >= min_touches
    ]

    def _enrich(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for lv in clusters:
            dist = abs(price - lv["price"]) / atr_val if atr_val else 0
            out.append(
                {
                    "price": lv["price"],
                    "touches": lv["touches"],
                    "distance_atr": round(dist, 2),
                    "is_near": dist < 2.0,
                }
            )
        return sorted(out, key=lambda x: x["distance_atr"])[:max_levels]

    sup = _enrich(support_clusters)
    res = _enrich(resistance_clusters)

    nearest_sup = next((lv["price"] for lv in sup if lv["price"] < price), None)
    nearest_res = next((lv["price"] for lv in res if lv["price"] > price), None)

    return {
        "support": sup,
        "resistance": res,
        "nearest_support": nearest_sup,
        "nearest_resistance": nearest_res,
        "atr": atr_val,
    }
