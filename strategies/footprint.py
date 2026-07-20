"""Footprint chart strategy: delta profile, absorption at extremes,
stacked imbalances, delta exhaustion, delta confirmation, POC/Value-Area,
PLUS three new signals:
  6. Delta divergence        — price new extreme but delta diverges (reversal warning)
  7. Cumulative delta trend  — net buy/sell pressure over last N candles
  8. Aggressive order ratio  — measures how committed directional flow is

Uses OHLCV + taker-buy delta from Binance klines (no tick data required).
Candle fields expected: open, high, low, close, volume, delta, time.
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
    """Approximate Point of Control and Value Area from OHLCV."""
    subset = candles[-lookback:]
    if not subset:
        return None, None, None
    lo = min(c["low"] for c in subset)
    hi = max(c["high"] for c in subset)
    rng = hi - lo
    if rng < 1e-12:
        return None, None, None

    BINS = 20
    bucket_vol  = [0.0] * BINS
    bucket_size = rng / BINS

    for c in subset:
        c_lo, c_hi, vol = c["low"], c["high"], c["volume"]
        c_rng = max(c_hi - c_lo, bucket_size)
        for b in range(BINS):
            b_lo    = lo + b * bucket_size
            b_hi    = b_lo + bucket_size
            overlap = max(0.0, min(c_hi, b_hi) - max(c_lo, b_lo))
            bucket_vol[b] += vol * (overlap / c_rng)

    poc_idx = max(range(BINS), key=lambda i: bucket_vol[i])
    poc     = lo + (poc_idx + 0.5) * bucket_size

    total    = sum(bucket_vol)
    va_target = total * 0.70
    va_vol    = bucket_vol[poc_idx]
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
    val = lo + lo_idx       * bucket_size
    return round(poc, 8), round(vah, 8), round(val, 8)


# ── original signal detectors ─────────────────────────────────────────────────

def _absorption(candles, avg_d):
    """Heavy-volume wick rejected at the extreme → passive side absorbs aggressor."""
    score   = 0.0
    reasons = []
    for c in candles[-4:]:
        rng = c["high"] - c["low"]
        if rng < 1e-12:
            continue
        cp         = _close_pos(c)
        upper_wick = (c["high"] - max(c["open"], c["close"])) / rng
        lower_wick = (min(c["open"], c["close"]) - c["low"])  / rng
        heavy      = abs(c["delta"]) > avg_d * 1.8

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
    score   = 0.0
    reasons = []
    threshold   = avg_d * 0.65
    streak_bull = streak_bear = 0
    for c in candles[-12:]:
        if c["delta"] > threshold:
            streak_bull += 1
            streak_bear  = 0
        elif c["delta"] < -threshold:
            streak_bear += 1
            streak_bull  = 0
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
    """Peak delta + close at opposite extreme = climax reversal signal."""
    score   = 0.0
    reasons = []
    if len(candles) < 5:
        return score, reasons

    last   = candles[-1]
    window = candles[-20:-1]
    if not window:
        return score, reasons

    rng = last["high"] - last["low"]
    if rng < 1e-12:
        return score, reasons

    cp       = _close_pos(last)
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
    """Delta aligned with close position across multiple candles = genuine flow."""
    score   = 0.0
    reasons = []
    bull = bear = 0
    for c in candles[-3:]:
        cp     = _close_pos(c)
        strong = abs(c["delta"]) > avg_d * 0.75
        if cp > 0.65 and c["delta"] > 0 and strong:
            bull += 1
        elif cp < 0.35 and c["delta"] < 0 and strong:
            bear += 1

    if bull >= 2:
        score += 0.40
        reasons.append(
            f"Delta confirmation: {bull}/3 candles close high + positive delta — genuine buying"
        )
    elif bear >= 2:
        score -= 0.40
        reasons.append(
            f"Delta confirmation: {bear}/3 candles close low + negative delta — genuine selling"
        )
    elif bull == 1:
        score += 0.15
        reasons.append("Weak delta confirmation: last candle closed high + positive delta")
    elif bear == 1:
        score -= 0.15
        reasons.append("Weak delta confirmation: last candle closed low + negative delta")
    return score, reasons


def _poc_context(candles, price, a):
    """POC and Value Area acceptance zones."""
    score   = 0.0
    reasons = []
    poc, vah, val = _poc_and_va(candles)
    if poc is None:
        return score, reasons, None

    if vah is not None and val is not None:
        if price > vah:
            score += 0.20
            reasons.append(
                f"Price above VAH ({vah:.6g}) — buyer acceptance, bullish bias"
            )
        elif price < val:
            score -= 0.20
            reasons.append(
                f"Price below VAL ({val:.6g}) — seller acceptance, bearish bias"
            )

    if abs(price - poc) < a * 0.5:
        reasons.append(
            f"Price at POC ({poc:.6g}) — high-volume node, wait for delta direction"
        )
    elif price > poc:
        score += 0.08
    else:
        score -= 0.08

    return score, reasons, {"poc": poc, "vah": vah, "val": val}


# ── NEW signal detectors ──────────────────────────────────────────────────────

def _delta_divergence(candles, avg_d):
    """Detect delta divergence: price makes new high/low but delta diverges.

    Bearish divergence: price higher high but delta lower high → buyers exhausting.
    Bullish divergence: price lower low but delta higher low → sellers exhausting.
    Requires at least 10 candles. Uses last 2 swing comparisons.
    """
    score   = 0.0
    reasons = []
    n = len(candles)
    if n < 10:
        return score, reasons

    # Compare last 2 groups of candles (split roughly in half)
    mid   = n // 2
    group1 = candles[mid - n//4 : mid]
    group2 = candles[-n//4:]
    if not group1 or not group2:
        return score, reasons

    price1_high  = max(c["close"] for c in group1)
    price1_low   = min(c["close"] for c in group1)
    price2_high  = max(c["close"] for c in group2)
    price2_low   = min(c["close"] for c in group2)

    delta1_high  = max(c["delta"] for c in group1)
    delta1_low   = min(c["delta"] for c in group1)
    delta2_high  = max(c["delta"] for c in group2)
    delta2_low   = min(c["delta"] for c in group2)

    # Bearish divergence: price higher high, delta lower high
    if price2_high > price1_high * 1.002 and delta2_high < delta1_high * 0.85:
        div_strength = min((delta1_high - delta2_high) / max(abs(delta1_high), 1e-9), 1.0)
        score -= 0.50 * (0.5 + div_strength * 0.5)
        reasons.append(
            f"Bearish delta divergence: price made new high but buy-delta declining "
            f"({delta1_high:.0f} → {delta2_high:.0f}) — buyers exhausting"
        )

    # Bullish divergence: price lower low, delta higher low (less negative)
    elif price2_low < price1_low * 0.998 and delta2_low > delta1_low * 0.85:
        div_strength = min((delta2_low - delta1_low) / max(abs(delta1_low), 1e-9), 1.0)
        score += 0.50 * (0.5 + div_strength * 0.5)
        reasons.append(
            f"Bullish delta divergence: price made new low but sell-delta shrinking "
            f"({delta1_low:.0f} → {delta2_low:.0f}) — sellers exhausting"
        )

    return score, reasons


def _cumulative_delta_trend(candles, avg_d):
    """Cumulative delta over last N candles: sustained buy/sell pressure trend.

    A consistently positive cumulative delta over 10 candles = sustained buyers.
    Trend of cumulative delta (rising = accelerating buy pressure).
    """
    score   = 0.0
    reasons = []
    window  = candles[-15:]
    if len(window) < 6:
        return score, reasons

    cum_delta = 0.0
    cum_series = []
    for c in window:
        cum_delta += c["delta"]
        cum_series.append(cum_delta)

    total_cum = cum_series[-1]
    mid_cum   = cum_series[len(cum_series) // 2]

    # Trend: is cumulative delta accelerating or decelerating?
    trend_acc = total_cum - mid_cum   # positive = buying accelerating in 2nd half

    threshold = avg_d * len(window) * 0.3

    if total_cum > threshold:
        score += 0.30
        if trend_acc > 0:
            score += 0.15
            reasons.append(
                f"Cumulative delta strongly bullish ({total_cum:.0f}) and accelerating — "
                "sustained buying pressure"
            )
        else:
            reasons.append(
                f"Cumulative delta bullish ({total_cum:.0f}) but decelerating — "
                "buying pressure waning"
            )
    elif total_cum < -threshold:
        score -= 0.30
        if trend_acc < 0:
            score -= 0.15
            reasons.append(
                f"Cumulative delta strongly bearish ({total_cum:.0f}) and accelerating — "
                "sustained selling pressure"
            )
        else:
            reasons.append(
                f"Cumulative delta bearish ({total_cum:.0f}) but decelerating — "
                "selling pressure waning"
            )

    return score, reasons


def _aggressive_order_ratio(candles, avg_d):
    """Aggressive order ratio: abs(delta)/volume per candle.

    High ratio = one side is very dominant (aggressive market orders).
    Low ratio = mixed, passive (limit order) dominated market.
    """
    score   = 0.0
    reasons = []
    window  = candles[-6:]
    if not window:
        return score, reasons

    ratios     = []
    directions = []
    for c in window:
        vol = c.get("volume", 0)
        if vol < 1e-9:
            continue
        ratio = abs(c["delta"]) / vol
        ratios.append(ratio)
        directions.append(1 if c["delta"] > 0 else -1)

    if not ratios:
        return score, reasons

    avg_ratio = sum(ratios) / len(ratios)
    last_ratio = ratios[-1]
    last_dir   = directions[-1]

    # High aggression (> 60% of volume is directional) + consistent direction
    if last_ratio > 0.60:
        consistent = sum(1 for d in directions[-3:] if d == last_dir) >= 2
        if last_dir > 0 and consistent:
            score += 0.25
            reasons.append(
                f"Aggressive buyers: {last_ratio*100:.0f}% of volume is directional buy — "
                "strong conviction long"
            )
        elif last_dir < 0 and consistent:
            score -= 0.25
            reasons.append(
                f"Aggressive sellers: {last_ratio*100:.0f}% of volume is directional sell — "
                "strong conviction short"
            )

    # Sudden spike in aggression (last candle 2x avg ratio)
    if avg_ratio > 1e-6 and last_ratio > avg_ratio * 2.0:
        if last_dir > 0:
            score += 0.15
            reasons.append(
                f"Aggression spike: buyer aggression jumped {last_ratio/avg_ratio:.1f}x baseline"
            )
        else:
            score -= 0.15
            reasons.append(
                f"Aggression spike: seller aggression jumped {last_ratio/avg_ratio:.1f}x baseline"
            )

    return score, reasons


# ── main ──────────────────────────────────────────────────────────────────────

def analyze(candles):
    a     = atr(candles) or (candles[-1]["close"] * 0.005)
    price = candles[-1]["close"]
    avg_d = _avg_abs_delta(candles)

    total_score = 0.0
    all_reasons = []

    # 1. Absorption at extremes (precision reversal — highest weight)
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

    # 4. Delta confirmation (genuine directional flow)
    s, r = _delta_confirmation(candles, avg_d)
    total_score += s
    all_reasons.extend(r)

    # 5. POC / Value Area context
    s, r, poc_data = _poc_context(candles, price, a)
    total_score += s
    all_reasons.extend(r)

    # 6. Delta divergence (NEW — early reversal warning)
    s, r = _delta_divergence(candles, avg_d)
    total_score += s
    all_reasons.extend(r)

    # 7. Cumulative delta trend (NEW — sustained pressure direction)
    s, r = _cumulative_delta_trend(candles, avg_d)
    total_score += s
    all_reasons.extend(r)

    # 8. Aggressive order ratio (NEW — conviction of directional flow)
    s, r = _aggressive_order_ratio(candles, avg_d)
    total_score += s
    all_reasons.extend(r)

    # Build compact delta-close profile for AI context (last 8 candles)
    delta_profile = [
        {
            "delta":     round(c["delta"], 2),
            "close_pct": round(_close_pos(c), 2),
            "aggr":      round(abs(c["delta"]) / max(c.get("volume", 1), 1e-9), 3),
        }
        for c in candles[-8:]
    ]

    # Cumulative delta trend value for AI context
    cum_d  = sum(c["delta"] for c in candles[-15:])

    overlay = {
        "avg_abs_delta":        round(avg_d, 2),
        "cumulative_delta_15":  round(cum_d, 2),
        "delta_close_profile":  delta_profile,
    }
    if poc_data:
        overlay.update(poc_data)

    if not all_reasons:
        all_reasons.append("Footprint neutral — no significant delta signature")

    return {
        "score":    clamp(total_score),
        "reasons":  all_reasons,
        "overlays": {"footprint": overlay},
    }
