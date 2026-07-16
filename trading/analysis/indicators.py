"""Technical indicators: EMA 20/50/200, VWAP, ATR, RSI, MACD.

Returns structured dicts suitable for the context builder.
No confluence scoring — just clean data.
"""
from __future__ import annotations

from typing import Any

from trading.analysis.helpers import (
    atr as _atr,
    ema,
    macd as _macd,
    rsi as _rsi,
    vwap as _vwap,
)


def compute_ema_levels(
    candles: list[dict[str, Any]],
) -> dict[str, float | None]:
    """EMA 20, 50, 200 — last values only."""
    closes = [c["close"] for c in candles]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)
    return {
        "ema20": e20[-1],
        "ema50": e50[-1],
        "ema200": e200[-1],
    }


def compute_ema_series(
    candles: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """EMA 20/50/200 as time-series for charting (last 100 points)."""
    closes = [c["close"] for c in candles]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)

    def _series(values: list[float | None]) -> list[dict[str, Any]]:
        return [
            {"time": candles[i]["time"], "value": v}
            for i, v in enumerate(values)
            if v is not None
        ][-100:]

    return {
        "ema20": _series(e20),
        "ema50": _series(e50),
        "ema200": _series(e200),
    }


def compute_ema_trend(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """EMA stack analysis: alignment, price position, fresh crosses."""
    closes = [c["close"] for c in candles]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)

    v20, v50, v200 = e20[-1], e50[-1], e200[-1]
    price = closes[-1]

    alignment = "neutral"
    if v20 and v50 and v200:
        if v20 > v50 > v200:
            alignment = "bullish"
        elif v20 < v50 < v200:
            alignment = "bearish"
        elif v20 > v50:
            alignment = "weakly_bullish"
        else:
            alignment = "weakly_bearish"

    price_vs_ema200 = "above" if (v200 and price > v200) else "below"

    # Detect fresh 20/50 cross in last 5 candles
    fresh_cross: str | None = None
    for i in range(-5, 0):
        a20, a50 = e20[i - 1], e50[i - 1]
        b20, b50 = e20[i], e50[i]
        if a20 and a50 and b20 and b50:
            if a20 <= a50 and b20 > b50:
                fresh_cross = "bullish_cross_20_50"
            elif a20 >= a50 and b20 < b50:
                fresh_cross = "bearish_cross_20_50"

    return {
        "ema20": v20,
        "ema50": v50,
        "ema200": v200,
        "alignment": alignment,
        "price_vs_ema200": price_vs_ema200,
        "fresh_cross": fresh_cross,
    }


def compute_vwap(candles: list[dict[str, Any]]) -> float | None:
    """Session VWAP from the full candle array."""
    return _vwap(candles)


def compute_atr(candles: list[dict[str, Any]], period: int = 14) -> float:
    """ATR(14) — current value."""
    return _atr(candles, period)


def compute_rsi(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    """RSI(14) — current value. None if insufficient data."""
    closes = [c["close"] for c in candles]
    return _rsi(closes, period)


def compute_macd(candles: list[dict[str, Any]]) -> dict[str, float | None]:
    """MACD(12,26,9) — last values: macd, signal, histogram."""
    closes = [c["close"] for c in candles]
    return _macd(closes)


def compute_all(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute all indicators and return a single structured dict."""
    price = candles[-1]["close"] if candles else 0.0
    ema_data = compute_ema_trend(candles)
    atr_val = compute_atr(candles)
    rsi_val = compute_rsi(candles)
    macd_data = compute_macd(candles)
    vwap_val = compute_vwap(candles)

    return {
        "price": price,
        "ema": ema_data,
        "vwap": vwap_val,
        "atr": atr_val,
        "rsi": rsi_val,
        "macd": macd_data,
    }
