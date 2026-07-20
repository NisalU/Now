"""Chart pattern recognition — structural patterns only (candlestick patterns removed).

Detects:
  - Head & Shoulders / Inverse Head & Shoulders
  - Ascending / Descending / Symmetrical Triangles
  - Rising / Falling Wedges
  - Bull / Bear Flags
  - Double Top / Double Bottom
  - Triple Top / Triple Bottom
  - Cup & Handle
  - Broadening Formation (Megaphone)

Each pattern returns score, reasons, and overlay data for live chart visualization.
Overlay format (chart_patterns list):
  {"type": str, "direction": "bullish"|"bearish"|"neutral",
   "name": str, "confirmed": bool,
   "lines": [{"start": {"time": t, "price": p}, "end": {"time": t, "price": p}}],
   "key_levels": [{"price": p, "label": str}]}
"""
from .helpers import swing_points, atr, clamp, linear_regression, cluster_levels


def _seg(candles, i0, p0, i1, p1):
    """Create a line segment dict from candle indices and prices."""
    i0 = max(0, min(i0, len(candles) - 1))
    i1 = max(0, min(i1, len(candles) - 1))
    return {
        "start": {"time": candles[i0]["time"], "price": round(float(p0), 8)},
        "end":   {"time": candles[i1]["time"], "price": round(float(p1), 8)},
    }


# ---------------------------------------------------------------------------
# Double Top / Bottom
# ---------------------------------------------------------------------------

def _double_top_bottom(candles):
    out = []
    highs, lows = swing_points(candles, lookback=3)
    a = atr(candles) or 1e-9
    price = candles[-1]["close"]
    n = len(candles) - 1

    if len(highs) >= 2:
        (i1, p1), (i2, p2) = highs[-2], highs[-1]
        if abs(p1 - p2) < a * 0.7 and i2 - i1 >= 5 and price < min(p1, p2):
            valley_low = min(c["low"] for c in candles[i1:i2 + 1])
            confirmed = price < valley_low
            lines = [
                _seg(candles, i1, p1, i2, p2),
                _seg(candles, 0, valley_low, n, valley_low),
            ]
            out.append(("double_top", -0.65 if confirmed else -0.40,
                        f"Double top ~{max(p1, p2):.6g} — neckline {valley_low:.6g}"
                        + (" [CONFIRMED]" if confirmed else ""),
                        {"type": "double_top", "direction": "bearish",
                         "name": "Double Top", "confirmed": confirmed,
                         "lines": lines,
                         "key_levels": [{"price": valley_low, "label": "DT Neckline"},
                                        {"price": max(p1, p2), "label": "DT Resistance"}]}))

    if len(lows) >= 2:
        (i1, p1), (i2, p2) = lows[-2], lows[-1]
        if abs(p1 - p2) < a * 0.7 and i2 - i1 >= 5 and price > max(p1, p2):
            peak_high = max(c["high"] for c in candles[i1:i2 + 1])
            confirmed = price > peak_high
            lines = [
                _seg(candles, i1, p1, i2, p2),
                _seg(candles, 0, peak_high, n, peak_high),
            ]
            out.append(("double_bottom", 0.65 if confirmed else 0.40,
                        f"Double bottom ~{min(p1, p2):.6g} — neckline {peak_high:.6g}"
                        + (" [CONFIRMED]" if confirmed else ""),
                        {"type": "double_bottom", "direction": "bullish",
                         "name": "Double Bottom", "confirmed": confirmed,
                         "lines": lines,
                         "key_levels": [{"price": peak_high, "label": "DB Neckline"},
                                        {"price": min(p1, p2), "label": "DB Support"}]}))
    return out


# ---------------------------------------------------------------------------
# Triple Top / Bottom
# ---------------------------------------------------------------------------

def _triple_top_bottom(candles):
    out = []
    highs, lows = swing_points(candles, lookback=3)
    a = atr(candles) or 1e-9
    price = candles[-1]["close"]
    n = len(candles) - 1

    if len(highs) >= 3:
        (i1, p1), (i2, p2), (i3, p3) = highs[-3], highs[-2], highs[-1]
        if i2 - i1 >= 4 and i3 - i2 >= 4 and \
                max(p1, p2, p3) - min(p1, p2, p3) < a * 0.9 and price < min(p1, p2, p3):
            top_price = (p1 + p2 + p3) / 3
            out.append(("triple_top", -0.75,
                        f"Triple top ~{top_price:.6g}",
                        {"type": "triple_top", "direction": "bearish",
                         "name": "Triple Top", "confirmed": True,
                         "lines": [_seg(candles, i1, p1, i3, p3)],
                         "key_levels": [{"price": top_price, "label": "TT Resistance"}]}))

    if len(lows) >= 3:
        (i1, p1), (i2, p2), (i3, p3) = lows[-3], lows[-2], lows[-1]
        if i2 - i1 >= 4 and i3 - i2 >= 4 and \
                max(p1, p2, p3) - min(p1, p2, p3) < a * 0.9 and price > max(p1, p2, p3):
            bot_price = (p1 + p2 + p3) / 3
            out.append(("triple_bottom", 0.75,
                        f"Triple bottom ~{bot_price:.6g}",
                        {"type": "triple_bottom", "direction": "bullish",
                         "name": "Triple Bottom", "confirmed": True,
                         "lines": [_seg(candles, i1, p1, i3, p3)],
                         "key_levels": [{"price": bot_price, "label": "TB Support"}]}))
    return out


# ---------------------------------------------------------------------------
# Head & Shoulders / Inverse H&S
# ---------------------------------------------------------------------------

def _head_shoulders(candles):
    out = []
    highs, lows = swing_points(candles, lookback=3)
    a = atr(candles) or 1e-9
    price = candles[-1]["close"]
    n = len(candles) - 1

    # H&S (bearish): three peaks, middle (head) highest, shoulders roughly equal
    if len(highs) >= 3:
        for idx in range(max(0, len(highs) - 4), len(highs) - 2):
            (i1, p1), (i2, p2), (i3, p3) = highs[idx], highs[idx + 1], highs[idx + 2]
            if p2 <= max(p1, p3):
                continue                  # middle must be highest
            if abs(p1 - p3) > a * 1.5:
                continue                  # shoulders must be roughly equal
            if i2 - i1 < 4 or i3 - i2 < 4:
                continue
            # Neckline = lows between shoulder-head and head-shoulder
            seg1 = candles[i1:i2 + 1]
            seg2 = candles[i2:i3 + 1]
            if not seg1 or not seg2:
                continue
            ni1 = i1 + min(range(len(seg1)), key=lambda x: seg1[x]["low"])
            ni2 = i2 + min(range(len(seg2)), key=lambda x: seg2[x]["low"])
            nl1, nl2 = candles[ni1]["low"], candles[ni2]["low"]
            neckline = (nl1 + nl2) / 2
            confirmed = price < neckline
            lines = [
                _seg(candles, i1, p1, i2, p2),
                _seg(candles, i2, p2, i3, p3),
                _seg(candles, ni1, nl1, ni2, nl2),
                _seg(candles, ni2, nl2, n, nl2 + (nl2 - nl1) * (n - ni2) / max(ni2 - ni1, 1)),
            ]
            out.append(("head_and_shoulders", -0.80 if confirmed else -0.55,
                        f"Head & Shoulders — head {p2:.6g}, neckline {neckline:.6g}"
                        + (" [CONFIRMED]" if confirmed else " [forming]"),
                        {"type": "head_and_shoulders", "direction": "bearish",
                         "name": "Head & Shoulders", "confirmed": confirmed,
                         "lines": lines,
                         "key_levels": [
                             {"price": neckline, "label": "H&S Neckline"},
                             {"price": p2, "label": "H&S Head"},
                         ]}))
            break   # most recent only

    # Inverse H&S (bullish): three troughs, middle (head) lowest
    if len(lows) >= 3:
        for idx in range(max(0, len(lows) - 4), len(lows) - 2):
            (i1, p1), (i2, p2), (i3, p3) = lows[idx], lows[idx + 1], lows[idx + 2]
            if p2 >= min(p1, p3):
                continue
            if abs(p1 - p3) > a * 1.5:
                continue
            if i2 - i1 < 4 or i3 - i2 < 4:
                continue
            seg1 = candles[i1:i2 + 1]
            seg2 = candles[i2:i3 + 1]
            if not seg1 or not seg2:
                continue
            ni1 = i1 + max(range(len(seg1)), key=lambda x: seg1[x]["high"])
            ni2 = i2 + max(range(len(seg2)), key=lambda x: seg2[x]["high"])
            nh1, nh2 = candles[ni1]["high"], candles[ni2]["high"]
            neckline = (nh1 + nh2) / 2
            confirmed = price > neckline
            lines = [
                _seg(candles, i1, p1, i2, p2),
                _seg(candles, i2, p2, i3, p3),
                _seg(candles, ni1, nh1, ni2, nh2),
                _seg(candles, ni2, nh2, n, nh2 + (nh2 - nh1) * (n - ni2) / max(ni2 - ni1, 1)),
            ]
            out.append(("inv_head_and_shoulders", 0.80 if confirmed else 0.55,
                        f"Inverse H&S — head {p2:.6g}, neckline {neckline:.6g}"
                        + (" [CONFIRMED]" if confirmed else " [forming]"),
                        {"type": "inv_head_and_shoulders", "direction": "bullish",
                         "name": "Inverse H&S", "confirmed": confirmed,
                         "lines": lines,
                         "key_levels": [
                             {"price": neckline, "label": "IH&S Neckline"},
                             {"price": p2, "label": "IH&S Head"},
                         ]}))
            break

    return out


# ---------------------------------------------------------------------------
# Triangles & Wedges
# ---------------------------------------------------------------------------

def _triangles_and_wedges(candles):
    out = []
    if len(candles) < 20:
        return out
    highs, lows = swing_points(candles, lookback=3)
    if len(highs) < 3 or len(lows) < 3:
        return out

    a = atr(candles) or 1e-9
    price = candles[-1]["close"]
    n = len(candles) - 1

    h_pts = highs[-5:]
    l_pts = lows[-5:]
    if len(h_pts) < 2 or len(l_pts) < 2:
        return out

    h_xs = [p[0] for p in h_pts]
    h_ys = [p[1] for p in h_pts]
    l_xs = [p[0] for p in l_pts]
    l_ys = [p[1] for p in l_pts]

    h_slope, h_int = linear_regression(h_xs, h_ys)
    l_slope, l_int = linear_regression(l_xs, l_ys)

    # Quality: residuals must be small
    h_resid = sum(abs(h_ys[i] - (h_slope * h_xs[i] + h_int)) for i in range(len(h_xs))) / len(h_xs)
    l_resid = sum(abs(l_ys[i] - (l_slope * l_xs[i] + l_int)) for i in range(len(l_xs))) / len(l_xs)
    if h_resid > a * 2.0 or l_resid > a * 2.0:
        return out

    i_start = min(h_xs[0], l_xs[0])
    h_now = h_slope * n + h_int
    l_now = l_slope * n + l_int
    h_start_p = h_slope * i_start + h_int
    l_start_p = l_slope * i_start + l_int

    flat = a * 0.0015   # slope threshold for "flat"

    def make_lines():
        return [
            _seg(candles, i_start, h_start_p, n, h_now),
            _seg(candles, i_start, l_start_p, n, l_now),
        ]

    # ASCENDING TRIANGLE: flat top + rising lows
    if abs(h_slope) < flat and l_slope > flat:
        confirmed = price > h_now
        out.append(("ascending_triangle", 0.55 if confirmed else 0.30,
                    f"Ascending triangle — flat resistance {h_now:.6g}, rising support",
                    {"type": "ascending_triangle", "direction": "bullish",
                     "name": "Ascending Triangle", "confirmed": confirmed,
                     "lines": make_lines(),
                     "key_levels": [{"price": h_now, "label": "AT Resistance"}]}))

    # DESCENDING TRIANGLE: flat bottom + falling highs
    elif abs(l_slope) < flat and h_slope < -flat:
        confirmed = price < l_now
        out.append(("descending_triangle", -0.55 if confirmed else -0.30,
                    f"Descending triangle — flat support {l_now:.6g}, falling resistance",
                    {"type": "descending_triangle", "direction": "bearish",
                     "name": "Descending Triangle", "confirmed": confirmed,
                     "lines": make_lines(),
                     "key_levels": [{"price": l_now, "label": "DT Support"}]}))

    # SYMMETRICAL TRIANGLE: highs falling + lows rising
    elif h_slope < -flat and l_slope > flat:
        denom = l_slope - h_slope
        apex_i = ((h_int - l_int) / denom) if denom != 0 else n + 20
        if price > h_now:
            dir_str, sc = "bullish", 0.45
        elif price < l_now:
            dir_str, sc = "bearish", -0.45
        else:
            dir_str, sc = "neutral", 0.0
        out.append(("symmetrical_triangle", sc,
                    f"Symmetrical triangle — apex ~candle {int(apex_i)}, breakout {'up' if sc > 0 else ('down' if sc < 0 else 'pending')}",
                    {"type": "symmetrical_triangle", "direction": dir_str,
                     "name": "Sym. Triangle", "confirmed": sc != 0,
                     "lines": make_lines(),
                     "key_levels": [{"price": h_now, "label": "ST Resistance"},
                                    {"price": l_now, "label": "ST Support"}]}))

    # RISING WEDGE (bearish): both rising, highs flatter than lows → converging up
    elif h_slope > flat and l_slope > flat and l_slope > h_slope:
        out.append(("rising_wedge", -0.55,
                    "Rising wedge (both S/R rising, converging) — bearish reversal risk",
                    {"type": "rising_wedge", "direction": "bearish",
                     "name": "Rising Wedge", "confirmed": False,
                     "lines": make_lines(),
                     "key_levels": [{"price": l_now, "label": "RW Support"}]}))

    # FALLING WEDGE (bullish): both falling, lows flatter than highs → converging down
    elif h_slope < -flat and l_slope < -flat and h_slope < l_slope:
        out.append(("falling_wedge", 0.55,
                    "Falling wedge (both S/R falling, converging) — bullish reversal setup",
                    {"type": "falling_wedge", "direction": "bullish",
                     "name": "Falling Wedge", "confirmed": False,
                     "lines": make_lines(),
                     "key_levels": [{"price": h_now, "label": "FW Resistance"}]}))

    return out


# ---------------------------------------------------------------------------
# Flags (Bull & Bear)
# ---------------------------------------------------------------------------

def _flags(candles):
    out = []
    if len(candles) < 15:
        return out
    a = atr(candles) or 1e-9
    n = len(candles) - 1

    for flag_len in [6, 8, 10]:
        if len(candles) < flag_len * 2 + 2:
            continue
        impulse = candles[-(flag_len * 2):-(flag_len)]
        consol  = candles[-flag_len:]
        if not impulse or not consol:
            continue

        impulse_change = impulse[-1]["close"] - impulse[0]["open"]
        consol_range   = max(c["high"] for c in consol) - min(c["low"] for c in consol)
        xs = list(range(len(consol)))
        ys = [c["close"] for c in consol]
        slope, intercept = linear_regression(xs, ys)

        fi_start = n - flag_len
        flag_h = max(c["high"] for c in consol)
        flag_l = min(c["low"] for c in consol)

        # BULL FLAG
        if impulse_change > a * 1.5 and consol_range < a * 2.0 and slope <= a * 0.05:
            pole_bot = min(c["low"] for c in impulse)
            pole_top = max(c["high"] for c in impulse)
            pi_start = n - flag_len * 2
            lines = [
                _seg(candles, pi_start, pole_bot, fi_start, pole_top),
                _seg(candles, fi_start, flag_h, n, flag_h + slope * (flag_len - 1)),
                _seg(candles, fi_start, flag_l, n, flag_l + slope * (flag_len - 1)),
            ]
            out.append(("bull_flag", 0.50,
                        f"Bull flag — {abs(impulse_change)/a:.1f}x ATR pole, tight consolidation",
                        {"type": "bull_flag", "direction": "bullish",
                         "name": "Bull Flag", "confirmed": False,
                         "lines": lines,
                         "key_levels": [{"price": pole_top, "label": "Flag Pole Top"}]}))
            break

        # BEAR FLAG
        elif impulse_change < -a * 1.5 and consol_range < a * 2.0 and slope >= -a * 0.05:
            pole_top = max(c["high"] for c in impulse)
            pole_bot = min(c["low"] for c in impulse)
            pi_start = n - flag_len * 2
            lines = [
                _seg(candles, pi_start, pole_top, fi_start, pole_bot),
                _seg(candles, fi_start, flag_h, n, flag_h + slope * (flag_len - 1)),
                _seg(candles, fi_start, flag_l, n, flag_l + slope * (flag_len - 1)),
            ]
            out.append(("bear_flag", -0.50,
                        f"Bear flag — {abs(impulse_change)/a:.1f}x ATR pole, tight consolidation",
                        {"type": "bear_flag", "direction": "bearish",
                         "name": "Bear Flag", "confirmed": False,
                         "lines": lines,
                         "key_levels": [{"price": pole_bot, "label": "Flag Pole Bot"}]}))
            break

    return out


# ---------------------------------------------------------------------------
# Cup & Handle
# ---------------------------------------------------------------------------

def _cup_and_handle(candles):
    out = []
    if len(candles) < 45:
        return out
    a = atr(candles) or 1e-9
    n = len(candles) - 1
    price = candles[-1]["close"]

    cup    = candles[-40:-8]
    handle = candles[-12:]
    if not cup:
        return out

    cup_left   = cup[0]["high"]
    cup_right  = cup[-1]["high"]
    cup_bottom = min(c["low"] for c in cup)
    cup_depth  = min(cup_left, cup_right) - cup_bottom

    if cup_depth < a * 3:
        return out
    if abs(cup_left - cup_right) > a * 2.5:
        return out

    # Verify U-shape: bottom should be in middle half of cup
    cup_bot_idx = min(range(len(cup)), key=lambda i: cup[i]["low"])
    if not (len(cup) // 4 <= cup_bot_idx <= 3 * len(cup) // 4):
        return out

    handle_depth = max(c["high"] for c in handle) - min(c["low"] for c in handle)
    if handle_depth > cup_depth * 0.55:
        return out

    rim_level = (cup_left + cup_right) / 2
    confirmed = price > rim_level
    cup_start_i = n - 40

    lines = [
        _seg(candles, cup_start_i, cup_left,
             cup_start_i + cup_bot_idx, cup_bottom),
        _seg(candles, cup_start_i + cup_bot_idx, cup_bottom,
             cup_start_i + len(cup) - 1, cup_right),
        _seg(candles, 0, rim_level, n, rim_level),
    ]
    out.append(("cup_and_handle", 0.65 if confirmed else 0.40,
                f"Cup & Handle — rim {rim_level:.6g}, depth {cup_depth/a:.1f}x ATR"
                + (" [CONFIRMED]" if confirmed else ""),
                {"type": "cup_and_handle", "direction": "bullish",
                 "name": "Cup & Handle", "confirmed": confirmed,
                 "lines": lines,
                 "key_levels": [{"price": rim_level, "label": "C&H Rim"},
                                {"price": cup_bottom, "label": "Cup Bottom"}]}))
    return out


# ---------------------------------------------------------------------------
# Broadening Formation (Megaphone)
# ---------------------------------------------------------------------------

def _broadening_formation(candles):
    out = []
    if len(candles) < 20:
        return out
    highs, lows = swing_points(candles, lookback=3)
    if len(highs) < 3 or len(lows) < 3:
        return out
    a = atr(candles) or 1e-9
    n = len(candles) - 1

    h_pts = highs[-4:]
    l_pts = lows[-4:]
    if len(h_pts) < 2 or len(l_pts) < 2:
        return out

    h_xs = [p[0] for p in h_pts]
    h_ys = [p[1] for p in h_pts]
    l_xs = [p[0] for p in l_pts]
    l_ys = [p[1] for p in l_pts]

    h_slope, h_int = linear_regression(h_xs, h_ys)
    l_slope, l_int = linear_regression(l_xs, l_ys)

    flat = a * 0.0015
    # Megaphone: highs expanding (positive slope) AND lows expanding (negative slope)
    if h_slope > flat and l_slope < -flat:
        i_start = min(h_xs[0], l_xs[0])
        lines = [
            _seg(candles, i_start, h_slope * i_start + h_int, n, h_slope * n + h_int),
            _seg(candles, i_start, l_slope * i_start + l_int, n, l_slope * n + l_int),
        ]
        h_end = h_slope * n + h_int
        l_end = l_slope * n + l_int
        out.append(("broadening_formation", -0.20,
                    "Broadening formation (megaphone) — expanding volatility, caution",
                    {"type": "broadening_formation", "direction": "neutral",
                     "name": "Broadening Formation", "confirmed": True,
                     "lines": lines,
                     "key_levels": [{"price": h_end, "label": "Megaphone Top"},
                                    {"price": l_end, "label": "Megaphone Bot"}]}))
    return out


# ---------------------------------------------------------------------------
# Main analyze function
# ---------------------------------------------------------------------------

def analyze(candles):
    if len(candles) < 10:
        return {"score": 0.0,
                "reasons": ["Insufficient candles for pattern analysis"],
                "overlays": {"chart_patterns": []}}

    all_patterns = (
        _double_top_bottom(candles)
        + _triple_top_bottom(candles)
        + _head_shoulders(candles)
        + _triangles_and_wedges(candles)
        + _flags(candles)
        + _cup_and_handle(candles)
        + _broadening_formation(candles)
    )

    # each element: (name, score, reason, overlay_dict)
    score = clamp(sum(s for _, s, _, _ in all_patterns))
    reasons = [msg for _, _, msg, _ in all_patterns] or ["No chart patterns detected"]
    chart_patterns = [ov for _, _, _, ov in all_patterns]

    return {
        "score": score,
        "reasons": reasons,
        "overlays": {"chart_patterns": chart_patterns},
    }
