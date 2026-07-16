"""Volume analysis: volume profile, spikes, buy/sell pressure, CVD."""
from __future__ import annotations

from typing import Any

BINS = 24
VOL_SPIKE_THRESHOLD = 2.0  # ratio to average to be considered a spike


def analyze_volume(
    candles: list[dict[str, Any]],
    lookback_avg: int = 20,
    poc_bins: int = BINS,
) -> dict[str, Any]:
    """Comprehensive volume analysis.

    Returns:
        current_volume, avg_volume, ratio, spike, buy_pressure, sell_pressure,
        cvd_tail (last 20 CVD values), poc, vah, val, volume_profile
    """
    if not candles:
        return {}

    # Current vs average volume
    current_vol = candles[-1]["volume"]
    recent_vols = [c["volume"] for c in candles[-lookback_avg:]]
    avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 1.0
    ratio = current_vol / avg_vol if avg_vol > 0 else 1.0
    is_spike = ratio >= VOL_SPIKE_THRESHOLD

    # Buy/sell pressure from delta
    buy_vol = candles[-1]["taker_buy_vol"] if "taker_buy_vol" in candles[-1] else current_vol * 0.5
    sell_vol = current_vol - buy_vol
    total_recent = sum(c["volume"] for c in candles[-5:]) or 1.0
    total_buy = sum(c.get("taker_buy_vol", c["volume"] * 0.5) for c in candles[-5:])
    buy_pct = total_buy / total_recent * 100 if total_recent > 0 else 50.0

    # Cumulative Volume Delta (CVD)
    cvd = 0.0
    cvd_series: list[float] = []
    for c in candles:
        cvd += c.get("delta", 0.0)
        cvd_series.append(cvd)
    cvd_tail = cvd_series[-20:]

    # CVD divergence: price up but CVD down (bearish div) or vice versa
    cvd_divergence: str | None = None
    if len(cvd_tail) >= 10:
        price_change = candles[-1]["close"] - candles[-10]["close"]
        cvd_change = cvd_tail[-1] - cvd_tail[-10]
        if price_change > 0 and cvd_change < 0:
            cvd_divergence = "bearish"
        elif price_change < 0 and cvd_change > 0:
            cvd_divergence = "bullish"

    # Volume Profile (simplified price-at-volume)
    all_prices = [c["close"] for c in candles]
    lo, hi = min(c["low"] for c in candles), max(c["high"] for c in candles)
    step = (hi - lo) / poc_bins if hi > lo else 1.0
    bucket_vols = [0.0] * poc_bins

    for c in candles:
        b0 = int((c["low"] - lo) / step)
        b1 = int((c["high"] - lo) / step)
        b0, b1 = max(0, min(b0, poc_bins - 1)), max(0, min(b1, poc_bins - 1))
        span = b1 - b0 + 1
        for b in range(b0, b1 + 1):
            bucket_vols[b] += c["volume"] / span

    total_vol = sum(bucket_vols) or 1.0
    poc_bin = max(range(poc_bins), key=lambda b: bucket_vols[b])
    poc = lo + (poc_bin + 0.5) * step

    # Value area (70% of volume around POC)
    covered = bucket_vols[poc_bin]
    lo_b, hi_b = poc_bin, poc_bin
    while covered / total_vol < 0.70 and (lo_b > 0 or hi_b < poc_bins - 1):
        down = bucket_vols[lo_b - 1] if lo_b > 0 else -1
        up = bucket_vols[hi_b + 1] if hi_b < poc_bins - 1 else -1
        if up >= down:
            hi_b += 1
            covered += bucket_vols[hi_b]
        else:
            lo_b -= 1
            covered += bucket_vols[lo_b]

    val = lo + lo_b * step
    vah = lo + (hi_b + 1) * step

    return {
        "current_volume": current_vol,
        "avg_volume": round(avg_vol, 2),
        "ratio": round(ratio, 2),
        "spike": is_spike,
        "buy_pct": round(buy_pct, 1),
        "sell_pct": round(100 - buy_pct, 1),
        "cvd": round(cvd, 2),
        "cvd_tail": [round(v, 2) for v in cvd_tail],
        "cvd_divergence": cvd_divergence,
        "poc": poc,
        "vah": vah,
        "val": val,
        "price_above_vah": candles[-1]["close"] > vah,
        "price_below_val": candles[-1]["close"] < val,
        "price_at_poc": abs(candles[-1]["close"] - poc) < step * 2,
    }
