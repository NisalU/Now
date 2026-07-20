"""Strategy generation using live market data — no AI, no external APIs.

Two modes available:
  generate_from_market_data(candles) — deep analysis of live candles:
      Evaluates every condition in the catalogue against the current candle
      window, scores them by consistency over the last N bars, and builds the
      best-matching strategy definition.

  generate_from_signals(candles) — learn from past strong engine signals:
      Walks back through signals.json, finds which conditions fired before
      correct-direction moves, ranks by precision, and returns the best combo.

Both functions return a strategy dict compatible with strategy_store and the
custom evaluator (strategies/custom.py).
"""
from __future__ import annotations

import json
import math
import os
import time
import threading
from typing import Optional


# ── keep a no-op seconds_until_ready so server.py import doesn't break ───────
def seconds_until_ready() -> float:
    return 0.0

GEN_COOLDOWN = 0  # no AI cooldown


# ── condition catalogue (must match strategies/custom.py) ────────────────────
CONDITION_CATALOGUE = [
    {"type": "price_above_ema",    "params": {"period": 25}},
    {"type": "price_below_ema",    "params": {"period": 25}},
    {"type": "ema_cross_above",    "params": {"fast": 7, "slow": 25}},
    {"type": "ema_cross_below",    "params": {"fast": 7, "slow": 25}},
    {"type": "rsi_above",          "params": {"threshold": 55, "period": 14}},
    {"type": "rsi_below",          "params": {"threshold": 45, "period": 14}},
    {"type": "volume_spike",       "params": {"multiplier": 1.5}},
    {"type": "candle_bullish",     "params": {"body_pct": 0.5}},
    {"type": "candle_bearish",     "params": {"body_pct": 0.5}},
    {"type": "delta_positive",     "params": {"n_candles": 3}},
    {"type": "delta_negative",     "params": {"n_candles": 3}},
    {"type": "price_change_above", "params": {"pct": 1.0, "n_candles": 5}},
    {"type": "price_change_below", "params": {"pct": 1.0, "n_candles": 5}},
    {"type": "atr_expansion",      "params": {"multiplier": 1.3}},
    {"type": "cvd_rising",         "params": {"n_candles": 5}},
    {"type": "cvd_falling",        "params": {"n_candles": 5}},
    {"type": "near_support",       "params": {"atr_mult": 0.5}},
    {"type": "near_resistance",    "params": {"atr_mult": 0.5}},
]

BULLISH_CONDS = {
    "price_above_ema", "ema_cross_above", "rsi_above", "volume_spike",
    "candle_bullish", "delta_positive", "price_change_above",
    "atr_expansion", "cvd_rising", "near_support",
}
BEARISH_CONDS = {
    "price_below_ema", "ema_cross_below", "rsi_below", "volume_spike",
    "candle_bearish", "delta_negative", "price_change_below",
    "atr_expansion", "cvd_falling", "near_resistance",
}

COND_LABELS = {
    "price_above_ema":    "Price above EMA {period}",
    "price_below_ema":    "Price below EMA {period}",
    "ema_cross_above":    "EMA {fast} crosses above EMA {slow}",
    "ema_cross_below":    "EMA {fast} crosses below EMA {slow}",
    "rsi_above":          "RSI({period}) > {threshold}",
    "rsi_below":          "RSI({period}) < {threshold}",
    "volume_spike":       "Volume spike ×{multiplier}",
    "candle_bullish":     "Bullish candle body >{body_pct}",
    "candle_bearish":     "Bearish candle body >{body_pct}",
    "delta_positive":     "Delta net positive ({n_candles}c)",
    "delta_negative":     "Delta net negative ({n_candles}c)",
    "price_change_above": "Price +{pct}% in {n_candles}c",
    "price_change_below": "Price -{pct}% in {n_candles}c",
    "atr_expansion":      "ATR expansion ×{multiplier}",
    "cvd_rising":         "CVD rising ({n_candles}c)",
    "cvd_falling":        "CVD falling ({n_candles}c)",
    "near_support":       "Price near support (≤{atr_mult}×ATR)",
    "near_resistance":    "Price near resistance (≤{atr_mult}×ATR)",
}


def _make_label(cond: dict) -> str:
    t   = cond.get("type", "")
    p   = cond.get("params", {})
    tpl = COND_LABELS.get(t, t)
    try:
        return tpl.format(**p)
    except (KeyError, ValueError):
        return t


# ── lightweight indicator helpers ─────────────────────────────────────────────

def _ema(closes: list[float], period: int) -> list[float]:
    if not closes or period < 1:
        return []
    k = 2.0 / (period + 1)
    out = [closes[0]]
    for v in closes[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1 + ag / al)


def _atr(candles: list[dict], period: int = 14) -> float:
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(
            c["high"] - c["low"],
            abs(c["high"] - p["close"]),
            abs(c["low"]  - p["close"]),
        ))
    if not trs:
        return candles[-1]["close"] * 0.005
    recent = trs[-period:] if len(trs) >= period else trs
    return sum(recent) / len(recent)


def _cvd(candles: list[dict], n: int) -> float:
    """Cumulative volume delta over last n candles."""
    return sum(c.get("delta", 0.0) for c in candles[-n:])


def _market_snapshot(candles: list[dict]) -> dict:
    """Compute a compact market snapshot for the description string."""
    closes  = [c["close"] for c in candles]
    price   = closes[-1]
    ema7    = _ema(closes, 7)[-1]  if len(closes) >= 7  else price
    ema25   = _ema(closes, 25)[-1] if len(closes) >= 25 else price
    ema99   = _ema(closes, 99)[-1] if len(closes) >= 99 else price
    rsi     = _rsi(closes, 14)
    atr_val = _atr(candles, 14)
    cvd5    = _cvd(candles, 5)
    vol_avg = sum(c["volume"] for c in candles[-20:]) / 20 if len(candles) >= 20 else 0
    vol_now = candles[-1]["volume"]
    vol_ratio = vol_now / vol_avg if vol_avg else 1.0
    chg5  = (price / closes[-6] - 1) * 100 if len(closes) >= 6 else 0.0
    chg20 = (price / closes[-21] - 1) * 100 if len(closes) >= 21 else 0.0
    return {
        "price": price, "ema7": ema7, "ema25": ema25, "ema99": ema99,
        "rsi": rsi, "atr": atr_val, "cvd5": cvd5,
        "vol_ratio": vol_ratio, "chg5": chg5, "chg20": chg20,
        "above_ema25": price > ema25, "above_ema99": price > ema99,
        "ema7_above_ema25": ema7 > ema25,
    }


# ── rolling window evaluator ──────────────────────────────────────────────────

def _rolling_fire_rate(cond: dict, candles: list[dict], window: int = 30) -> float:
    """Fraction of the last `window` candles where this condition fires.

    For each step i we look at candles[:i+1] (the full history up to that bar)
    so the condition sees realistic data.
    """
    from strategies.custom import _eval_condition  # local import
    n     = len(candles)
    start = max(20, n - window)
    fires = 0
    total = 0
    for i in range(start, n):
        window_data = candles[:i + 1]
        try:
            if _eval_condition(cond, window_data):
                fires += 1
        except Exception:
            pass
        total += 1
    return fires / total if total else 0.0


def _forward_return(candles: list[dict], idx: int, horizon: int = 3) -> float:
    """Percentage price change from candle[idx] to candle[idx+horizon]."""
    end = min(idx + horizon, len(candles) - 1)
    if end <= idx:
        return 0.0
    return (candles[end]["close"] / candles[idx]["close"] - 1) * 100


# ── main: generate from market data ──────────────────────────────────────────

def generate_from_market_data(candles: list[dict]) -> dict:
    """Analyse live candle data and return the best strategy definition.

    Algorithm:
    1.  Compute a market snapshot (trend, momentum, volume, CVD).
    2.  Determine bias: bullish / bearish based on EMA stack, RSI, CVD.
    3.  For each candidate condition aligned with that bias, measure:
         a. Rolling fire rate over last 30 bars
         b. Precision: fire rate on bars where forward return confirmed direction
    4.  Pick top 2–3 conditions by precision × fire-rate score.
    5.  Return strategy dict.
    """
    from strategies.custom import _eval_condition  # local import

    if len(candles) < 30:
        return _minimal_fallback(candles)

    snap = _market_snapshot(candles)

    # Determine bias from multiple signals
    bull_votes = 0
    bear_votes = 0
    if snap["above_ema25"]:        bull_votes += 1
    else:                          bear_votes += 1
    if snap["above_ema99"]:        bull_votes += 1
    else:                          bear_votes += 1
    if snap["ema7_above_ema25"]:   bull_votes += 1
    else:                          bear_votes += 1
    if snap["rsi"] is not None:
        if snap["rsi"] > 52:       bull_votes += 1
        elif snap["rsi"] < 48:     bear_votes += 1
    if snap["cvd5"] > 0:           bull_votes += 1
    elif snap["cvd5"] < 0:         bear_votes += 1
    if snap["chg5"] > 0.5:         bull_votes += 1
    elif snap["chg5"] < -0.5:      bear_votes += 1

    is_bullish  = bull_votes >= bear_votes
    direction   = "bullish" if is_bullish else "bearish"
    pool        = BULLISH_CONDS if is_bullish else BEARISH_CONDS

    # Score each condition in the aligned pool
    scored: list[tuple[str, float, float]] = []  # (type, precision, fire_rate)
    horizon = 3

    for cat_cond in CONDITION_CATALOGUE:
        ct = cat_cond["type"]
        if ct not in pool:
            continue
        # Fire points: indices where condition fired on candles[:i+1]
        fire_indices = []
        n = len(candles)
        for i in range(20, n - horizon):
            try:
                if _eval_condition(cat_cond, candles[:i + 1]):
                    fire_indices.append(i)
            except Exception:
                pass

        if len(fire_indices) < 3:
            continue

        fire_rate = len(fire_indices) / max(1, n - 20 - horizon)

        # Precision: fraction of fires where forward return confirmed direction
        confirmed = 0
        for idx in fire_indices:
            fwd = _forward_return(candles, idx, horizon)
            if is_bullish and fwd > 0.1:
                confirmed += 1
            elif not is_bullish and fwd < -0.1:
                confirmed += 1
        precision = confirmed / len(fire_indices)

        # Combined score: reward precision, penalise conditions that never fire
        combo_score = precision * min(1.0, fire_rate * 5)
        scored.append((ct, precision, fire_rate, combo_score))

    if not scored:
        return _minimal_fallback(candles)

    scored.sort(key=lambda x: -x[3])
    top = scored[:3]

    # Build condition objects
    conditions = []
    for ct, prec, fr, _ in top:
        tmpl = next((c for c in CONDITION_CATALOGUE if c["type"] == ct), None)
        if tmpl:
            cond = dict(tmpl)
            cond["label"] = _make_label(cond)
            conditions.append(cond)

    if not conditions:
        return _minimal_fallback(candles)

    rsi_str  = f"RSI {snap['rsi']:.0f}" if snap["rsi"] is not None else "RSI n/a"
    cvd_str  = f"CVD {'↑' if snap['cvd5'] > 0 else '↓'}{abs(snap['cvd5']):.0f}"
    chg_str  = f"5c chg {snap['chg5']:+.2f}%"
    vol_str  = f"vol ×{snap['vol_ratio']:.1f}"
    prec_pct = top[0][1] * 100

    desc = (
        f"Market-data strategy for {'bullish' if is_bullish else 'bearish'} bias. "
        f"Snapshot: {rsi_str}, {cvd_str}, {chg_str}, {vol_str}. "
        f"Top condition precision: {prec_pct:.0f}% over {len(candles)} candles."
    )

    logic = "AND" if len(conditions) >= 2 else "OR"
    name  = f"MktData — {'Bull' if is_bullish else 'Bear'} ({len(conditions)}c {logic})"

    return {
        "name":             name,
        "description":      desc,
        "signal_direction": direction,
        "logic":            logic,
        "weight":           10,
        "conditions":       conditions,
        "source":           "market_data",
        "precision":        round(top[0][1], 3),
        "market_snapshot":  {
            "rsi":       round(snap["rsi"], 1) if snap["rsi"] is not None else None,
            "cvd5":      round(snap["cvd5"], 2),
            "chg5":      round(snap["chg5"], 3),
            "vol_ratio": round(snap["vol_ratio"], 2),
            "bias_votes": {"bull": bull_votes, "bear": bear_votes},
        },
    }


# ── generate from historical signals ─────────────────────────────────────────

def generate_from_signals(candles: list[dict],
                          signals_path: str | None = None) -> dict:
    """Discover effective condition combos from past strong engine signals.

    1. Load signals.json for recent LONG/SHORT signals with score > 35.
    2. For each signal, walk back through candles to the signal timestamp and
       evaluate every catalogue condition.
    3. Rank conditions by precision (fires on correct-direction bars).
    4. Return best combo, or fall back to generate_from_market_data().
    """
    if signals_path is None:
        signals_path = os.path.join(os.path.dirname(__file__), "signals.json")

    try:
        with open(signals_path) as fh:
            raw_signals = json.load(fh)
    except Exception:
        raw_signals = []

    strong = [s for s in raw_signals
              if abs(s.get("score", 0)) >= 35 and s.get("direction") in ("LONG", "SHORT")]

    if not strong:
        # No history yet — fall back to live-candle analysis
        return generate_from_market_data(candles)

    from strategies.custom import _eval_condition

    cond_scores: dict[str, dict] = {}

    for sig in strong[-30:]:
        sig_dir  = sig["direction"]
        sig_time = sig.get("time", 0)

        # Find the candle index closest to signal timestamp
        closest_idx = len(candles) - 1
        for i, c in enumerate(candles):
            if c.get("time", 0) >= sig_time:
                closest_idx = max(0, i - 1)
                break

        window = candles[:closest_idx + 1]
        if len(window) < 20:
            continue

        for cat_cond in CONDITION_CATALOGUE:
            ct = cat_cond["type"]
            try:
                fired = _eval_condition(cat_cond, window)
            except Exception:
                fired = False

            if ct not in cond_scores:
                cond_scores[ct] = {"bull": 0, "bear": 0, "total": 0}
            cond_scores[ct]["total"] += 1
            if fired:
                if sig_dir == "LONG":
                    cond_scores[ct]["bull"] += 1
                else:
                    cond_scores[ct]["bear"] += 1

    if not cond_scores:
        return generate_from_market_data(candles)

    bull_scored: list[tuple[str, float]] = []
    bear_scored: list[tuple[str, float]] = []

    for ct, sc in cond_scores.items():
        if sc["total"] < 3:
            continue
        bull_prec = sc["bull"] / sc["total"]
        bear_prec = sc["bear"] / sc["total"]
        if ct in BULLISH_CONDS:
            bull_scored.append((ct, bull_prec))
        if ct in BEARISH_CONDS:
            bear_scored.append((ct, bear_prec))

    bull_scored.sort(key=lambda x: -x[1])
    bear_scored.sort(key=lambda x: -x[1])

    top_bull = bull_scored[0][1] if bull_scored else 0.0
    top_bear = bear_scored[0][1] if bear_scored else 0.0

    if top_bull >= top_bear and bull_scored:
        direction     = "bullish"
        top_conds_raw = bull_scored[:3]
    elif bear_scored:
        direction     = "bearish"
        top_conds_raw = bear_scored[:3]
    else:
        return generate_from_market_data(candles)

    conditions = []
    for ct, prec in top_conds_raw:
        tmpl = next((c for c in CONDITION_CATALOGUE if c["type"] == ct), None)
        if tmpl:
            cond = dict(tmpl)
            cond["label"] = _make_label(cond)
            conditions.append(cond)

    if not conditions:
        return generate_from_market_data(candles)

    top_prec = top_conds_raw[0][1]
    desc = (
        f"Learnt from {len(strong)} strong signals (score ≥ 35). "
        f"Top condition precision: {top_prec*100:.0f}%. "
        f"{len(conditions)} conditions, {('AND' if len(conditions) >= 2 else 'OR')} logic."
    )

    return {
        "name":             f"Signal-Learner — {'Bull' if direction == 'bullish' else 'Bear'} ({len(strong)} signals)",
        "description":      desc,
        "signal_direction": direction,
        "logic":            "AND" if len(conditions) >= 2 else "OR",
        "weight":           10,
        "conditions":       conditions,
        "source":           "rule_learner",
        "precision":        round(top_prec, 3),
    }


def _minimal_fallback(candles: list[dict]) -> dict:
    """Absolute fallback when data is too sparse for scoring."""
    closes  = [c["close"] for c in candles]
    cum_d   = sum(c.get("delta", 0.0) for c in candles[-20:])
    k       = 2.0 / 26
    ema     = closes[0]
    for p in closes[1:]:
        ema = p * k + ema * (1 - k)
    is_bull = closes[-1] > ema and cum_d >= 0

    if is_bull:
        direction  = "bullish"
        conditions = [
            {"type": "price_above_ema", "params": {"period": 25},
             "label": "Price above EMA 25"},
            {"type": "delta_positive",  "params": {"n_candles": 3},
             "label": "Delta net positive (3c)"},
        ]
    else:
        direction  = "bearish"
        conditions = [
            {"type": "price_below_ema", "params": {"period": 25},
             "label": "Price below EMA 25"},
            {"type": "delta_negative",  "params": {"n_candles": 3},
             "label": "Delta net negative (3c)"},
        ]

    return {
        "name":             f"Snapshot — {'Bull' if is_bull else 'Bear'} Base",
        "description":      "Generated from current market snapshot (insufficient history for scoring).",
        "signal_direction": direction,
        "logic":            "AND",
        "weight":           8,
        "conditions":       conditions,
        "source":           "market_data",
        "precision":        None,
    }


# ── stub kept for server.py import compatibility ──────────────────────────────

def generate_from_ai(candles: list[dict], symbol: str = "?",
                     engine_breakdown: list | None = None) -> dict:
    """AI generation is disabled — returns market-data strategy instead."""
    return generate_from_market_data(candles)
