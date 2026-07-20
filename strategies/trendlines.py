"""Trendline detection — enhanced with touch counting, R² quality scoring,
channel detection (ascending/descending/horizontal), and wedge classification."""
from .helpers import swing_points, linear_regression, atr, clamp


def _fit_line(points, candles, is_high=True):
    """Fit a line through swing points; return enriched dict or None."""
    if len(points) < 2:
        return None
    pts = points[-6:]   # up to 6 most recent swing points
    xs  = [p[0] for p in pts]
    ys  = [p[1] for p in pts]
    slope, intercept = linear_regression(xs, ys)
    a = atr(candles) or 1e-9

    # Residual quality
    resid = sum(abs(ys[i] - (slope * xs[i] + intercept)) for i in range(len(xs))) / len(xs)
    if resid > a * 1.8:
        return None

    # R² goodness of fit
    mean_y = sum(ys) / len(ys)
    ss_tot = sum((y - mean_y) ** 2 for y in ys) or 1e-12
    ss_res = sum((ys[i] - (slope * xs[i] + intercept)) ** 2 for i in range(len(xs)))
    r2 = max(0.0, 1.0 - ss_res / ss_tot)
    if r2 < 0.65:
        return None

    n = len(candles) - 1
    i0 = xs[0]
    return {
        "slope":      slope,
        "intercept":  intercept,
        "r2":         round(r2, 3),
        "swing_pts":  len(pts),
        "start":      {"time": candles[i0]["time"],
                       "price": round(slope * i0 + intercept, 8)},
        "end":        {"time": candles[n]["time"],
                       "price": round(slope * n + intercept, 8)},
        "value_now":  slope * n + intercept,
        "i0":         i0,
    }


def _candle_touch_count(candles, slope, intercept, a, is_high):
    """Count candles touching the trendline (within 0.55 ATR)."""
    touches = 0
    for i, c in enumerate(candles):
        lv = slope * i + intercept
        ref = c["high"] if is_high else c["low"]
        if abs(ref - lv) < a * 0.55:
            touches += 1
    return touches


def analyze(candles):
    highs, lows = swing_points(candles, lookback=3)
    a     = atr(candles) or (candles[-1]["close"] * 0.005)
    price = candles[-1]["close"]
    n     = len(candles) - 1

    upper = _fit_line(highs, candles, is_high=True)
    lower = _fit_line(lows,  candles, is_high=False)

    score   = 0.0
    reasons = []
    trendlines_out = []

    # Count all candle touches along each line
    if upper:
        upper["all_touches"] = _candle_touch_count(
            candles, upper["slope"], upper["intercept"], a, True)
    if lower:
        lower["all_touches"] = _candle_touch_count(
            candles, lower["slope"], lower["intercept"], a, False)

    # ── Lower trendline ────────────────────────────────────────────────────
    if lower:
        touch_bonus = min(lower["all_touches"] * 0.04, 0.25)
        quality_str = f"{lower['all_touches']}T R²={lower['r2']:.2f}"

        if lower["slope"] > 0:
            score += 0.30 + touch_bonus
            reasons.append(f"Ascending support trendline ({quality_str})")
        elif lower["slope"] < 0:
            score -= 0.10   # slight negative — descending support less reliable
            reasons.append(f"Descending support trendline ({quality_str})")

        dist = (price - lower["value_now"]) / a
        if 0 <= dist < 1.2:
            score += 0.45
            reasons.append("Price bouncing at trendline support — precision entry zone")
        elif -0.5 < dist < 0:
            score -= 0.50
            reasons.append("Price broke below trendline support — bearish structure break")
        elif dist < -0.5:
            score -= 0.35
            reasons.append("Well below broken trendline support — continuation down")

        trendlines_out.append({
            "type":        "support",
            "start":       lower["start"],
            "end":         lower["end"],
            "touches":     lower["all_touches"],
            "r2":          lower["r2"],
        })

    # ── Upper trendline ────────────────────────────────────────────────────
    if upper:
        touch_bonus = min(upper["all_touches"] * 0.04, 0.25)
        quality_str = f"{upper['all_touches']}T R²={upper['r2']:.2f}"

        if upper["slope"] < 0:
            score -= 0.30 + touch_bonus
            reasons.append(f"Descending resistance trendline ({quality_str})")
        elif upper["slope"] > 0:
            score += 0.10   # ascending resistance — slightly bullish bias
            reasons.append(f"Ascending resistance trendline ({quality_str})")

        dist = (upper["value_now"] - price) / a
        if 0 <= dist < 1.2:
            score -= 0.45
            reasons.append("Price pressing into trendline resistance — rejection risk")
        elif -0.5 < dist < 0:
            score += 0.50
            reasons.append("Price broke above trendline resistance — bullish structure break")
        elif dist < -0.5:
            score += 0.35
            reasons.append("Well above broken trendline resistance — continuation up")

        trendlines_out.append({
            "type":    "resistance",
            "start":   upper["start"],
            "end":     upper["end"],
            "touches": upper["all_touches"],
            "r2":      upper["r2"],
        })

    # ── Channel & wedge detection ──────────────────────────────────────────
    channel_type = None
    if upper and lower:
        slope_diff = abs(upper["slope"] - lower["slope"])
        parallel   = slope_diff < a * 0.004

        if parallel:
            avg_slope = (upper["slope"] + lower["slope"]) / 2
            flat      = a * 0.0012

            if avg_slope > flat:
                channel_type = "ascending"
                score += 0.22
                reasons.append("Ascending channel — bias long toward lower rail")
            elif avg_slope < -flat:
                channel_type = "descending"
                score -= 0.22
                reasons.append("Descending channel — bias short toward upper rail")
            else:
                channel_type = "horizontal"
                reasons.append("Horizontal channel — range-bound, trade boundaries")

            ch_height = upper["value_now"] - lower["value_now"]
            if ch_height > a * 0.5:
                pos = (price - lower["value_now"]) / ch_height
                if pos < 0.20:
                    score += 0.32
                    reasons.append(f"Lower channel rail ({pos*100:.0f}% of range) — long bias")
                elif pos > 0.80:
                    score -= 0.32
                    reasons.append(f"Upper channel rail ({pos*100:.0f}% of range) — short bias")

        else:
            # Converging lines — wedge classification
            flat = a * 0.0012
            us, ls = upper["slope"], lower["slope"]
            if us > flat and ls > flat and ls > us:
                # Both rising, lows rising faster → rising wedge (bearish)
                score -= 0.22
                channel_type = "rising_wedge"
                reasons.append("Rising wedge detected — bearish compression")
            elif us < -flat and ls < -flat and us < ls:
                # Both falling, highs falling faster → falling wedge (bullish)
                score += 0.22
                channel_type = "falling_wedge"
                reasons.append("Falling wedge detected — bullish compression")
            elif us < -flat and ls > flat:
                # Highs falling, lows rising → symmetrical compression
                channel_type = "symmetrical"
                reasons.append("Converging lines — breakout imminent")
            elif us > flat and ls < -flat:
                # Highs rising, lows falling → expansion
                channel_type = "broadening"
                score -= 0.10
                reasons.append("Broadening pattern — volatile / difficult to trade")

    overlays = {"trendlines": trendlines_out, "channel_type": channel_type}
    if not reasons:
        reasons.append("No clean trendline structure")
    return {"score": clamp(score), "reasons": reasons, "overlays": overlays}
