"""Smart Money Concepts: Order Blocks, Fair Value Gaps, Liquidity Sweeps."""
from __future__ import annotations

from typing import Any

from trading.analysis.helpers import atr, cluster_levels, swing_points


def detect_order_blocks(
    candles: list[dict[str, Any]],
    atr_val: float,
    lookback: int = 40,
) -> list[dict[str, Any]]:
    """Last opposite-colour candle before a strong impulsive move.

    Bullish OB: bearish candle followed by an impulsive up-move > 1.5 ATR.
    Bearish OB: bullish candle followed by an impulsive down-move > 1.5 ATR.
    Only returns unmitigated OBs (price hasn't fully traded through).
    """
    price = candles[-1]["close"]
    start = max(0, len(candles) - lookback - 2)
    obs: list[dict[str, Any]] = []

    for i in range(start, len(candles) - 1):
        c = candles[i]
        body = abs(c["close"] - c["open"])
        if body == 0:
            continue
        move = candles[i + 1]["close"] - c["close"]

        if c["close"] < c["open"] and move > atr_val * 1.5:
            # Bullish OB
            if price > c["low"]:  # not fully mitigated
                obs.append(
                    {
                        "type": "bullish",
                        "top": c["high"],
                        "bottom": c["low"],
                        "mid": (c["high"] + c["low"]) / 2,
                        "time": c["time"],
                        "impulse_size": move,
                    }
                )
        elif c["close"] > c["open"] and move < -atr_val * 1.5:
            # Bearish OB
            if price < c["high"]:  # not fully mitigated
                obs.append(
                    {
                        "type": "bearish",
                        "top": c["high"],
                        "bottom": c["low"],
                        "mid": (c["high"] + c["low"]) / 2,
                        "time": c["time"],
                        "impulse_size": move,
                    }
                )

    # Sort by recency, keep last 4 of each type
    bull_obs = sorted(
        [ob for ob in obs if ob["type"] == "bullish"], key=lambda x: x["time"]
    )[-3:]
    bear_obs = sorted(
        [ob for ob in obs if ob["type"] == "bearish"], key=lambda x: x["time"]
    )[-3:]

    # Tag proximity to current price
    result = []
    for ob in bull_obs + bear_obs:
        dist = abs(price - ob["mid"]) / atr_val if atr_val else 0
        ob["distance_atr"] = round(dist, 2)
        ob["price_inside"] = ob["bottom"] <= price <= ob["top"]
        result.append(ob)

    return result


def detect_fair_value_gaps(
    candles: list[dict[str, Any]],
    lookback: int = 30,
) -> list[dict[str, Any]]:
    """3-candle imbalance: gap between candle[i].high and candle[i+2].low (or reverse).

    Only returns unfilled FVGs (price hasn't traded through the midpoint).
    """
    price = candles[-1]["close"]
    start = max(0, len(candles) - lookback - 2)
    fvgs: list[dict[str, Any]] = []

    for i in range(start, len(candles) - 2):
        c1, c3 = candles[i], candles[i + 2]

        if c1["high"] < c3["low"]:
            # Bullish FVG
            mid = (c1["high"] + c3["low"]) / 2
            if price > c1["high"]:  # unfilled if price above bottom
                fvgs.append(
                    {
                        "type": "bullish",
                        "top": c3["low"],
                        "bottom": c1["high"],
                        "mid": mid,
                        "time": candles[i + 1]["time"],
                        "filled": price < mid,
                    }
                )
        elif c1["low"] > c3["high"]:
            # Bearish FVG
            mid = (c1["low"] + c3["high"]) / 2
            if price < c1["low"]:  # unfilled
                fvgs.append(
                    {
                        "type": "bearish",
                        "top": c1["low"],
                        "bottom": c3["high"],
                        "mid": mid,
                        "time": candles[i + 1]["time"],
                        "filled": price > mid,
                    }
                )

    # Keep most recent 4, unfilled first
    open_fvgs = [f for f in fvgs if not f["filled"]]
    return open_fvgs[-4:]


def detect_liquidity_sweeps(
    candles: list[dict[str, Any]],
    atr_val: float,
    lookback_swing: int = 3,
    window: int = 5,
) -> list[dict[str, Any]]:
    """Detect stop-hunt liquidity sweeps.

    A sweep occurs when a candle wicks beyond a cluster of equal highs/lows
    but closes back inside — the wick is the hunt, the close is the reversal.
    """
    highs, lows = swing_points(candles, lookback=lookback_swing)
    tol = atr_val * 0.4

    eq_highs = [lv for lv in cluster_levels(highs, tol) if lv["touches"] >= 2]
    eq_lows = [lv for lv in cluster_levels(lows, tol) if lv["touches"] >= 2]

    sweeps: list[dict[str, Any]] = []

    for c in candles[-window:]:
        for lv in eq_lows:
            if c["low"] < lv["price"] - tol * 0.2 and c["close"] > lv["price"]:
                sweeps.append(
                    {
                        "type": "bullish",
                        "price": lv["price"],
                        "wick_low": c["low"],
                        "close": c["close"],
                        "time": c["time"],
                        "description": f"Bullish sweep below equal lows {lv['price']:.6g}",
                    }
                )
        for lv in eq_highs:
            if c["high"] > lv["price"] + tol * 0.2 and c["close"] < lv["price"]:
                sweeps.append(
                    {
                        "type": "bearish",
                        "price": lv["price"],
                        "wick_high": c["high"],
                        "close": c["close"],
                        "time": c["time"],
                        "description": f"Bearish sweep above equal highs {lv['price']:.6g}",
                    }
                )

    # Resting liquidity pools (magnets)
    pools = [
        {"type": "sell_side", "price": lv["price"], "touches": lv["touches"]}
        for lv in eq_highs[:4]
    ] + [
        {"type": "buy_side", "price": lv["price"], "touches": lv["touches"]}
        for lv in eq_lows[:4]
    ]

    return sweeps[-4:], pools  # type: ignore[return-value]


def analyze_smc(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Full SMC analysis: OBs, FVGs, and liquidity sweeps."""
    from trading.analysis.market_structure import detect_bos_choch

    atr_val = atr(candles) or candles[-1]["close"] * 0.005
    structure = detect_bos_choch(candles)
    order_blocks = detect_order_blocks(candles, atr_val)
    fvgs = detect_fair_value_gaps(candles)
    sweeps, pools = detect_liquidity_sweeps(candles, atr_val)

    return {
        "structure": structure,
        "order_blocks": order_blocks,
        "fvgs": fvgs,
        "liquidity_sweeps": sweeps,
        "liquidity_pools": pools,
        "has_sweep": bool(sweeps),
        "has_structure_change": structure["has_structure_change"],
    }
