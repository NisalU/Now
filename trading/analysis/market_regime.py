"""Market regime classifier.

Pure deterministic classification based on price action, volatility,
and structure. No AI involved. Used to provide regime context to the AI
and to gate AI calls on high-volatility / choppy conditions.
"""
from __future__ import annotations

from typing import Any

from trading import config
from trading.analysis.helpers import atr


def classify_regime(
    candles: list[dict[str, Any]],
    structure_trend: int = 0,
    fundamentals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify the current market regime.

    Returns:
        regime: trending_bullish | trending_bearish | range |
                accumulation | distribution | high_volatility | mixed
        tradeable: bool — whether conditions are suitable for AI analysis
        compression: float — range/ATR ratio (tight = <0.45)
        volatility_expansion: float — recent vs baseline true range
        reasons: list[str]
    """
    price = candles[-1]["close"]
    atr_val = atr(candles) or price * 0.005

    # Range compression: spread of last 20 closes vs ATR
    closes_20 = [c["close"] for c in candles[-20:]]
    range_20 = max(closes_20) - min(closes_20) if closes_20 else 0.0
    compression = range_20 / (atr_val * 20) if atr_val else 1.0

    # Volatility expansion: recent 14 vs baseline 60
    recent_n = min(14, len(candles))
    base_n = min(60, len(candles))
    recent_ranges = [c["high"] - c["low"] for c in candles[-recent_n:]]
    base_ranges = [c["high"] - c["low"] for c in candles[-base_n:]]
    vol_recent = sum(recent_ranges) / recent_n if recent_n else 0.0
    vol_base = sum(base_ranges) / base_n if base_n else vol_recent
    expansion = vol_recent / vol_base if vol_base else 1.0

    reasons: list[str] = []
    tradeable = True

    if expansion > config.REGIME_VOLATILITY_SPIKE:
        regime = "high_volatility"
        reasons.append(
            f"True range expanded {expansion:.1f}× vs 60-candle baseline — extreme volatility"
        )
        tradeable = False

    elif structure_trend == 1 and compression > 0.5:
        regime = "trending_bullish"
        reasons.append("Higher highs + higher lows, price expanding above EMA stack")

    elif structure_trend == -1 and compression > 0.5:
        regime = "trending_bearish"
        reasons.append("Lower highs + lower lows, price collapsing below EMA stack")

    elif compression < config.REGIME_COMPRESSION_TIGHT:
        # Check OI for accumulation vs distribution signals
        if fundamentals and fundamentals.get("open_interest", 0) > 0:
            oi_bias = fundamentals.get("ls_bias", "balanced")
            if "long" in oi_bias:
                regime = "distribution"
                reasons.append("Range-bound with longs accumulating — potential distribution")
            elif "short" in oi_bias:
                regime = "accumulation"
                reasons.append("Range-bound with shorts building — potential accumulation")
            else:
                regime = "range"
                reasons.append(f"Price compressed to {compression:.2f}× ATR over 20 candles")
        else:
            regime = "range"
            reasons.append(f"Tight range: {compression:.2f}× ATR compression over 20 candles")
        tradeable = False

    else:
        regime = "mixed"
        reasons.append("No clean trend or range — structure and volatility disagree")
        tradeable = False

    return {
        "regime": regime,
        "tradeable": tradeable,
        "compression": round(compression, 3),
        "volatility_expansion": round(expansion, 3),
        "reasons": reasons,
    }
