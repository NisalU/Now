"""Pure-Python math helpers for the analysis engine.

No numpy dependency — keeps the codebase portable to Termux and minimal envs.
All functions are synchronous and stateless.
"""
from __future__ import annotations

import math
from typing import Any


# ── Moving averages ───────────────────────────────────────────────────────────

def ema(values: list[float], period: int) -> list[float | None]:
    """Exponential moving average. Returns list with None until period filled."""
    if period <= 0:
        return [None] * len(values)
    k = 2.0 / (period + 1)
    result: list[float | None] = [None] * len(values)
    for i, v in enumerate(values):
        if i < period - 1:
            continue
        if result[i - 1] is None and i == period - 1:
            result[i] = sum(values[:period]) / period  # SMA seed
        elif result[i - 1] is not None:
            prev = result[i - 1]
            assert prev is not None
            result[i] = v * k + prev * (1 - k)
    return result


def sma(values: list[float], period: int) -> list[float | None]:
    """Simple moving average."""
    result: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = sum(values[i - period + 1 : i + 1]) / period
    return result


def rma(values: list[float], period: int) -> list[float | None]:
    """Wilder's smoothed moving average (used in RSI)."""
    if period <= 0:
        return [None] * len(values)
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    # Seed with SMA
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    for i in range(period, len(values)):
        prev = result[i - 1]
        assert prev is not None
        result[i] = (prev * (period - 1) + values[i]) / period
    return result


# ── Volatility ────────────────────────────────────────────────────────────────

def true_range(candles: list[dict[str, Any]]) -> list[float]:
    """True range series."""
    tr: list[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            tr.append(c["high"] - c["low"])
        else:
            prev_close = candles[i - 1]["close"]
            tr.append(
                max(
                    c["high"] - c["low"],
                    abs(c["high"] - prev_close),
                    abs(c["low"] - prev_close),
                )
            )
    return tr


def atr(candles: list[dict[str, Any]], period: int = 14) -> float:
    """Average True Range — last value only."""
    if len(candles) < period:
        if candles:
            return candles[-1]["high"] - candles[-1]["low"]
        return 0.0
    tr = true_range(candles)
    smoothed = rma(tr, period)
    for v in reversed(smoothed):
        if v is not None:
            return v
    return 0.0


# ── Momentum ──────────────────────────────────────────────────────────────────

def rsi(closes: list[float], period: int = 14) -> float | None:
    """RSI — last value only. Returns None if insufficient data."""
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain_series = rma(gains, period)
    avg_loss_series = rma(losses, period)

    ag = next((v for v in reversed(avg_gain_series) if v is not None), None)
    al = next((v for v in reversed(avg_loss_series) if v is not None), None)

    if ag is None or al is None:
        return None
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> dict[str, float | None]:
    """MACD line, signal line, and histogram — last values only."""
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)

    macd_line: list[float | None] = [
        (f - s) if f is not None and s is not None else None
        for f, s in zip(fast_ema, slow_ema)
    ]
    valid_macd = [v for v in macd_line if v is not None]
    if not valid_macd:
        return {"macd": None, "signal": None, "histogram": None}

    signal_series = ema(valid_macd, signal_period)
    sig_val = next((v for v in reversed(signal_series) if v is not None), None)
    macd_val = macd_line[-1]
    hist = (macd_val - sig_val) if macd_val is not None and sig_val is not None else None

    return {"macd": macd_val, "signal": sig_val, "histogram": hist}


# ── Volume-weighted average price ─────────────────────────────────────────────

def vwap(candles: list[dict[str, Any]]) -> float | None:
    """Session VWAP from the provided candles."""
    cum_tp_vol = 0.0
    cum_vol = 0.0
    for c in candles:
        typical = (c["high"] + c["low"] + c["close"]) / 3
        cum_tp_vol += typical * c["volume"]
        cum_vol += c["volume"]
    if cum_vol == 0:
        return None
    return cum_tp_vol / cum_vol


# ── Swing structure ───────────────────────────────────────────────────────────

def swing_points(
    candles: list[dict[str, Any]], lookback: int = 3
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Return (highs, lows) as (index, price) tuples.

    A swing high at index i: high[i] > all highs within lookback bars on each side.
    """
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    n = len(candles)

    for i in range(lookback, n - lookback):
        window_h = [candles[j]["high"] for j in range(i - lookback, i + lookback + 1)]
        window_l = [candles[j]["low"] for j in range(i - lookback, i + lookback + 1)]
        mid = lookback

        if candles[i]["high"] == max(window_h) and window_h.count(max(window_h)) == 1:
            highs.append((i, candles[i]["high"]))
        if candles[i]["low"] == min(window_l) and window_l.count(min(window_l)) == 1:
            lows.append((i, candles[i]["low"]))

    return highs, lows


def cluster_levels(
    points: list[tuple[int, float]], tolerance: float
) -> list[dict[str, Any]]:
    """Cluster nearby price levels and count touches."""
    if not points:
        return []
    sorted_pts = sorted(points, key=lambda x: x[1])
    clusters: list[dict[str, Any]] = []
    current = [sorted_pts[0]]

    for pt in sorted_pts[1:]:
        if abs(pt[1] - current[-1][1]) <= tolerance:
            current.append(pt)
        else:
            price = sum(p[1] for p in current) / len(current)
            clusters.append(
                {
                    "price": price,
                    "touches": len(current),
                    "last_index": max(p[0] for p in current),
                }
            )
            current = [pt]

    if current:
        price = sum(p[1] for p in current) / len(current)
        clusters.append(
            {
                "price": price,
                "touches": len(current),
                "last_index": max(p[0] for p in current),
            }
        )

    return sorted(clusters, key=lambda c: c["price"])


# ── Linear regression ─────────────────────────────────────────────────────────

def linear_regression(
    xs: list[float], ys: list[float]
) -> tuple[float, float]:
    """Returns (slope, intercept)."""
    n = len(xs)
    if n < 2:
        return 0.0, ys[-1] if ys else 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0, my
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom
    return slope, my - slope * mx


# ── Misc ──────────────────────────────────────────────────────────────────────

def clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return (new - old) / abs(old) * 100
