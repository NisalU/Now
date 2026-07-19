"""Footprint chart strategy: delta profile, absorption at extremes,
stacked imbalances, delta exhaustion, and POC/Value-Area context.

Uses OHLCV + taker-buy delta from Binance klines (no tick data required).
Candle fields expected: open, high, low, close, volume, delta, time.

Five signals:
    1. Delta absorption at extremes  — heavy-volume wick rejected at high/low
    2. Stacked imbalances            — 3+ consecutive same-direction delta candles
    3. Delta exhaustion / climax     — peak delta but closes at opposite extreme
    4. Delta confirmation            — delta aligns with close position (genuine flow)
    5. POC / Value-Area context      — volume-weighted price acceptance zones
"""
from .helpers import atr, clamp


# ── helpers ───────────────────────────────────────────────────────────────────

def _close_pos(c):
    """Where did the candle close in its range? 0.0 (low) … 1.0 (high)."""
    rng = c["high"] - c["low"]
    if rng < 1e-12:
        return 0.5
    return (c["close"] - c["low"]) / rng


def _avg_abs_delta(candles, n=60):
    vals = [abs(c["delta"]) for c in candles[-n:]]
    return sum(vals) / len(vals) if vals else 1e-9


def _poc_and_va(candles, lookback=40):
    """Approximate Point of Control and Value Area from OHLCV.

    Distributes each candle's volume proportionally across 20 price buckets
    based on how much of the candle range overlaps each bucket.

    Returns (poc_price, vah, val) rounded to 8 dp, or (None, None, None).
    """
    subset = candles[-lookback:]
    if not subset:
        return None, None, None
    lo = min(c["low"] for c in subset)
    hi = max(c["high"] for c in subset)
    rng = hi - lo
    if rng < 1e-12:
        return None, None, None

    BINS = 20
    bucket_vol = [0.0] * BINS
    bucket_size = rng / BINS

    for c in subset:
        c_lo, c_hi, vol = c["low"], c["high"], c["volume"]
        c_rng = max(c_hi - c_lo, bucket_size)
        for b in range(BINS):
            b_lo = lo + b * bucket_size
            b_hi = b_lo + bucket_size
            overlap = max(0.0, min(c_hi, b_hi) - max(c_lo, b_lo))
            bucket_vol[b] += vol * (overlap / c_rng)

    poc_idx = max(range(BINS), key=lambda i: bucket_vol[i])
    poc = lo + (poc_idx + 0.5) * bucket_size

    # Value Area: expand from POC until 70% of total volume is captured
    total = sum(bucket_vol)
    va_target = total * 0.70
    va_vol = bucket_vol[poc_idx]
    lo_idx = hi_idx = poc_idx
    while va_vol < va_target and (lo_idx > 0 or hi_idx < BINS - 1):
        add_lo = bucket_vol[lo_idx - 1] if lo_idx > 0 else -1
        add_hi = bucket_vol[hi_idx + 1] if hi_idx < BINS - 1 else -1
        if add_lo >= add_hi:
            lo_idx -= 1
            va_vol += add_lo
        else:
            hi_idx += 1
            va_vol += add_hi

    vah = lo + (hi_idx + 1) * bucket_size
    val = lo + lo_idx * bucket_size
    return round(poc, 8), round(vah, 8), round(val, 8)


# ── signal detectors ──────────────────────────────────────────────────────────

def _absorption(candles, avg_d):
    """Heavy-volume wick that gets rejected at the extreme.

    Bearish absorption: big positive delta + upper wick > 55% of range
        + close below midpoint → buyers absorbed by passive sellers → SHORT.
    Bullish absorption: big negative delta + lower wick > 55% of range
        + close above midpoint → sellers absorbed by passive buyers → LONG.
    """
    score = 0.0
    reasons = []
    for c in candles[-4:]:
        rng = c["high"] - c["low"]
        if rng < 1e-12:
            continue
        cp = _close_pos(c)
        upper_wick = (c["high"] - max(c["open"], c["close"])) / rng
        lower_wick = (min(c["open"], c["close"]) - c["low"]) / rng
        heavy = abs(c["delta"]) > avg_d * 1.8

        if heavy and c["delta"] > 0 and upper_wick > 0.55 and cp < 0.45:
            score -= 0.65
            reasons.append(
                f"Bearish absorption: {c['delta']:.0f} buy-delta absorbed at high — "
                f"upper wick {upper_wick*100:.0f}%, close at {cp*100:.0f}% of range"
            )
        elif heavy and c["delta"] < 0 and lower_wick > 0.55 and cp > 0.55:
            score += 0.65
            reasons.append(
                f"Bullish absorption: {c['delta']:.0f} sell-delta absorbed at low — "
                f"lower wick {lower_wick*100:.0f}%, close at {cp*100:.0f}% of range"
            )
    return score, reasons


def _stacked_imbalances(candles, avg_d):
    """3+ consecutive same-direction delta candles = momentum continuation."""
    score = 0.0
    reasons = []
    threshold = avg_d * 0.65
    streak_bull = streak_bear = 0
    for c in candles[-12:]:
        if c["delta"] > threshold:
            streak_bull += 1
            streak_bear = 0
        elif c["delta"] < -threshold:
            streak_bear += 1
            streak_bull = 0
        else:
            streak_bull = streak_bear = 0

    if streak_bull >= 3:
        s = min(0.30 + (streak_bull - 3) * 0.08, 0.60)
        score += s
        reasons.append(
            f"Stacked buy imbalance: {streak_bull} consecutive positive-delta candles — "
            "buyer momentum confirmed"
        )
    elif streak_bear >= 3:
        s = min(0.30 + (streak_bear - 3) * 0.08, 0.60)
        score -= s
        reasons.append(
            f"Stacked sell imbalance: {streak_bear} consecutive negative-delta candles — "
            "seller momentum confirmed"
        )
    return score, reasons


def _exhaustion(candles, avg_d):
    """Peak delta + close at opposite extreme = climax reversal.

    The current candle has the largest absolute delta in the last 20 candles
    but the close is at the opposite end of the range → buyers/sellers spent.
    """
    score = 0.0
    reasons = []
    if len(candles) < 5:
        return score, reasons

    last = candles[-1]
    window = candles[-20:-1]
    if not window:
        return score, reasons

    rng = last["high"] - last["low"]
    if rng < 1e-12:
        return score, reasons

    cp = _close_pos(last)
    max_bull = max((c["delta"] for c in window), default=0)
    max_bear = min((c["delta"] for c in window), default=0)

    if last["delta"] > max(max_bull, avg_d * 2.5) and cp < 0.35:
        score -= 0.75
        reasons.append(
            f"Buy climax exhaustion: peak buy-delta {last['delta']:.0f} but close at "
            f"{cp*100:.0f}% of range — buyers spent, reversal very likely"
        )
    elif last["delta"] < min(max_bear, -avg_d * 2.5) and cp > 0.65:
        score += 0.75
        reasons.append(
            f"Sell climax exhaustion: peak sell-delta {last['delta']:.0f} but close at "
            f"{cp*100:.0f}% of range — sellers spent, reversal very likely"
        )
    return score, reasons


def _delta_confirmation(candles, avg_d):
    """Delta aligns with close position across multiple candles = genuine flow."""
    score = 0.0
    reasons = []
    bull = bear = 0
    for c in candles[-3:]:
        cp = _close_pos(c)
        strong = abs(c["delta"]) > avg_d * 0.75
        if cp > 0.65 and c["delta"] > 0 and strong:
            bull += 1
        elif cp < 0.35 and c["delta"] < 0 and strong:
            bear += 1

    if bull >= 2:
        score += 0.40
        reasons.append(
            f"Delta confirmation: {bull}/3 candles close high + positive delta — genuine buying pressure"
        )
    elif bear >= 2:
        score -= 0.40
        reasons.append(
            f"Delta confirmation: {bear}/3 candles close low + negative delta — genuine selling pressure"
        )
    elif bull == 1:
        score += 0.15
        reasons.append("Weak delta confirmation: last candle close high + positive delta")
    elif bear == 1:
        score -= 0.15
        reasons.append("Weak delta confirmation: last candle close low + negative delta")
    return score, reasons


def _poc_context(candles, price, a):
    """POC and Value Area: price acceptance zones."""
    score = 0.0
    reasons = []
    poc, vah, val = _poc_and_va(candles)
    if poc is None:
        return score, reasons, None

    if vah is not None and val is not None:
        if price > vah:
            score += 0.20
            reasons.append(
                f"Price above Value Area High ({vah:.6g}) — buyer acceptance zone, bullish bias"
            )
        elif price < val:
            score -= 0.20
            reasons.append(
                f"Price below Value Area Low ({val:.6g}) — seller acceptance zone, bearish bias"
            )

    if abs(price - poc) < a * 0.5:
        reasons.append(
            f"Price at POC ({poc:.6g}) — high-volume node, watch delta on next candle for direction"
        )
    elif price > poc:
        score += 0.08
    else:
        score -= 0.08

    return score, reasons, {"poc": poc, "vah": vah, "val": val}


# ── main ──────────────────────────────────────────────────────────────────────

def analyze(candles):
    a = atr(candles) or (candles[-1]["close"] * 0.005)
    price = candles[-1]["close"]
    avg_d = _avg_abs_delta(candles)

    total_score = 0.0
    all_reasons = []

    # 1. Absorption at extremes (highest weight — precision reversal signal)
    s, r = _absorption(candles, avg_d)
    total_score += s
    all_reasons.extend(r)

    # 2. Stacked imbalances (momentum continuation)
    s, r = _stacked_imbalances(candles, avg_d)
    total_score += s
    all_reasons.extend(r)

    # 3. Delta exhaustion / climax (high-confidence fade)
    s, r = _exhaustion(candles, avg_d)
    total_score += s
    all_reasons.extend(r)

    # 4. Delta confirmation (genuine directional pressure)
    s, r = _delta_confirmation(candles, avg_d)
    total_score += s
    all_reasons.extend(r)

    # 5. POC / Value Area context
    s, r, poc_data = _poc_context(candles, price, a)
    total_score += s
    all_reasons.extend(r)

    # Build compact delta-close profile for AI context (last 6 candles)
    delta_profile = [
        {
            "delta": round(c["delta"], 2),
            "close_pct": round(_close_pos(c), 2),
        }
        for c in candles[-6:]
    ]

    overlay = {
        "avg_abs_delta": round(avg_d, 2),
        "delta_close_profile": delta_profile,
    }
    if poc_data:
        overlay.update(poc_data)

    if not all_reasons:
        all_reasons.append("Footprint neutral — no significant delta signature")

    return {
        "score": clamp(total_score),
        "reasons": all_reasons,
        "overlays": {"footprint": overlay},
    }
