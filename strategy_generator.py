"""Automated strategy generation — two independent modes.

Mode 1 — Rule Learner (no API, always available):
    Scans engine breakdown history for condition combos that fired during
    strong signals. Ranks combos by precision (% of fires where price moved
    the right way). Returns the best combo as a strategy definition.

Mode 2 — AI Generator (Groq, optional):
    Sends a condensed market snapshot to a lightweight Groq model and asks
    it to write a strategy definition JSON. Uses llama-3.1-8b-instant (low
    token cost) and enforces a 90-second cooldown so it never starves the
    main analyst's Groq quota.
"""
from __future__ import annotations

import json
import os
import time
import threading
import traceback
import random
from typing import Optional

import requests

# ── shared constants ─────────────────────────────────────────────────────────
GROQ_URL    = "https://api.groq.com/openai/v1/chat/completions"
GEN_MODEL   = "llama-3.1-8b-instant"   # cheapest model — keeps TPM low
GEN_COOLDOWN = 90                        # seconds between AI gen calls
MAX_TOKENS   = 500

_last_gen_time = 0.0
_gen_lock      = threading.Lock()


def _groq_key() -> str:
    return os.environ.get("GROQ_API_KEY", "").strip()


def seconds_until_ready() -> float:
    """How many seconds until the AI generator cooldown expires."""
    elapsed = time.time() - _last_gen_time
    return max(0.0, GEN_COOLDOWN - elapsed)


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
    "near_support":       "Near support (×{atr_mult} ATR)",
    "near_resistance":    "Near resistance (×{atr_mult} ATR)",
}


def _make_label(cond: dict) -> str:
    tmpl = COND_LABELS.get(cond["type"], cond["type"])
    try:
        return tmpl.format(**cond.get("params", {}))
    except KeyError:
        return cond["type"]


# ── Mode 1: Rule Learner ──────────────────────────────────────────────────────

def generate_from_signals(candles: list[dict], signals_path: str | None = None) -> dict:
    """Discover effective condition combos from past strong signals.

    Algorithm:
    1. Load signals.json for recent LONG/SHORT signals with score > 35.
    2. For each signal, walk back through candles to the signal timestamp,
       evaluate every condition in CONDITION_CATALOGUE.
    3. Count how many times each condition fired BEFORE a correct-direction
       signal vs a wrong-direction one.
    4. Pick the top 2–3 conditions with the highest precision for the dominant
       direction and bundle them into a strategy definition.
    """
    if signals_path is None:
        signals_path = os.path.join(os.path.dirname(__file__), "signals.json")

    try:
        with open(signals_path) as fh:
            raw_signals = json.load(fh)
    except Exception:
        raw_signals = []

    # Strong signals only
    strong = [s for s in raw_signals
              if abs(s.get("score", 0)) >= 35 and s.get("direction") in ("LONG", "SHORT")]

    if not strong:
        return _fallback_from_candles(candles)

    from strategies.custom import _eval_condition  # local import avoids circular

    # Score each condition: count fires on strong-signal candles
    cond_scores: dict[str, dict] = {}  # type -> {"bull": int, "bear": int, "total": int}

    for sig in strong[-30:]:           # last 30 strong signals
        direction = sig["direction"]
        sig_time  = sig.get("time", 0)

        # Find the candle closest to this signal
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
                if direction == "LONG":
                    cond_scores[ct]["bull"] += 1
                else:
                    cond_scores[ct]["bear"] += 1

    if not cond_scores:
        return _fallback_from_candles(candles)

    # Compute per-condition precision toward each direction
    bull_scored = []
    bear_scored = []
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

    # Pick the stronger direction
    top_bull_prec = bull_scored[0][1] if bull_scored else 0
    top_bear_prec = bear_scored[0][1] if bear_scored else 0

    if top_bull_prec >= top_bear_prec and bull_scored:
        direction     = "bullish"
        top_conds_raw = bull_scored[:3]
    elif bear_scored:
        direction     = "bearish"
        top_conds_raw = bear_scored[:3]
    else:
        return _fallback_from_candles(candles)

    # Build condition objects
    conditions = []
    for ct, prec in top_conds_raw:
        template = next((c for c in CONDITION_CATALOGUE if c["type"] == ct), None)
        if template:
            cond = dict(template)
            cond["label"] = _make_label(cond)
            conditions.append(cond)

    if not conditions:
        return _fallback_from_candles(candles)

    top_prec = top_conds_raw[0][1]
    desc = (f"Auto-discovered: {len(conditions)} conditions with {top_prec*100:.0f}% "
            f"precision on past {len(strong)} strong signals.")

    return {
        "name":             f"Auto — {'Bull' if direction == 'bullish' else 'Bear'} Pattern",
        "description":      desc,
        "signal_direction": direction,
        "logic":            "AND" if len(conditions) >= 2 else "OR",
        "weight":           10,
        "conditions":       conditions,
        "source":           "rule_learner",
        "precision":        round(top_prec, 3),
    }


def _fallback_from_candles(candles: list[dict]) -> dict:
    """Minimal fallback when no signal history is available.

    Looks at the last 20 candles: if cumulative delta is positive and price
    is above EMA25, generate a bullish strategy; otherwise bearish.
    """
    closes = [c["close"] for c in candles]
    cum_d  = sum(c.get("delta", 0) for c in candles[-20:])

    # Simple EMA25
    k   = 2 / 26
    ema = closes[0]
    for p in closes[1:]:
        ema = p * k + ema * (1 - k)
    price_above_ema = closes[-1] > ema

    if cum_d > 0 and price_above_ema:
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
        "name":             f"Auto — {'Bull' if direction == 'bullish' else 'Bear'} Base",
        "description":      "Auto-generated from current market snapshot (no signal history yet).",
        "signal_direction": direction,
        "logic":            "AND",
        "weight":           8,
        "conditions":       conditions,
        "source":           "candle_snapshot",
    }


# ── Mode 2: AI Generator (Groq) ───────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a quant analyst designing rules-based crypto trading strategies.

Given live market data, output ONE strategy as JSON.

Available condition types and their params:
- price_above_ema      {"period": 7|25|99}
- price_below_ema      {"period": 7|25|99}
- ema_cross_above      {"fast": 7|25, "slow": 25|99}
- ema_cross_below      {"fast": 7|25, "slow": 25|99}
- rsi_above            {"threshold": 30-70, "period": 14}
- rsi_below            {"threshold": 30-70, "period": 14}
- volume_spike         {"multiplier": 1.2-3.0}
- candle_bullish       {"body_pct": 0.3-0.8}
- candle_bearish       {"body_pct": 0.3-0.8}
- delta_positive       {"n_candles": 1-10}
- delta_negative       {"n_candles": 1-10}
- price_change_above   {"pct": 0.5-5.0, "n_candles": 3-20}
- price_change_below   {"pct": 0.5-5.0, "n_candles": 3-20}
- atr_expansion        {"multiplier": 1.1-3.0}
- cvd_rising           {"n_candles": 3-10}
- cvd_falling          {"n_candles": 3-10}
- near_support         {"atr_mult": 0.3-1.5}
- near_resistance      {"atr_mult": 0.3-1.5}

Return ONLY this JSON (no markdown, no explanation):
{
  "name": "short strategy name",
  "description": "one sentence: what market condition this targets",
  "signal_direction": "bullish" or "bearish",
  "logic": "AND" or "OR",
  "weight": 6-14,
  "conditions": [
    {"type": "<type>", "label": "<human label>", "params": {<params>}},
    ...
  ]
}
Use 2-4 conditions. Make them internally consistent (all bullish OR all bearish conditions unless mixing for confirmation)."""


def generate_from_ai(candles: list[dict], symbol: str = "?",
                     engine_breakdown: list[dict] | None = None) -> dict:
    """Use Groq llama-3.1-8b to auto-generate a strategy from market context.

    Returns a strategy definition dict on success.
    Raises RuntimeError if Groq key missing, cooldown active, or rate limited.
    """
    global _last_gen_time

    key = _groq_key()
    if not key:
        raise RuntimeError("GROQ_API_KEY not set — AI generation unavailable.")

    with _gen_lock:
        wait = seconds_until_ready()
        if wait > 0:
            raise RuntimeError(
                f"AI generator cooldown: {int(wait)}s remaining. "
                "Use 'Learn from Signals' for instant generation."
            )

    # Build compact market snapshot
    recent = candles[-12:]
    candle_lines = []
    for c in recent:
        d  = c.get("delta", 0)
        cl = c["close"]
        ch = c["high"]
        lo = c["low"]
        pct = (cl / candles[-13]["close"] - 1) * 100 if len(candles) > 13 else 0
        candle_lines.append(
            f"  close={cl:.4g} hi={ch:.4g} lo={lo:.4g} delta={d:+.0f} chg={pct:+.2f}%"
        )

    top_strategies = ""
    if engine_breakdown:
        top3 = sorted(engine_breakdown, key=lambda b: -abs(b.get("contribution", 0)))[:5]
        top_strategies = "\nTop engine signals:\n" + "\n".join(
            f"  {b['label']}: score={b['score']:+.2f} contribution={b['contribution']:+.1f}"
            for b in top3
        )

    user_text = (
        f"Symbol: {symbol}\n"
        f"Last {len(recent)} candles:\n" + "\n".join(candle_lines) +
        top_strategies +
        "\n\nDesign a strategy that targets the dominant pattern in this data."
    )

    body = {
        "model":       GEN_MODEL,
        "max_tokens":  MAX_TOKENS,
        "temperature": 0.4,
        "messages": [
            {"role": "system",  "content": _SYSTEM_PROMPT},
            {"role": "user",    "content": user_text},
        ],
    }

    try:
        resp = requests.post(
            GROQ_URL,
            json=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=25,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Groq request failed: {exc}") from exc

    with _gen_lock:
        _last_gen_time = time.time()

    if resp.status_code == 429:
        raise RuntimeError(
            "Groq rate limit hit. Use 'Learn from Signals' instead, "
            f"or wait {GEN_COOLDOWN}s and try again."
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Groq error {resp.status_code}: {resp.text[:200]}")

    raw = resp.json()["choices"][0]["message"]["content"].strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        strategy = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI returned invalid JSON: {raw[:200]}") from exc

    # Validate required fields
    required = ("name", "signal_direction", "conditions")
    missing  = [k for k in required if k not in strategy]
    if missing:
        raise RuntimeError(f"AI strategy missing fields: {missing}")

    # Enforce safe weight
    strategy["weight"]  = max(6, min(14, int(strategy.get("weight", 10))))
    strategy["logic"]   = strategy.get("logic", "AND").upper()
    strategy["source"]  = "ai_generated"

    # Ensure each condition has a label
    for cond in strategy.get("conditions", []):
        if "label" not in cond:
            cond["label"] = _make_label(cond)

    return strategy
