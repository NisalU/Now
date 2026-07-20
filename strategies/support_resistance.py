"""Support & resistance zones — enhanced with dynamic strength scoring,
zone rejection counting, volume confluence, failed-breakout signals,
and psychological round-number levels."""
from .helpers import swing_points, cluster_levels, atr, clamp


def _volume_at_level(candles, price, tolerance):
    """Sum volume of candles whose range overlaps the price zone."""
    total = 0.0
    for c in candles[-80:]:
        if c["low"] - tolerance <= price <= c["high"] + tolerance:
            total += c.get("volume", 0)
    return total


def _rejection_count(candles, price, tolerance):
    """Count how many times price touched the zone then reversed sharply."""
    rejections = 0
    for i in range(1, len(candles) - 1):
        c = candles[i]
        if c["low"] - tolerance <= price <= c["high"] + tolerance:
            nxt = candles[i + 1]
            prv = candles[i - 1]
            # Bearish rejection from resistance
            if c["high"] >= price - tolerance and nxt["close"] < c["close"] - tolerance * 0.3:
                rejections += 1
            # Bullish rejection from support
            elif c["low"] <= price + tolerance and nxt["close"] > c["close"] + tolerance * 0.3:
                rejections += 1
    return rejections


def _round_number_levels(price, a):
    """Return nearby psychological round-number levels."""
    levels = []
    # Find the dominant round-number step for this price magnitude
    mag = 1.0
    p = abs(price)
    while p >= 200:
        mag *= 10
        p /= 10
    while p < 20:
        mag /= 10
        p *= 10

    for divisor in [1, 2, 5]:
        step = mag / divisor
        nearest = round(price / step) * step
        if abs(nearest - price) < a * 2.5 and nearest not in levels:
            levels.append(nearest)
    return levels


def _avg_vol(candles):
    vols = [c.get("volume", 0) for c in candles[-60:] if c.get("volume", 0) > 0]
    return sum(vols) / len(vols) if vols else 1.0


def analyze(candles):
    highs, lows = swing_points(candles, lookback=3)
    a = atr(candles) or (candles[-1]["close"] * 0.005)
    tol = a * 0.75   # cluster tolerance
    zone_w = a * 0.5  # half-width for volume queries

    res_levels = [lv for lv in cluster_levels(highs, tol) if lv["touches"] >= 2][:6]
    sup_levels = [lv for lv in cluster_levels(lows, tol) if lv["touches"] >= 2][:6]

    price = candles[-1]["close"]
    avg_v = _avg_vol(candles)
    score = 0.0
    reasons = []

    # -- Enrich each level with rejection count, volume, and composite strength
    for lv in res_levels + sup_levels:
        lv["rejections"] = _rejection_count(candles, lv["price"], tol)
        lv["volume"]     = _volume_at_level(candles, lv["price"], zone_w)
        vol_factor       = min(lv["volume"] / max(avg_v * lv["touches"], 1e-9), 3.0)
        # Strength = touches + rejections (weighted higher) + volume factor
        lv["strength"]   = lv["touches"] + lv["rejections"] * 1.5 + vol_factor

    res_levels.sort(key=lambda x: -x["strength"])
    sup_levels.sort(key=lambda x: -x["strength"])

    # -- Nearest support below and resistance above -------------------------
    supports_below    = sorted([lv for lv in sup_levels if lv["price"] < price],
                               key=lambda x: price - x["price"])
    resistance_above  = sorted([lv for lv in res_levels if lv["price"] > price],
                               key=lambda x: x["price"] - price)

    if supports_below:
        s = supports_below[0]
        dist = (price - s["price"]) / a
        if dist < 2.5:
            strength_bonus = min(s["strength"] * 0.055, 0.45)
            score += 0.45 + strength_bonus
            tag = f"{s['touches']}T/{s['rejections']}R"
            reasons.append(f"Near support {s['price']:.6g} ({tag}, str={s['strength']:.1f})")
        if dist < 0.6:
            score += 0.20
            reasons.append("Price sitting on support — high-precision entry zone")

    if resistance_above:
        r = resistance_above[0]
        dist = (r["price"] - price) / a
        if dist < 2.5:
            strength_bonus = min(r["strength"] * 0.055, 0.45)
            score -= 0.45 + strength_bonus
            tag = f"{r['touches']}T/{r['rejections']}R"
            reasons.append(f"Near resistance {r['price']:.6g} ({tag}, str={r['strength']:.1f})")
        if dist < 0.6:
            score -= 0.20
            reasons.append("Price pressing into resistance — rejection risk high")

    # -- Confirmed breakout/breakdown: 2 candle closes needed --------------
    c1, c2 = candles[-2], candles[-1]
    for lv in res_levels:
        if c1["close"] > lv["price"] and c2["close"] > lv["price"] and \
                candles[-3]["close"] <= lv["price"] + a * 0.4:
            score += 0.55
            reasons.append(f"Confirmed breakout above resistance {lv['price']:.6g} (2-bar close)")
    for lv in sup_levels:
        if c1["close"] < lv["price"] and c2["close"] < lv["price"] and \
                candles[-3]["close"] >= lv["price"] - a * 0.4:
            score -= 0.55
            reasons.append(f"Confirmed breakdown below support {lv['price']:.6g} (2-bar close)")

    # -- Failed breakout (liquidity grab + snap-back = zone is strong) -----
    for lv in res_levels:
        if c2["high"] > lv["price"] and c2["close"] < lv["price"] - a * 0.15:
            score -= 0.35
            reasons.append(f"Failed breakout above {lv['price']:.6g} — resistance holding strong")
    for lv in sup_levels:
        if c2["low"] < lv["price"] and c2["close"] > lv["price"] + a * 0.15:
            score += 0.35
            reasons.append(f"Failed breakdown below {lv['price']:.6g} — support holding strong")

    # -- Previous resistance flipped to support (and vice versa) -----------
    for lv in res_levels:
        if lv["price"] < price and (price - lv["price"]) / a < 1.5:
            score += 0.25
            reasons.append(f"Previous resistance {lv['price']:.6g} flipped to support")
    for lv in sup_levels:
        if lv["price"] > price and (lv["price"] - price) / a < 1.5:
            score -= 0.25
            reasons.append(f"Previous support {lv['price']:.6g} flipped to resistance")

    # -- Psychological round-number levels ---------------------------------
    for rp in _round_number_levels(price, a):
        dist = abs(rp - price) / a
        if dist < 1.2:
            if rp <= price:
                score += 0.15
                reasons.append(f"Round-number support {rp:.6g}")
            else:
                score -= 0.15
                reasons.append(f"Round-number resistance {rp:.6g}")

    overlays = {
        "support": [{"price": lv["price"], "touches": lv["touches"],
                     "strength": round(lv["strength"], 1)} for lv in sup_levels],
        "resistance": [{"price": lv["price"], "touches": lv["touches"],
                        "strength": round(lv["strength"], 1)} for lv in res_levels],
    }
    if not reasons:
        reasons.append("Price between S/R zones — no structural edge")
    return {"score": clamp(score), "reasons": reasons, "overlays": overlays}
