"""Context builder: converts multi-timeframe analysis into a compact, token-efficient
JSON payload suitable for the Groq AI trader prompt.

Target: < 2500 tokens of JSON text per symbol call.
Strategy: round numbers aggressively, omit null/empty fields, cap array lengths.
"""
from __future__ import annotations

import time
from typing import Any


def _f(v: float | None, decimals: int = 2) -> float | None:
    """Round a float; return None if None."""
    if v is None:
        return None
    return round(v, decimals)


def _tf_summary(tf: str, data: dict[str, Any]) -> dict[str, Any]:
    """Compact summary for one timeframe."""
    ema = data.get("ema", {})
    macd = data.get("macd", {})
    structure = data.get("structure", {})
    sr = data.get("support_resistance", {})
    fib = data.get("fibonacci")
    vol = data.get("volume", {})
    obs = data.get("order_blocks", [])
    fvgs = data.get("fvgs", [])
    sweeps = data.get("liquidity_sweeps", [])

    result: dict[str, Any] = {
        "tf": tf,
        "trend": structure.get("trend", "neutral"),
        "ema_align": ema.get("alignment"),
        "ema20": _f(ema.get("ema20")),
        "ema50": _f(ema.get("ema50")),
        "ema200": _f(ema.get("ema200")),
        "vwap": _f(data.get("vwap")),
        "atr": _f(data.get("atr")),
        "rsi": _f(data.get("rsi"), 1),
        "macd_hist": _f(macd.get("histogram")),
        "vol_ratio": _f(vol.get("ratio"), 1),
        "vol_spike": vol.get("spike", False),
        "buy_pct": _f(vol.get("buy_pct"), 1),
        "cvd_div": vol.get("cvd_divergence"),
        "poc": _f(vol.get("poc")),
    }

    # Structure events
    if structure.get("last_bos"):
        result["last_bos"] = structure["last_bos"]["type"] + " " + str(_f(structure["last_bos"]["level"]))
    if structure.get("last_choch"):
        result["last_choch"] = "CHoCH " + str(_f(structure["last_choch"]["level"]))

    # S/R
    sup = [_f(lv["price"]) for lv in sr.get("support", [])[:3]]
    res = [_f(lv["price"]) for lv in sr.get("resistance", [])[:3]]
    if sup:
        result["support"] = sup
    if res:
        result["resistance"] = res

    # Order blocks (most recent 2 of each)
    bull_obs = [o for o in obs if o["type"] == "bullish"][-2:]
    bear_obs = [o for o in obs if o["type"] == "bearish"][-2:]
    if bull_obs or bear_obs:
        result["obs"] = [
            {"type": o["type"], "top": _f(o["top"]), "bot": _f(o["bottom"]), "dist": o.get("distance_atr")}
            for o in (bull_obs + bear_obs)
        ]

    # FVGs (last 2)
    if fvgs:
        result["fvgs"] = [
            {"type": f["type"], "top": _f(f["top"]), "bot": _f(f["bottom"]), "mid": _f(f["mid"])}
            for f in fvgs[-2:]
        ]

    # Liquidity sweeps (last 2)
    if sweeps:
        result["sweeps"] = [
            {"type": s["type"], "price": _f(s["price"])}
            for s in sweeps[-2:]
        ]

    # Fibonacci golden zone
    if fib and fib.get("near_golden_zone"):
        result["fib_zone"] = {
            "dir": fib["direction"],
            "hi": _f(fib["swing_high"]),
            "lo": _f(fib["swing_low"]),
        }

    # Trendlines
    tl = data.get("trendlines", {})
    if tl.get("up_trendline"):
        result["up_tl"] = _f(tl["up_trendline"]["current_value"])
    if tl.get("down_trendline"):
        result["down_tl"] = _f(tl["down_trendline"]["current_value"])

    # Strip None values to save tokens
    return {k: v for k, v in result.items() if v is not None}


def build_context(
    symbol: str,
    current_price: float,
    mtf_analysis: dict[str, dict[str, Any]],
    fundamentals: dict[str, Any] | None = None,
    market_regime: dict[str, Any] | None = None,
    trigger: str | None = None,
) -> dict[str, Any]:
    """Build the compact AI context payload.

    The AI priority order from the spec:
        1D → Market cycle
        4H → Primary trend
        2H → Momentum
        1H → Bias
        30m → Setup
        15m → Entry
        5m → Confirmation
        1m → Precision timing
    """
    TF_ORDER = ["1D", "4H", "2H", "1H", "30m", "15m", "5m", "1m"]

    timeframes: dict[str, Any] = {}
    for tf in TF_ORDER:
        if tf in mtf_analysis:
            timeframes[tf] = _tf_summary(tf, mtf_analysis[tf])

    ctx: dict[str, Any] = {
        "symbol": symbol,
        "price": _f(current_price),
        "ts": int(time.time()),
        "timeframes": timeframes,
    }

    if trigger:
        ctx["trigger"] = trigger

    if market_regime:
        ctx["regime"] = {
            "type": market_regime.get("regime"),
            "tradeable": market_regime.get("tradeable"),
        }

    if fundamentals:
        ctx["fundamentals"] = {
            "funding_pct": _f(fundamentals.get("funding_pct"), 4),
            "funding_bias": fundamentals.get("funding_bias"),
            "ls_ratio": _f(fundamentals.get("long_short_ratio"), 2),
            "ls_bias": fundamentals.get("ls_bias"),
            "contrarian_risk": fundamentals.get("contrarian_risk"),
        }

    return ctx
