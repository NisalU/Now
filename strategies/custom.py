"""Custom strategy evaluator.

Runs user-defined strategy definitions (stored as JSON) against live candle
data. Each strategy is a list of conditions connected by AND / OR logic.

Supported condition types
─────────────────────────
price_above_ema     params: period (7|25|99)
price_below_ema     params: period (7|25|99)
ema_cross_above     params: fast (7|25), slow (25|99)
ema_cross_below     params: fast (7|25), slow (25|99)
rsi_above           params: period (default 14), threshold (0-100)
rsi_below           params: period (default 14), threshold (0-100)
volume_spike        params: multiplier (default 1.5)
candle_bullish      params: body_pct (default 0.5)   close > open + body_pct*range
candle_bearish      params: body_pct (default 0.5)
delta_positive      params: n_candles (default 3)    net delta sum > 0
delta_negative      params: n_candles (default 3)
price_change_above  params: pct, n_candles            (close[-1]/close[-n]-1)*100 > pct
price_change_below  params: pct, n_candles
atr_expansion       params: multiplier (default 1.3)  current ATR > avg_ATR * mult
cvd_rising          params: n_candles (default 5)     cum-delta trend slope > 0
cvd_falling         params: n_candles (default 5)
near_support        params: atr_mult (default 0.5)    price within atr_mult*ATR of nearest S level
near_resistance     params: atr_mult (default 0.5)
"""
from __future__ import annotations
import math
from .helpers import atr as _atr_fn


# ── indicator helpers ─────────────────────────────────────────────────────────

def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    # Wilder smoothing over last `period` bars
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def _atr_series(candles: list[dict], period: int = 14) -> list[float]:
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(
            c["high"] - c["low"],
            abs(c["high"] - p["close"]),
            abs(c["low"]  - p["close"]),
        ))
    if not trs:
        return []
    result = [sum(trs[:period]) / period] if len(trs) >= period else [trs[0]]
    k = 1 / period
    for tr in trs[period:]:
        result.append(tr * k + result[-1] * (1 - k))
    return result


def _support_resistance(candles: list[dict], n: int = 40, bins: int = 10
                        ) -> tuple[list[float], list[float]]:
    """Very fast approximate S/R via price-cluster pivot counting."""
    subset = candles[-n:]
    highs = sorted(set(round(c["high"], 4) for c in subset))
    lows  = sorted(set(round(c["low"],  4) for c in subset))
    # Simple: top 3 highs = resistance, bottom 3 lows = support
    supports    = sorted(lows[:3])
    resistances = sorted(highs[-3:], reverse=True)
    return supports, resistances


# ── condition evaluators ──────────────────────────────────────────────────────

def _eval_condition(cond: dict, candles: list[dict]) -> bool:
    t      = cond.get("type", "")
    p      = cond.get("params", {})
    closes = [c["close"] for c in candles]
    n      = len(candles)

    # ── EMA conditions ────────────────────────────────────────────────────────
    if t in ("price_above_ema", "price_below_ema"):
        period = int(p.get("period", 25))
        if n < period:
            return False
        ema_vals = _ema(closes, period)
        price    = closes[-1]
        ema_now  = ema_vals[-1]
        return price > ema_now if t == "price_above_ema" else price < ema_now

    if t in ("ema_cross_above", "ema_cross_below"):
        fast = int(p.get("fast", 7))
        slow = int(p.get("slow", 25))
        if n < slow + 2:
            return False
        fast_vals = _ema(closes, fast)
        slow_vals = _ema(closes, slow)
        if t == "ema_cross_above":
            return fast_vals[-1] > slow_vals[-1] and fast_vals[-2] <= slow_vals[-2]
        return fast_vals[-1] < slow_vals[-1] and fast_vals[-2] >= slow_vals[-2]

    # ── RSI ───────────────────────────────────────────────────────────────────
    if t in ("rsi_above", "rsi_below"):
        period    = int(p.get("period", 14))
        threshold = float(p.get("threshold", 50))
        rsi_val   = _rsi(closes[-period * 3:], period)
        if rsi_val is None:
            return False
        return rsi_val > threshold if t == "rsi_above" else rsi_val < threshold

    # ── Volume spike ──────────────────────────────────────────────────────────
    if t == "volume_spike":
        mult = float(p.get("multiplier", 1.5))
        vols = [c.get("volume", 0) for c in candles[-30:]]
        if len(vols) < 5:
            return False
        avg_vol = sum(vols[:-1]) / len(vols[:-1])
        return vols[-1] > avg_vol * mult

    # ── Candle body ───────────────────────────────────────────────────────────
    if t in ("candle_bullish", "candle_bearish"):
        body_pct = float(p.get("body_pct", 0.5))
        c   = candles[-1]
        rng = c["high"] - c["low"]
        if rng < 1e-12:
            return False
        body = abs(c["close"] - c["open"]) / rng
        if body < body_pct:
            return False
        if t == "candle_bullish":
            return c["close"] > c["open"]
        return c["close"] < c["open"]

    # ── Delta ─────────────────────────────────────────────────────────────────
    if t in ("delta_positive", "delta_negative"):
        nc   = int(p.get("n_candles", 3))
        net  = sum(c.get("delta", 0) for c in candles[-nc:])
        return net > 0 if t == "delta_positive" else net < 0

    # ── Price change % ────────────────────────────────────────────────────────
    if t in ("price_change_above", "price_change_below"):
        nc  = int(p.get("n_candles", 5))
        pct = float(p.get("pct", 1.0))
        if len(closes) < nc + 1:
            return False
        change = (closes[-1] / closes[-nc - 1] - 1) * 100
        return change > pct if t == "price_change_above" else change < pct

    # ── ATR expansion ─────────────────────────────────────────────────────────
    if t == "atr_expansion":
        mult = float(p.get("multiplier", 1.3))
        atr_series = _atr_series(candles[-30:])
        if len(atr_series) < 5:
            return False
        avg = sum(atr_series[:-1]) / len(atr_series[:-1])
        return atr_series[-1] > avg * mult

    # ── CVD trend ─────────────────────────────────────────────────────────────
    if t in ("cvd_rising", "cvd_falling"):
        nc = int(p.get("n_candles", 5))
        window = candles[-nc:]
        if len(window) < 3:
            return False
        # Linear slope of cumulative delta
        cum, vals = 0.0, []
        for c in window:
            cum += c.get("delta", 0)
            vals.append(cum)
        n_w  = len(vals)
        x_m  = (n_w - 1) / 2
        y_m  = sum(vals) / n_w
        num  = sum((i - x_m) * (vals[i] - y_m) for i in range(n_w))
        slope = num  # sign is enough
        return slope > 0 if t == "cvd_rising" else slope < 0

    # ── Near S/R ─────────────────────────────────────────────────────────────
    if t in ("near_support", "near_resistance"):
        mult  = float(p.get("atr_mult", 0.5))
        price = candles[-1]["close"]
        atr   = _atr_fn(candles) or price * 0.005
        supports, resistances = _support_resistance(candles)
        levels = supports if t == "near_support" else resistances
        if not levels:
            return False
        nearest = min(levels, key=lambda lv: abs(lv - price))
        return abs(price - nearest) <= atr * mult

    # Unknown condition type → neutral (don't fire)
    return False


# ── strategy evaluator ────────────────────────────────────────────────────────

def evaluate(strategy: dict, candles: list[dict]) -> dict:
    """Run a custom strategy definition against candles.

    Returns a standard strategy result dict compatible with the engine:
      { score, reasons, overlays }
    """
    conditions  = strategy.get("conditions", [])
    logic       = strategy.get("logic", "AND").upper()    # "AND" | "OR"
    direction   = strategy.get("signal_direction", "bullish")  # "bullish" | "bearish"
    name        = strategy.get("name", "Custom")

    if not conditions or len(candles) < 20:
        return {"score": 0.0, "reasons": [], "overlays": {}}

    results = [(cond, _eval_condition(cond, candles)) for cond in conditions]

    if logic == "AND":
        triggered = all(r for _, r in results)
    else:  # OR
        triggered = any(r for _, r in results)

    if not triggered:
        return {"score": 0.0, "reasons": [], "overlays": {}}

    score    = 1.0 if direction == "bullish" else -1.0
    met_ids  = [c.get("label") or c.get("type", "?") for c, r in results if r]
    all_ids  = [c.get("label") or c.get("type", "?") for c, r in results]

    reasons = [
        f"[{name}] {logic} conditions met ({', '.join(met_ids)}) — "
        f"{'bullish' if score > 0 else 'bearish'} signal"
    ]
    if logic == "AND" and len(all_ids) > 1:
        reasons.append(f"[{name}] All {len(all_ids)} conditions confirmed")

    return {
        "score":    score,
        "reasons":  reasons,
        "overlays": {},
    }
