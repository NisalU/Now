"""Candlestick & chart pattern recognition (pure Python)."""
from .helpers import swing_points, atr, clamp, linear_regression, cluster_levels


def _body(c):
    return abs(c["close"] - c["open"])


def _is_bull(c):
    return c["close"] > c["open"]


def _is_bear(c):
    return c["close"] < c["open"]


def _candle_patterns(candles):
    """Detect single/multi-candle patterns on the last closed candles."""
    out = []
    c1, c2 = candles[-2], candles[-1]
    rng2 = c2["high"] - c2["low"] or 1e-9

    # Engulfing
    if _body(c2) > _body(c1) * 1.1:
        if c2["close"] > c2["open"] and c1["close"] < c1["open"] and \
           c2["close"] >= c1["open"] and c2["open"] <= c1["close"]:
            out.append(("bullish_engulfing", 0.5, "Bullish engulfing candle"))
        if c2["close"] < c2["open"] and c1["close"] > c1["open"] and \
           c2["close"] <= c1["open"] and c2["open"] >= c1["close"]:
            out.append(("bearish_engulfing", -0.5, "Bearish engulfing candle"))

    # Pin bar / hammer / shooting star
    body = _body(c2)
    lower_wick = min(c2["open"], c2["close"]) - c2["low"]
    upper_wick = c2["high"] - max(c2["open"], c2["close"])
    if body / rng2 < 0.35:
        if lower_wick > body * 2 and lower_wick > upper_wick * 2:
            out.append(("hammer", 0.4, "Hammer / bullish pin bar (rejection of lows)"))
        if upper_wick > body * 2 and upper_wick > lower_wick * 2:
            out.append(("shooting_star", -0.4, "Shooting star / bearish pin bar (rejection of highs)"))

    # Doji (indecision) - very small body relative to range
    if body / rng2 < 0.08:
        out.append(("doji", 0.0, "Doji (indecision candle)"))

    # Harami (inside bar with reversal implication)
    if _body(c1) > 0 and _body(c2) < _body(c1) * 0.6:
        hi_body = max(c1["open"], c1["close"])
        lo_body = min(c1["open"], c1["close"])
        if lo_body <= min(c2["open"], c2["close"]) and max(c2["open"], c2["close"]) <= hi_body:
            if _is_bear(c1) and _is_bull(c2):
                out.append(("bullish_harami", 0.35, "Bullish harami (inside reversal bar)"))
            if _is_bull(c1) and _is_bear(c2):
                out.append(("bearish_harami", -0.35, "Bearish harami (inside reversal bar)"))

    # Three-candle patterns
    if len(candles) >= 3:
        c0 = candles[-3]

        # Morning star / evening star
        b0, b1, b2 = _body(c0), _body(c1), _body(c2)
        rng0 = c0["high"] - c0["low"] or 1e-9
        rng1 = c1["high"] - c1["low"] or 1e-9
        if _is_bear(c0) and b0 / rng0 > 0.5 and b1 / rng1 < 0.4 and _is_bull(c2) and \
           c2["close"] > (c0["open"] + c0["close"]) / 2 and \
           max(c1["open"], c1["close"]) < c0["close"] + b0 * 0.3:
            out.append(("morning_star", 0.55, "Morning star (bullish reversal)"))
        if _is_bull(c0) and b0 / rng0 > 0.5 and b1 / rng1 < 0.4 and _is_bear(c2) and \
           c2["close"] < (c0["open"] + c0["close"]) / 2 and \
           min(c1["open"], c1["close"]) > c0["close"] - b0 * 0.3:
            out.append(("evening_star", -0.55, "Evening star (bearish reversal)"))

        # Three white soldiers / three black crows
        if _is_bull(c0) and _is_bull(c1) and _is_bull(c2) and \
           c1["close"] > c0["close"] and c2["close"] > c1["close"] and \
           c1["open"] > c0["open"] and c2["open"] > c1["open"] and \
           b0 / rng0 > 0.4 and b1 / rng1 > 0.4:
            out.append(("three_white_soldiers", 0.6, "Three white soldiers (strong bullish continuation)"))
        if _is_bear(c0) and _is_bear(c1) and _is_bear(c2) and \
           c1["close"] < c0["close"] and c2["close"] < c1["close"] and \
           c1["open"] < c0["open"] and c2["open"] < c1["open"] and \
           b0 / rng0 > 0.4 and b1 / rng1 > 0.4:
            out.append(("three_black_crows", -0.6, "Three black crows (strong bearish continuation)"))

    return out


def _double_top_bottom(candles):
    """Double top/bottom from the last swing points."""
    out = []
    highs, lows = swing_points(candles, lookback=3)
    a = atr(candles) or 1e-9
    price = candles[-1]["close"]

    if len(highs) >= 2:
        (i1, p1), (i2, p2) = highs[-2], highs[-1]
        if abs(p1 - p2) < a * 0.7 and i2 - i1 >= 5 and price < min(p1, p2):
            out.append(("double_top", -0.6, f"Double top at {max(p1, p2):.6g}"))
    if len(lows) >= 2:
        (i1, p1), (i2, p2) = lows[-2], lows[-1]
        if abs(p1 - p2) < a * 0.7 and i2 - i1 >= 5 and price > max(p1, p2):
            out.append(("double_bottom", 0.6, f"Double bottom at {min(p1, p2):.6g}"))
    return out


def _triple_top_bottom(candles):
    """Triple top/bottom: three roughly-equal swing highs/lows in a row."""
    out = []
    highs, lows = swing_points(candles, lookback=3)
    a = atr(candles) or 1e-9
    price = candles[-1]["close"]

    if len(highs) >= 3:
        (i1, p1), (i2, p2), (i3, p3) = highs[-3], highs[-2], highs[-1]
        if i2 - i1 >= 4 and i3 - i2 >= 4 and \
           max(p1, p2, p3) - min(p1, p2, p3) < a * 0.8 and price < min(p1, p2, p3):
            out.append(("triple_top", -0.7, f"Triple top near {sum([p1, p2, p3]) / 3:.6g}"))
    if len(lows) >= 3:
        (i1, p1), (i2, p2), (i3, p3) = lows[-3], lows[-2], lows[-1]
        if i2 - i1 >= 4 and i3 - i2 >= 4 and \
           max(p1, p2, p3) - min(p1, p2, p3) < a * 0.8 and price > max(p1, p2, p3):
            out.append(("triple_bottom", 0.7, f"Triple bottom near {sum([p1, p2, p3]) / 3:.6g}"))
    return out


def _head_shoulders(candles):
    out = []
    highs, lows = swing_points(candles, lookback=3)
    a = atr(candles) or 1e-9
    if len(highs) >= 3:
        (_, l), (_, h), (_, r) = highs[-3], highs[-2], highs[-1]
        if h > l + a * 0.5 and h > r + a * 0.5 and abs(l - r) < a * 1.2:
            out.append(("head_shoulders", -0.5, "Head & shoulders forming"))
    if len(lows) >= 3:
        (_, l), (_, h), (_, r) = lows[-3], lows[-2], lows[-1]
        if h < l - a * 0.5 and h < r - a * 0.5 and abs(l - r) < a * 1.2:
            out.append(("inv_head_shoulders", 0.5, "Inverse head & shoulders forming"))
    return out


def _triangles_and_wedges(candles):
    """Triangle and wedge patterns from trendlines fit to recent swing highs/lows."""
    out = []
    highs, lows = swing_points(candles, lookback=3)
    a = atr(candles) or 1e-9
    if len(highs) < 3 or len(lows) < 3:
        return out

    hx, hy = zip(*highs[-4:]) if len(highs) >= 4 else zip(*highs[-3:])
    lx, ly = zip(*lows[-4:]) if len(lows) >= 4 else zip(*lows[-3:])
    h_slope, _ = linear_regression(list(hx), list(hy))
    l_slope, _ = linear_regression(list(lx), list(ly))
    flat = a * 0.03

    if abs(h_slope) < flat and l_slope > flat:
        out.append(("ascending_triangle", 0.4, "Ascending triangle (flat resistance, rising support)"))
    elif abs(l_slope) < flat and h_slope < -flat:
        out.append(("descending_triangle", -0.4, "Descending triangle (flat support, falling resistance)"))
    elif h_slope < -flat and l_slope > flat:
        out.append(("symmetrical_triangle", 0.0, "Symmetrical triangle (converging trendlines, breakout pending)"))
    elif h_slope > flat and l_slope > flat and h_slope < l_slope:
        out.append(("rising_wedge", -0.45, "Rising wedge (bearish reversal risk)"))
    elif h_slope < -flat and l_slope < -flat and h_slope > l_slope:
        out.append(("falling_wedge", 0.45, "Falling wedge (bullish reversal setup)"))

    return out


def _flags(candles):
    """Flag/pennant continuation: strong impulse leg followed by tight consolidation."""
    out = []
    if len(candles) < 12:
        return out
    a = atr(candles) or 1e-9
    impulse = candles[-12:-6]
    consol = candles[-6:]
    impulse_change = impulse[-1]["close"] - impulse[0]["open"]
    consol_range = max(c["high"] for c in consol) - min(c["low"] for c in consol)
    xs = list(range(len(consol)))
    ys = [c["close"] for c in consol]
    slope, _ = linear_regression(xs, ys)

    if impulse_change > a * 1.5 and consol_range < a * 1.2 and slope <= a * 0.1:
        out.append(("bull_flag", 0.35, "Bull flag (consolidation after strong upmove)"))
    elif impulse_change < -a * 1.5 and consol_range < a * 1.2 and slope >= -a * 0.1:
        out.append(("bear_flag", -0.35, "Bear flag (consolidation after strong downmove)"))
    return out


def analyze(candles):
    found = (
        _candle_patterns(candles)
        + _double_top_bottom(candles)
        + _triple_top_bottom(candles)
        + _head_shoulders(candles)
        + _triangles_and_wedges(candles)
        + _flags(candles)
    )
    score = clamp(sum(s for _, s, _ in found))
    reasons = [msg for _, _, msg in found] or ["No notable patterns"]
    overlays = {"patterns": [
        {"name": n, "direction": "bull" if s > 0 else ("bear" if s < 0 else "neutral")}
        for n, s, _ in found
    ]}
    return {"score": score, "reasons": reasons, "overlays": overlays}
