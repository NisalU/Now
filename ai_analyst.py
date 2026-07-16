"""AI analyst — discretionary structure/liquidity read on top of the
confluence engine.

Pipeline (no regime or trade-quality gates — every bar gets a full AI read):

    Market data (engine.py)
        -> Signal memory context (signal_memory.py)     -- past similar setups
        -> Primary AI analyst (Groq -> OpenRouter fallback, SYSTEM_PROMPT)
        -> Server-side risk gate (_apply_risk_gate)      -- re-checks the math
        -> AI critic (second opinion)                    -- tries to kill it
        -> Signal memory write

Provider priority:
    1. Groq  (GROQ_API_KEY) — fast, high-quality; rate-limited on free tier
    2. OpenRouter free models (OPENROUTER_API_KEY) — automatic fallback on
       Groq 429.  Uses SYSTEM_PROMPT_ENHANCED, a more directive prompt tuned
       for smaller/weaker models to still produce valid, conservative JSON.

Pure Python — uses `requests` only, so it runs on Termux.
"""
import json
import logging
import os
import threading
import time
import traceback
from collections import deque

import requests

import config
import market_regime
import signal_memory
import trade_quality
from engine import engine
from strategies.helpers import atr

log = logging.getLogger("ai_analyst")

# ---------------------------------------------------------------------------
# Provider URLs
# ---------------------------------------------------------------------------
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ---------------------------------------------------------------------------
# Model lists
# ---------------------------------------------------------------------------

# Groq models — tried in order; first that responds is cached for the session.
GROQ_MODELS = [
    os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama-3.1-8b-instant",
]

# OpenRouter free models — tried in order when Groq is rate-limited (429).
# SYSTEM_PROMPT_ENHANCED is used instead of SYSTEM_PROMPT for these models.
OPENROUTER_FREE_MODELS = [
    m for m in [
        os.environ.get("OPENROUTER_MODEL", ""),
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen3-235b-a22b:free",
        "nousresearch/hermes-3-llama-3.1-405b:free",
        "meta-llama/llama-3.1-70b-instruct:free",
        "mistralai/mistral-7b-instruct:free",
        "google/gemma-2-9b-it:free",
    ] if m
]

# ---------------------------------------------------------------------------
# Primary system prompt  (used with Groq high-quality models)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a high-level discretionary crypto market analyst.

Your job is to read market structure, liquidity behavior, and execution context on Binance crypto markets and publish only high-quality trade ideas.

You are NOT a signal factory.
You are NOT a confluence score interpreter.
You are NOT an auto-trading system.
You do NOT manufacture trades to stay active.

You think like a patient discretionary trader:
selective, thesis-driven, structure-first, and risk-aware.

Your default answer is:

WAIT

A trade must be earned by price behavior.

==================================================
CORE IDENTITY
==================================================

You analyze the market the way a professional trader would:

- Start with context
- Build a directional thesis
- Identify the key liquidity event
- Find the decision zone
- Wait for confirmation
- Define invalidation
- Decide whether the trade is worth taking

You do NOT reduce the market to a numeric score.
You do NOT approve trades because several indicators align.
You do NOT treat strategy labels as signal generators.

You may use technical tools and strategy outputs as supporting evidence, but they are secondary.
Primary decision-making must come from:

- market structure
- liquidity behavior
- price delivery
- reaction at key levels
- order-flow context when available
- location
- risk/reward
- invalidation clarity

If the market does not tell a clean story, the answer is WAIT.

==================================================
NON-NEGOTIABLE TRADING PRINCIPLES
==================================================

1. No clear thesis = no trade.
2. No clean location = no trade.
3. No logical invalidation = no trade.
4. Poor reward relative to risk = no trade.
5. Chasing extended price = no trade.
6. Mixed or conflicting structure = WAIT.
7. Lower timeframe signals never override higher timeframe context without a strong liquidity-led reversal case.
8. A missed trade is acceptable. A bad trade is unacceptable.

==================================================
HOW TO THINK
==================================================

Read the market in this order:

1. CONTEXT
What kind of environment is this?
- trend
- range
- expansion
- compression
- accumulation
- distribution
- squeeze
- exhaustion

2. STRUCTURE
What is price actually doing?
- continuation
- pullback
- failed breakout
- reversal attempt
- acceptance above value
- rejection from value
- BOS
- CHoCH
- trend acceleration
- trend deterioration

3. LIQUIDITY
Where are traders trapped or exposed?
- equal highs / equal lows
- prior swing highs / lows
- obvious breakout levels
- stop clusters
- sweep and reclaim
- failed sweep
- untouched liquidity targets

4. LOCATION
Is price sitting at a meaningful area?
- support / resistance
- supply / demand
- order block
- fair value gap
- value area edge
- POC
- fib retracement zone
- trendline retest
- prior breakout / breakdown level

5. CONFIRMATION
What actually confirms the idea?
- reclaim after sweep
- rejection from zone
- lower timeframe structure shift
- continuation after retest
- absorption
- CVD / delta confirmation when available
- acceptance above or below a key level

6. TRADEABILITY
Is this worth taking?
- entry quality
- stop placement quality
- target realism
- reward/risk
- proximity to opposing liquidity
- whether move is already too mature

==================================================
USE OF INPUT DATA
==================================================

You may receive structured strategy information from the engine.

Treat all strategy outputs as references, not commands.

Never say:
- "this is a trade because the score is high"
- "this is bullish because the engine is bullish"
- "signal approved due to confluence threshold"

Instead:
- interpret the underlying market story
- use strategy outputs only if they support the story
- ignore strategy outputs when price behavior contradicts them

If the engine suggests one direction but price structure and liquidity disagree, trust structure and liquidity.

==================================================
PRIORITY HIERARCHY
==================================================

When forming a decision, prioritize evidence in this order:

1. Higher timeframe structure
2. Liquidity event
3. Reaction at the decision zone
4. Order-flow confirmation
5. Execution quality
6. Strategy/tool alignment

Indicators and strategy modules can support a trade.
They cannot create one by themselves.

==================================================
WHAT A VALID TRADE MUST HAVE
==================================================

A valid trade idea must contain all of the following:

1. A clear market thesis
2. A precise location
3. A concrete trigger or confirmation
4. A logical invalidation point
5. Realistic targets
6. Minimum reward/risk of 1.8
7. Preferably 2.5 or higher
8. No obvious evidence that the move is already overextended

If any of these are missing, return WAIT.

==================================================
THESIS STANDARD
==================================================

Before deciding LONG or SHORT, silently form a thesis in this style:

- What happened?
- Why does that matter?
- Who is trapped or forced?
- Where is price likely drawn next?
- What proves the idea right?
- What proves it wrong?

Examples of valid thesis logic:

- Price swept sell-side liquidity into demand, reclaimed the level, and now has room toward buy-side liquidity.
- Price broke structure, retested supply, and order-flow failed to confirm upside, favoring continuation lower.
- Price is still inside unresolved range conditions, so directional conviction is not yet tradable.

Your final reasoning must reflect this kind of narrative.
Do not give generic indicator summaries.

==================================================
WHEN TO CHOOSE WAIT
==================================================

WAIT is the correct answer when:

- structure is mixed
- the move is already extended
- price is between meaningful levels
- no sweep / reaction / trigger is present
- the entry would be late
- reward/risk is weak
- order-flow is absent or contradictory
- higher timeframe bias is unclear
- the setup exists in theory but not yet in execution

WAIT is a strong professional decision, not a weak one.

==================================================
ENTRY AND RISK DESIGN
==================================================

If a trade is valid:

ENTRY:
- choose a price that makes structural sense
- prefer retracement or reclaim entries over emotional chasing
- entry must be close enough to invalidation to preserve trade quality

STOP:
- place stop at the actual invalidation point
- not a random percentage
- not a cosmetic buffer
- if structure would still remain valid after the stop, the stop is wrong

TP1:
- first realistic reaction level

TP2:
- main objective where opposing liquidity or structure is likely to react

Risk/reward:
- must be based on the actual entry, stop, and targets
- if not attractive, reject the setup

==================================================
INTERNAL REVIEW
==================================================

Before finalizing, challenge the setup from three angles:

ANALYST:
Why does this trade make sense?

CONTRARIAN:
What is the strongest reason this trade could fail?

RISK MANAGER:
Is this opportunity actually worth taking now, or is waiting better?

If the setup does not survive this review, return WAIT.

==================================================
STYLE RULES
==================================================

Be concise, precise, and professional.
Do not sound robotic.
Do not sound like a checklist generator.
Do not mention scoring systems, thresholds, or weighted confluence logic.
Do not hype the trade.
Do not overstate certainty.
Do not force confidence when conditions are unclear.

==================================================
OUTPUT RULES
==================================================

Respond ONLY with a JSON object using these exact keys:

{
  "signal": "LONG" | "SHORT" | "WAIT",
  "setup_type": "<short label for the setup, e.g. 'liquidity sweep + reclaim', 'order block retest', 'breakout continuation', or 'none' if WAIT>",
  "confidence": <integer 0-100>,
  "entry": <number|null>,
  "stop": <number|null>,
  "tp1": <number|null>,
  "tp2": <number|null>,
  "risk_reward": <number|null>,
  "orderflow_read": "<one precise sentence describing delta/CVD/absorption or state that confirmation is lacking>",
  "reasoning": "<2-4 sentences explaining the thesis, location, confirmation, and target logic in discretionary trader language>",
  "invalidation": "<one precise sentence stating what price behavior or level would invalidate the idea>"
}

==================================================
MEANING OF OUTPUT
==================================================

For "LONG":
- bullish thesis is clear
- location is good
- confirmation is present
- invalidation is logical
- reward/risk is acceptable

For "SHORT":
- bearish thesis is clear
- location is good
- confirmation is present
- invalidation is logical
- reward/risk is acceptable

For "WAIT":
- no clean executable edge exists right now
- if WAIT, use null for entry, stop, tp1, tp2, and risk_reward when no valid trade plan exists

==================================================
CONFIDENCE RULE
==================================================

Confidence is not a score derived from the engine.
Confidence is your discretionary conviction in the setup quality and execution clarity.

Use this rough interpretation:
- 0-39: unclear / poor / not tradable
- 40-59: developing but incomplete
- 60-74: decent but not exceptional
- 75-89: strong and tradable
- 90-100: rare, extremely clean setup

Do not inflate confidence.
WAIT is often the most professional answer.

==================================================
FINAL RULE
==================================================

You are paid for selectivity, not activity.

Never manufacture a signal.
Never justify a trade because multiple tools align.
Only approve a trade when price, liquidity, structure, and execution quality clearly support it.
Otherwise, return WAIT."""


# ---------------------------------------------------------------------------
# Enhanced system prompt  (used with OpenRouter free / smaller models)
#
# Design goals vs SYSTEM_PROMPT:
#   - JSON schema shown first and repeated — weaker models lose the format
#   - All instructions in short, direct sentences — reduces misinterpretation
#   - WAIT bias made even more explicit — free models tend to over-trade
#   - Anti-hallucination rule: only use levels from the provided data
#   - No abstract framing — every rule is concrete and checkable
#   - Anti-preamble instruction — prevents text before the JSON object
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_ENHANCED = """CRITICAL OUTPUT RULE
Your entire response must be a single valid JSON object.
Start with { and end with }.
No text, markdown, code fences, or explanation before or after the JSON.
Violating this rule makes your response unusable.

REQUIRED JSON SCHEMA
{
  "signal": "LONG" | "SHORT" | "WAIT",
  "setup_type": "<setup label, e.g. 'liquidity sweep + reclaim', or 'none' if WAIT>",
  "confidence": <integer 0-100>,
  "entry": <number or null>,
  "stop": <number or null>,
  "tp1": <number or null>,
  "tp2": <number or null>,
  "risk_reward": <number or null>,
  "orderflow_read": "<one sentence: describe delta/CVD/absorption evidence, or state it is absent>",
  "reasoning": "<2-4 sentences: thesis, location, confirmation, target logic — use trader language, not indicator names>",
  "invalidation": "<one sentence: exact price level or behavior that proves the idea wrong>"
}

When signal is WAIT: set entry, stop, tp1, tp2, risk_reward all to null.

---

ROLE
You are a professional crypto market analyst.
Your default answer is WAIT.
WAIT is not a failure. WAIT is the correct answer most of the time.
A missed trade is acceptable. A bad trade destroys capital.

---

WHEN YOU MAY CALL LONG OR SHORT
All seven conditions must be true simultaneously.
If any one is missing or doubtful, return WAIT.

1. Higher timeframe (4h) structure clearly supports the direction.
2. Price is at a precise, meaningful level from the data:
   order block, fair value gap, support, resistance, or post-sweep reclaim.
3. There is a concrete confirmation signal:
   reclaim after sweep, rejection from zone, structure break with follow-through,
   delta absorption, or CVD divergence.
4. Stop loss sits at a logical invalidation level (not a random percentage).
5. Risk/reward from entry to TP1 is at least 1.8.
6. Entry is within 2.5 ATR of the current live price.
7. You can state in one sentence who is trapped and where price is drawn next.

---

MANDATORY WAIT CONDITIONS
Return WAIT immediately if any of the following apply:

- Higher timeframe structure is unclear or mixed.
- Price is between levels with no obvious magnet or trigger.
- The directional move has already run 60%+ of the expected range.
- Stop placement has no structural basis (would need to be a round % number).
- Reward/risk to TP1 is below 1.8 after honest calculation.
- Entry would be more than 2.5 ATR from current price.
- Order-flow (delta, CVD) contradicts the structural direction.
- Recent similar setups on this symbol have lost 2+ times in a row.
- The risk_warnings field contains active cautions.

---

HOW TO READ THE INPUT DATA

higher_timeframe → Start here. 4h bias sets the directional filter.
liquidity → Look for sweeps and resting pools. These are the most important events.
key_levels → support, resistance, order_blocks, fvg_mids, poc, vah, val.
recent_candles → Read delta per bar. Is buying or selling dominant?
cvd_last_24 → Is cumulative delta trending with or against price?
strategies → Use as supporting evidence only. Never as a primary reason.
market_regime → Informs context. High volatility or chop = lean toward WAIT.
risk_warnings → Read all warnings. Heed them.

Do NOT invent price levels that are not in the provided data.
Do NOT use round numbers as support/resistance unless they appear in key_levels.
Do NOT call a trade because the engine score is high or multiple indicators agree.

---

VALID TRADE CHECKLIST (run this before calling LONG or SHORT)

[ ] HTF structure is clear and aligned
[ ] Price is at a named level from key_levels or liquidity
[ ] A confirmation event has occurred (not just "approaching the level")
[ ] Stop is at the structural invalidation point
[ ] R:R >= 1.8 to TP1 using actual numbers from entry, stop, tp1
[ ] Entry is not chasing (within 2.5 ATR)
[ ] Thesis can be stated in one sentence

If any box is unchecked, signal must be WAIT.

---

ENTRY AND RISK RULES

Entry: structural reclaim or retest entry preferred over breakout chase.
Stop: must be at the level that, if hit, proves the thesis wrong.
TP1: first realistic opposing level (opposing liquidity, supply/demand zone, POC).
TP2: next major structural target beyond TP1.
Risk/reward: compute honestly — (|tp1 - entry|) / (|entry - stop|). If < 1.8, return WAIT.

---

CONFIDENCE SCALE

0-39:  unclear / poor / not tradable — return WAIT
40-59: developing but missing a key piece — return WAIT unless very clean
60-74: decent setup, tradable with caution
75-89: strong setup, all conditions met
90-100: extremely clean, rare — only if every condition is met beyond doubt

Do not inflate confidence to justify a trade.
A 55% confidence WAIT is more honest than an 80% confidence bad trade.

---

FINAL INSTRUCTION
Output ONLY the JSON object.
No introduction. No summary. No markdown.
Begin your response with { and end with }."""


CRITIC_PROMPT = """You are the risk-management critic on a discretionary trading desk.

A primary analyst has proposed a trade call. Your only job is to try to kill it.
You are not here to be agreeable. You are here to protect capital.

You will be given the market context that the analyst saw, and the analyst's
proposed call. Challenge it on exactly these points:

1. Is this entry late? Has the move already run before this entry was proposed?
2. Is there liquidity or an opposing structure level sitting against this trade
   before either target is reached?
3. Is the reward/risk realistic given the actual distances between entry, stop
   and targets, not just the reported ratio?
4. Could this be a trap — a sweep, a failed breakout, or a level that looks
   like support/resistance but has already been invalidated?
5. Does the higher timeframe context conflict with this trade?

Be skeptical by default. A trade only survives your review if it clearly
holds up against all five questions. When in doubt, reject.

Respond ONLY with a JSON object using these exact keys:

{
  "approve": true | false,
  "concerns": ["<short phrase>", ...],
  "critique": "<1-3 sentences explaining your verdict, written for the trader who proposed the setup>"
}

If the input signal is already WAIT, approve it — WAIT never needs defending."""


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

def _get_api_key():
    """Read GROQ_API_KEY from env or local .env file."""
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if key:
        return key
    base = os.path.dirname(__file__)
    for name in (".env", ".env.local", ".env.development.local"):
        try:
            with open(os.path.join(base, name)) as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("GROQ_API_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def _get_openrouter_key():
    """Read OPENROUTER_API_KEY from env or local .env file."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    base = os.path.dirname(__file__)
    for name in (".env", ".env.local", ".env.development.local"):
        try:
            with open(os.path.join(base, name)) as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("OPENROUTER_API_KEY="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def _fnum(x, digits=6):
    return round(float(x), digits)


def _htf_summary(symbol):
    """Slim higher-timeframe read used for top-down context (priority #1 in
    the analyst's hierarchy). Never raises — HTF context is a nice-to-have,
    not a hard dependency."""
    try:
        htf = engine.get_state(symbol, config.AI_HTF_INTERVAL)
    except Exception:  # noqa: BLE001
        return None
    ov = htf.get("overlays", {})
    structure = ov.get("structure") or {}
    top_reasons = sorted(htf["breakdown"], key=lambda b: -abs(b["contribution"]))[:3]
    return {
        "interval": config.AI_HTF_INTERVAL,
        "price": _fnum(htf["price"]),
        "composite": htf["composite"],
        "direction": htf["direction"],
        "trend": structure.get("trend"),
        "structure_events": structure.get("events"),
        "top_reasons": [r for b in top_reasons for r in b["reasons"][:1] if r],
    }


def _liquidity_context(ov):
    """Explicit liquidity/structure fields the analyst is told to read
    first — sweeps, resting pools and BOS/CHoCH events — pulled out of the
    strategy overlays instead of buried inside per-strategy reasons."""
    structure = ov.get("structure") or {}
    return {
        "sweeps": ov.get("sweeps") or [],
        "liquidity_pools": ov.get("liquidity_pools") or [],
        "structure_trend": structure.get("trend"),
        "structure_events": structure.get("events") or [],
        "orderflow_divergence": ov.get("divergence"),
    }


def _risk_warnings(analysis, regime, memory_rows):
    """Plain-language warnings surfaced to the AI as additional context.
    These are informational only — they do NOT block or filter signals."""
    warnings = []
    ov = analysis.get("overlays", {})
    fundamentals = ov.get("fundamentals")

    if not regime.get("tradeable", True):
        warnings.append(
            f"Note: regime classifier flagged this market as {regime['regime']} — "
            f"factor this into your discretionary read."
        )
    if fundamentals:
        fr = fundamentals.get("funding_rate", 0)
        if abs(fr) > 0.0005:
            side = "longs" if fr > 0 else "shorts"
            warnings.append(f"Funding is stretched ({fr*100:.4f}%) — {side} are crowded")
        ls = fundamentals.get("long_short_ratio", 1.0)
        if ls > 3 or ls < 0.5:
            warnings.append(f"Long/short account ratio is extreme ({ls:.2f}) — contrarian risk")
    if memory_rows:
        losses = [r for r in memory_rows if r.get("result") == "loss"]
        if len(losses) >= 2:
            warnings.append(
                f"{len(losses)} of the last {len(memory_rows)} similar setups on this symbol "
                f"lost — treat this direction with extra scrutiny"
            )
    return warnings


def _compact_market(analysis, symbol, regime, structural_quality, memory_rows):
    """Shrink the engine's analysis dict into a compact prompt payload."""
    candles = analysis["candles"]
    ov = analysis.get("overlays", {})
    a = atr(candles) or analysis["price"] * 0.005

    recent = [
        {
            "t": c["time"], "o": _fnum(c["open"]), "h": _fnum(c["high"]),
            "l": _fnum(c["low"]), "c": _fnum(c["close"]),
            "vol": _fnum(c["volume"], 2), "delta": _fnum(c["delta"], 2),
        }
        for c in candles[-24:]
    ]

    cvd = ov.get("cvd") or []
    cvd_tail = [_fnum(p["value"], 2) for p in cvd[-24:]]

    strategies = [
        {
            "name": b["label"], "weight": b["weight"], "score": b["score"],
            "contribution": b["contribution"], "reasons": b["reasons"][:2],
        }
        for b in analysis["breakdown"]
    ]

    levels = {}
    if ov.get("support"):
        levels["support"] = [_fnum(lv["price"]) for lv in ov["support"][:4]]
    if ov.get("resistance"):
        levels["resistance"] = [_fnum(lv["price"]) for lv in ov["resistance"][:4]]
    if ov.get("volume_profile"):
        vp = ov["volume_profile"]
        levels["poc"] = _fnum(vp["poc"])
        levels["vah"] = _fnum(vp["vah"])
        levels["val"] = _fnum(vp["val"])
    if ov.get("order_blocks"):
        levels["order_blocks"] = [
            {"type": ob["type"], "top": _fnum(ob["top"]), "bottom": _fnum(ob["bottom"])}
            for ob in ov["order_blocks"][:3]
        ]
    if ov.get("fvgs"):
        levels["fvg_mids"] = [
            {"type": f["type"], "mid": _fnum(f["mid"])} for f in ov["fvgs"][:3]
        ]

    fundamentals = ov.get("fundamentals")

    return {
        "symbol": analysis["symbol"],
        "chart": config.AI_INTERVAL,
        "price": _fnum(analysis["price"]),
        "atr": _fnum(a),
        "change_24h_pct": (analysis.get("ticker") or {}).get("change_pct"),
        "engine_composite_score": analysis["composite"],
        "engine_direction": analysis["direction"],
        "market_regime": regime,             # context only — not a gate
        "structural_quality": structural_quality,  # context only — not a gate
        "higher_timeframe": _htf_summary(symbol),
        "liquidity": _liquidity_context(ov),
        "strategies": strategies,
        "orderflow_divergence": ov.get("divergence"),
        "cvd_last_24": cvd_tail,
        "key_levels": levels,
        "futures_fundamentals": fundamentals,
        "recent_similar_setups": memory_rows,
        "risk_warnings": _risk_warnings(analysis, regime, memory_rows),
        "recent_candles": recent,
    }


def _fmt_setup_type(raw):
    """Convert raw setup_type from AI into a clean display label."""
    if not raw or raw == "none":
        return "—"
    # Title-case each word, cap at 30 chars
    words = str(raw).replace("_", " ").replace("+", " + ").split()
    label = " ".join(w.capitalize() for w in words)
    return label[:30]


# ---------------------------------------------------------------------------
# Main analyst class
# ---------------------------------------------------------------------------

class AIAnalyst:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache = {}          # symbol -> ai result dict
        self._model = None        # first working Groq model, cached for session
        self._or_model = None     # first working OpenRouter model, cached for session
        self._groq_rate_limited = False   # flip to True on 429; reset after 5 min
        self._groq_rl_until = 0.0         # epoch time when Groq cooldown expires
        self.enabled = bool(_get_api_key() or _get_openrouter_key())
        self.last_error = None

        # Engine status metrics
        self._last_latency = None       # ms of last AI round-trip
        self._inference_count = 0       # total AI calls made
        self._inference_window = deque(maxlen=120)  # timestamps for rate calculation
        self._active_models = set()     # models that have responded this session

        # Per-model individual rate-limit tracking (separate from global Groq RL).
        # Groq can rate-limit a specific model while others still work.
        # Key: model name  Value: epoch time when individual cooldown expires
        self._model_rl_until: dict = {}
        _MODEL_RL_SECONDS = 90          # individual model cooldown (90 s)
        self._MODEL_RL_SECONDS = _MODEL_RL_SECONDS

        # Pipeline event log — ring-buffer pushed to the dashboard after each run.
        self._pipeline_events: deque = deque(
            maxlen=getattr(config, "PIPELINE_LOG_MAX", 100)
        )
        self._active_run: dict = {}     # last emitted pipeline stage

        # Recent LONG/SHORT signals for the dashboard table (max 20)
        self._recent_ai_signals = []
        self._load_recent_signals_from_db()

    def _load_recent_signals_from_db(self):
        """Seed the in-memory recent signals list from signal_memory on startup."""
        try:
            rows = []
            for sym in config.SYMBOLS:
                rows.extend(signal_memory.recent_similar(sym, limit=4))
            rows.sort(key=lambda r: r.get("timestamp", 0), reverse=True)
            for r in rows[:20]:
                if r.get("direction") in ("LONG", "SHORT"):
                    self._recent_ai_signals.append({
                        "time": r["timestamp"],
                        "symbol": r.get("symbol", ""),
                        "setup_type": _fmt_setup_type(r.get("setup_type", "")),
                        "direction": r["direction"],
                        "confidence": 0,  # not stored in memory; placeholder
                    })
            self._recent_ai_signals = self._recent_ai_signals[:20]
        except Exception:  # noqa: BLE001
            pass

    def get_cached(self, symbol):
        with self._lock:
            return self._cache.get(symbol)

    def get_status(self):
        """Return AI engine status metrics for the dashboard widget."""
        with self._lock:
            now = time.time()
            recent = [t for t in self._inference_window if now - t < 60]
            rate_per_min = len(recent)
            signals = list(self._recent_ai_signals)
            cur_model = self._model or self._or_model
            groq_rl = self._groq_rate_limited and now < self._groq_rl_until
            cooldown_remaining = max(0, int(self._groq_rl_until - now)) if groq_rl else 0

        # Infer provider from model name: OpenRouter models contain a "/"
        if cur_model:
            provider = "openrouter" if "/" in cur_model else "groq"
        else:
            provider = None

        return {
            "online": self.enabled,
            "version": "v4.3",
            "active_models": len(config.WEIGHTS),  # strategy model count (10)
            "latency_ms": self._last_latency,
            "inference_per_min": rate_per_min,
            "total_inferences": self._inference_count,
            "current_model": cur_model,
            "provider": provider,
            "groq_rate_limited": groq_rl,
            "groq_cooldown_remaining": cooldown_remaining,
            "last_error": self.last_error,
            "recent_signals": signals,
        }

    def get_recent_signals(self):
        with self._lock:
            return list(self._recent_ai_signals)

    def get_pipeline_log(self):
        """Return recent pipeline events for the dashboard (newest-first)."""
        with self._lock:
            return list(reversed(self._pipeline_events))

    def get_active_run(self):
        """Return the most recently emitted pipeline stage."""
        with self._lock:
            return dict(self._active_run)

    def _record_evt(self, **kwargs):
        """Append a timestamped pipeline event to the ring buffer."""
        evt = {"ts": round(time.time(), 3), **kwargs}
        with self._lock:
            self._pipeline_events.append(evt)
            self._active_run = evt
        return evt

    # -----------------------------------------------------------------------
    # Low-level HTTP calls
    # -----------------------------------------------------------------------

    def _post_model(self, url, headers, model, system_prompt, payload_text, timeout=45):
        """Single HTTP POST to an OpenAI-compatible chat endpoint.
        Returns (model, content_str) on success.
        Raises RuntimeError with a tag prefix for caller to inspect:
          - 'RATE_LIMIT:...'  → 429 received
          - 'MODEL_ERROR:...' → 400/404 model issue, try next model
          - 'HTTP_ERROR:...'  → other non-200 status
        """
        resp = requests.post(
            url,
            headers=headers,
            json={
                "model": model,
                "temperature": 0.2,
                "max_tokens": 700,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": payload_text},
                ],
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
            return model, content
        if resp.status_code == 429:
            raise RuntimeError(f"RATE_LIMIT:{model}: {resp.text[:120]}")
        if resp.status_code in (400, 404) and "model" in resp.text.lower():
            raise RuntimeError(f"MODEL_ERROR:{model}: {resp.status_code} {resp.text[:80]}")
        raise RuntimeError(f"HTTP_ERROR:{model}: {resp.status_code} {resp.text[:160]}")

    def _call_groq_models(self, system_prompt, payload_text):
        """Try each Groq model in order.
        Returns (model, content) on success.
        Raises RuntimeError('RATE_LIMIT:...') if ALL tried models returned 429.
        Raises RuntimeError with details if all failed for other reasons.
        """
        key = _get_api_key()
        if not key:
            raise RuntimeError("GROQ_API_KEY not set")
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        models = [self._model] if self._model else []
        models += [m for m in GROQ_MODELS if m and m not in models]

        # Skip models that are still in their individual rate-limit cooldown.
        # This avoids burning an API call on a model we already know is limited.
        now_t = time.time()
        active_models = [m for m in models if now_t >= self._model_rl_until.get(m, 0)]
        skipped = [m for m in models if m not in active_models]
        if skipped:
            log.info("Skipping individually rate-limited Groq models: %s", skipped)
        if not active_models:
            # Every model is in cooldown — raise global RATE_LIMIT to trigger OpenRouter.
            raise RuntimeError("RATE_LIMIT:all Groq models are individually rate-limited")
        models = active_models

        last_exc = None
        all_rate_limited = True
        for model in models:
            try:
                self._record_evt(stage="model_attempt", provider="groq", model=model)
                result = self._post_model(GROQ_URL, headers, model, system_prompt, payload_text)
                # Success — cache this model and clear its individual cooldown.
                self._model = model
                self._model_rl_until.pop(model, None)
                self._active_models.add(model)
                self._record_evt(stage="model_success", provider="groq", model=model)
                return result
            except RuntimeError as e:
                msg = str(e)
                last_exc = e
                if msg.startswith("RATE_LIMIT:"):
                    # Mark this specific model as rate-limited for a short window.
                    self._model_rl_until[model] = time.time() + self._MODEL_RL_SECONDS
                    # If this was our cached preferred model, clear it so we
                    # don't keep hitting it on every subsequent call.
                    if self._model == model:
                        self._model = None
                    self._record_evt(
                        stage="model_rate_limited", provider="groq", model=model,
                        message=f"Rate limited — trying next model",
                        cooldown_s=self._MODEL_RL_SECONDS,
                    )
                    log.warning("Groq rate limit on %s — trying next model", model)
                    continue  # try next Groq model
                else:
                    all_rate_limited = False
                    if msg.startswith("MODEL_ERROR:"):
                        self._record_evt(stage="model_error", provider="groq", model=model, message=msg[:120])
                        continue  # model gone, try next
                    raise  # HTTP_ERROR or other — surface immediately
            except requests.RequestException as e:
                last_exc = e
                all_rate_limited = False
                continue

        if all_rate_limited:
            raise RuntimeError(f"RATE_LIMIT:all Groq models rate-limited: {last_exc}")
        raise RuntimeError(f"all Groq models failed: {last_exc}")

    def _call_openrouter_models(self, system_prompt, payload_text):
        """Try each OpenRouter free model in order.
        Returns (model, content) on success.
        Raises RuntimeError if all fail.
        """
        key = _get_openrouter_key()
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/NisalU/Now",
            "X-Title": "AI Trading Signal Bot",
        }
        models = [self._or_model] if self._or_model else []
        models += [m for m in OPENROUTER_FREE_MODELS if m and m not in models]

        last_exc = None
        for model in models:
            try:
                result = self._post_model(
                    OPENROUTER_URL, headers, model, system_prompt, payload_text, timeout=60
                )
                self._or_model = model
                self._active_models.add(model)
                log.info("OpenRouter fallback succeeded with model: %s", model)
                return result
            except RuntimeError as e:
                msg = str(e)
                last_exc = e
                if msg.startswith("RATE_LIMIT:") or msg.startswith("MODEL_ERROR:"):
                    log.warning("OpenRouter model %s failed (%s), trying next", model, msg[:60])
                    continue
                raise
            except requests.RequestException as e:
                last_exc = e
                continue

        raise RuntimeError(f"all OpenRouter models failed: {last_exc}")

    def _call_groq_with_prompt(self, system_prompt, payload_text):
        """Route AI call: Groq first, OpenRouter free models as fallback on rate-limit.

        When Groq is rate-limited the enhanced prompt (SYSTEM_PROMPT_ENHANCED)
        is used for OpenRouter instead of the caller's system_prompt, unless
        the caller itself is already using SYSTEM_PROMPT_ENHANCED (e.g. the
        critic passing its own prompt — in that case we use the passed prompt).
        Returns (model_name, content_str).
        """
        now = time.time()
        groq_ok = not self._groq_rate_limited or now >= self._groq_rl_until

        if groq_ok and _get_api_key():
            try:
                result = self._call_groq_models(system_prompt, payload_text)
                # Successful Groq call — clear any previous global rate-limit flag.
                if self._groq_rate_limited:
                    self._record_evt(stage="provider_recovered", provider="groq",
                                     message="Groq recovered — back on primary provider")
                self._groq_rate_limited = False
                return result
            except RuntimeError as e:
                if str(e).startswith("RATE_LIMIT:"):
                    log.warning(
                        "Groq rate-limited across all models — falling back to OpenRouter "
                        "for the next %d seconds", config.GROQ_RATE_LIMIT_COOLDOWN
                    )
                    self._groq_rate_limited = True
                    self._groq_rl_until = now + config.GROQ_RATE_LIMIT_COOLDOWN
                    self._record_evt(
                        stage="provider_fallback", from_provider="groq",
                        to_provider="openrouter",
                        message=f"All Groq models rate-limited — switching to OpenRouter for {config.GROQ_RATE_LIMIT_COOLDOWN}s",
                        cooldown_s=config.GROQ_RATE_LIMIT_COOLDOWN,
                    )
                    # Fall through to OpenRouter below.
                else:
                    raise

        # OpenRouter fallback — use enhanced prompt for the primary analyst,
        # keep the caller's prompt for the critic (it's already very concise).
        or_key = _get_openrouter_key()
        if not or_key:
            raise RuntimeError(
                "Groq rate-limited and OPENROUTER_API_KEY is not set. "
                "Add your OpenRouter key to use the free-model fallback."
            )

        self._record_evt(stage="provider_fallback_active", provider="openrouter",
                         cooldown_remaining=max(0, int(self._groq_rl_until - time.time())))

        # Use enhanced prompt for primary analyst; keep original for critic.
        or_prompt = (
            SYSTEM_PROMPT_ENHANCED
            if system_prompt == SYSTEM_PROMPT
            else system_prompt
        )
        return self._call_openrouter_models(or_prompt, payload_text)

    def _call_groq(self, payload_text):
        return self._call_groq_with_prompt(SYSTEM_PROMPT, payload_text)

    # -----------------------------------------------------------------------
    # Risk gate
    # -----------------------------------------------------------------------

    def _apply_risk_gate(self, result, atr_value, ov):
        """Re-derive risk/reward from actual entry/stop/tp1 numbers and
        downgrade to WAIT only for hard arithmetic failures. This is NOT a
        market-quality filter — it only catches logically broken trade plans
        (entry == stop, missing levels, negative R:R)."""
        if result["signal"] not in ("LONG", "SHORT"):
            return result

        entry, stop, tp1, price = result["entry"], result["stop"], result["tp1"], result["price"]
        gate_reason = None

        if entry is None or stop is None or tp1 is None:
            gate_reason = "missing entry/stop/tp1 — no complete trade plan"
        else:
            risk = abs(entry - stop)
            reward = abs(tp1 - entry)
            if risk <= 0:
                gate_reason = "stop equals entry — invalid invalidation"
            else:
                recomputed_rr = round(reward / risk, 2)
                result["risk_reward"] = recomputed_rr
                if recomputed_rr < config.AI_MIN_RISK_REWARD:
                    gate_reason = (
                        f"recomputed risk/reward {recomputed_rr} is below the "
                        f"{config.AI_MIN_RISK_REWARD} minimum"
                    )
                elif atr_value > 0 and abs(entry - price) > atr_value * config.AI_MAX_ENTRY_ATR_DISTANCE:
                    gate_reason = (
                        f"entry is {abs(entry - price) / atr_value:.1f} ATR from live price "
                        f"— move already extended"
                    )

        if gate_reason:
            direction = result["signal"]
            result.update({
                "signal": "WAIT",
                "direction": None,
                "entry": None, "stop": None, "tp1": None, "tp2": None,
                "risk_reward": None,
                "gated": True,
                "gate_reason": gate_reason,
                "reasoning": (
                    f"Model proposed {result.get('confidence', 0)}% confidence "
                    f"{direction}, but the risk gate rejected it: "
                    f"{gate_reason}. " + result["reasoning"]
                )[:600],
            })
        return result

    # -----------------------------------------------------------------------
    # AI critic
    # -----------------------------------------------------------------------

    def _call_critic(self, result, market):
        """Second, independent AI call that challenges the primary call.
        Never raises; on any failure the primary result is kept as-is."""
        if result["signal"] not in ("LONG", "SHORT"):
            return result, None

        review_payload = {
            "market_context": market,
            "proposed_call": {
                "signal": result["signal"],
                "setup_type": result.get("setup_type"),
                "entry": result["entry"],
                "stop": result["stop"],
                "tp1": result["tp1"],
                "tp2": result["tp2"],
                "risk_reward": result["risk_reward"],
                "orderflow_read": result["orderflow_read"],
                "reasoning": result["reasoning"],
                "invalidation": result["invalidation"],
            },
        }
        try:
            _, raw = self._call_groq_with_prompt(
                CRITIC_PROMPT,
                "Review this proposed trade call against its market context:\n"
                + json.dumps(review_payload, separators=(",", ":")),
            )
            critic_out = json.loads(raw)
        except Exception as e:  # noqa: BLE001
            log.warning("AI critic call failed, keeping primary call as-is: %s", e)
            return result, None

        approve = bool(critic_out.get("approve", True))
        critique = str(critic_out.get("critique") or "")[:400]
        concerns = [str(c)[:120] for c in (critic_out.get("concerns") or [])][:5]

        if not approve:
            result.update({
                "signal": "WAIT",
                "direction": None,
                "entry": None, "stop": None, "tp1": None, "tp2": None,
                "risk_reward": None,
                "gated": True,
                "gate_reason": "rejected by AI critic review",
                "reasoning": (
                    f"Critic rejected this {result['signal']} setup: {critique} " + result["reasoning"]
                )[:600],
            })
        return result, {"approve": approve, "concerns": concerns, "critique": critique}

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def _wait_result(self, symbol, analysis, reason, regime=None, extra=None):
        result = {
            "symbol": symbol,
            "interval": config.AI_INTERVAL,
            "updated": int(time.time()),
            "price": analysis["price"],
            "engine_score": analysis["composite"],
            "model": None,
            "signal": "WAIT",
            "direction": None,
            "setup_type": "none",
            "confidence": 0,
            "entry": None, "stop": None, "tp1": None, "tp2": None,
            "risk_reward": None,
            "orderflow_read": "",
            "reasoning": reason,
            "invalidation": "",
            "gated": False,
            "gate_reason": None,
            "market_regime": (regime or {}).get("regime"),
            "htf_bias": None,
            "liquidity_context": None,
            "trade_quality": None,
            "critic": None,
            "latency_ms": None,
        }
        if extra:
            result.update(extra)
        with self._lock:
            self._cache[symbol] = result
        return result

    def analyze(self, symbol):
        """Run the full pipeline for `symbol`. Blocking (call in a thread).

        No regime filter or trade-quality gate — every bar gets a full AI read.
        Pipeline: market_data -> memory_context -> ai_call -> trade_quality -> critic -> signal_out.
        Pipeline events are recorded at each stage and exposed via get_pipeline_log().
        """
        run_id = f"{symbol}:{int(time.time())}"

        # ── Stage 1: Market data ──────────────────────────────────────────
        self._record_evt(run_id=run_id, stage="market_data", status="fetching", symbol=symbol)
        t_data = time.time()
        analysis = engine.get_state(symbol, config.AI_INTERVAL)
        ov = analysis.get("overlays", {})
        a = atr(analysis["candles"]) or analysis["price"] * 0.005
        self._record_evt(
            run_id=run_id, stage="market_data", status="done", symbol=symbol,
            price=analysis["price"], composite=round(analysis["composite"], 1),
            regime_label=None,           # filled after classify() below
            duration_ms=int((time.time() - t_data) * 1000),
        )

        # Regime and structural quality computed for AI *context* only (not gates).
        regime = market_regime.classify(analysis)
        structural_quality = trade_quality.grade(analysis, plan=None, regime=None)

        # Update the market_data event with regime info now that we have it.
        self._record_evt(
            run_id=run_id, stage="market_data_regime", symbol=symbol,
            regime=regime["regime"], direction=analysis["direction"],
            composite=round(analysis["composite"], 1),
        )

        # ── Stage 2: Signal memory context ───────────────────────────────
        self._record_evt(run_id=run_id, stage="memory_context", status="loading", symbol=symbol)
        memory_rows = signal_memory.recent_similar(symbol, limit=config.SIGNAL_MEMORY_LOOKBACK)
        self._record_evt(
            run_id=run_id, stage="memory_context", status="done", symbol=symbol,
            found=len(memory_rows),
        )

        # ── Stage 3: Primary AI call ──────────────────────────────────────
        self._record_evt(
            run_id=run_id, stage="ai_call", status="start", symbol=symbol,
            interval=config.AI_INTERVAL, htf_interval=config.AI_HTF_INTERVAL,
        )
        market = _compact_market(analysis, symbol, regime, structural_quality, memory_rows)
        user_text = (
            "Here is the live market data and context. Do your top-down discretionary read "
            "and give your single best call as JSON:\n"
            + json.dumps(market, separators=(",", ":"))
        )
        t0 = time.time()
        model, raw = self._call_groq(user_text)
        latency_ms = int((time.time() - t0) * 1000)

        # Infer provider from model name: OpenRouter models contain a "/"
        provider = "openrouter" if model and "/" in model else "groq"
        self._record_evt(
            run_id=run_id, stage="ai_call", status="done", symbol=symbol,
            model=model, provider=provider, latency_ms=latency_ms,
        )

        # Update status metrics.
        with self._lock:
            self._last_latency = latency_ms
            self._inference_count += 1
            self._inference_window.append(time.time())

        try:
            out = json.loads(raw)
        except ValueError:
            raise RuntimeError(f"AI returned non-JSON: {raw[:160]}")

        signal = str(out.get("signal", "WAIT")).upper()
        if signal not in ("LONG", "SHORT", "WAIT"):
            signal = "WAIT"

        def num(k):
            v = out.get(k)
            try:
                return round(float(v), 8) if v is not None else None
            except (TypeError, ValueError):
                return None

        htf = market.get("higher_timeframe") or {}

        result = {
            "symbol": symbol,
            "interval": config.AI_INTERVAL,
            "updated": int(time.time()),
            "price": analysis["price"],
            "engine_score": analysis["composite"],
            "model": model,
            "model_used": model,         # explicit alias for dashboard
            "provider": provider,        # "groq" | "openrouter"
            "signal": signal,
            "direction": signal if signal in ("LONG", "SHORT") else None,
            "setup_type": str(out.get("setup_type") or "none")[:80],
            "confidence": max(0, min(100, int(out.get("confidence") or 0))),
            "entry": num("entry"),
            "stop": num("stop"),
            "tp1": num("tp1"),
            "tp2": num("tp2"),
            "risk_reward": num("risk_reward"),
            "orderflow_read": str(out.get("orderflow_read") or "")[:300],
            "reasoning": str(out.get("reasoning") or "")[:600],
            "invalidation": str(out.get("invalidation") or "")[:300],
            "gated": False,
            "gate_reason": None,
            "market_regime": regime["regime"],
            "htf_bias": htf.get("direction"),
            "liquidity_context": market["liquidity"],
            "trade_quality": None,
            "critic": None,
            "latency_ms": latency_ms,
        }

        self._record_evt(
            run_id=run_id, stage="ai_parsed", symbol=symbol,
            signal=signal, confidence=result["confidence"],
            setup_type=result["setup_type"], model=model, provider=provider,
        )

        # Risk gate DISABLED — trusting AI's own risk/reward evaluation.
        # (Kept as method for reference; not called so no signals are lost.)

        # ── Stage 4: Trade quality ────────────────────────────────────────
        self._record_evt(run_id=run_id, stage="trade_quality", status="computing", symbol=symbol)
        plan = {"entry": result["entry"], "stop": result["stop"], "tp1": result["tp1"]}
        final_quality = trade_quality.grade(analysis, plan=plan, regime=None)
        result["trade_quality"] = final_quality
        self._record_evt(
            run_id=run_id, stage="trade_quality", status="done", symbol=symbol,
            grade=final_quality["grade"] if final_quality else None,
        )

        # ── Stage 5: AI critic ────────────────────────────────────────────
        if config.AI_CRITIC_ENABLED and result["signal"] in ("LONG", "SHORT"):
            self._record_evt(
                run_id=run_id, stage="critic", status="start", symbol=symbol,
                reviewing_signal=result["signal"], confidence=result["confidence"],
            )
            result, critic = self._call_critic(result, market)
            result["critic"] = critic
            approved = (critic or {}).get("approve", True) if critic else True
            self._record_evt(
                run_id=run_id, stage="critic", status="done", symbol=symbol,
                approved=approved,
                concerns=len((critic or {}).get("concerns") or []) if critic else 0,
                critique=((critic or {}).get("critique") or "")[:120] if critic else None,
                final_signal=result["signal"],
            )
        else:
            result["critic"] = None
            if not config.AI_CRITIC_ENABLED:
                self._record_evt(run_id=run_id, stage="critic", status="skipped",
                                 symbol=symbol, reason="critic disabled in config")
            else:
                self._record_evt(run_id=run_id, stage="critic", status="skipped",
                                 symbol=symbol, reason="AI returned WAIT — critic not invoked")

        # ── Stage 6: Signal out ───────────────────────────────────────────
        self._record_evt(
            run_id=run_id, stage="signal_out", symbol=symbol,
            signal=result["signal"], confidence=result["confidence"],
            model=model, provider=provider, latency_ms=latency_ms,
            setup_type=result["setup_type"],
            gated=result.get("gated", False),
            gate_reason=result.get("gate_reason"),
        )

        # ── Stage 7: Signal memory write ─────────────────────────────────
        if result["signal"] in ("LONG", "SHORT"):
            signal_memory.record({
                "symbol": symbol,
                "timestamp": result["updated"],
                "setup_type": result["setup_type"],
                "direction": result["signal"],
                "entry": result["entry"],
                "stop": result["stop"],
                "target": result["tp1"],
                "market_condition": regime["regime"],
                "trade_quality": result["trade_quality"]["grade"] if result["trade_quality"] else None,
                "ai_reasoning": result["reasoning"],
                "result": "pending",
            })
            # Update in-memory recent signals table.
            with self._lock:
                self._recent_ai_signals.insert(0, {
                    "time": result["updated"],
                    "symbol": symbol,
                    "setup_type": _fmt_setup_type(result["setup_type"]),
                    "direction": result["signal"],
                    "confidence": result["confidence"],
                })
                self._recent_ai_signals = self._recent_ai_signals[:20]

        with self._lock:
            self._cache[symbol] = result
        self.last_error = None
        return result

    def analyze_safe(self, symbol):
        """Like analyze() but never raises; returns cached/error placeholder."""
        try:
            return self.analyze(symbol)
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            traceback.print_exc()
            cached = self.get_cached(symbol)
            if cached:
                return cached
            return {
                "symbol": symbol,
                "interval": config.AI_INTERVAL,
                "updated": int(time.time()),
                "error": str(e)[:200],
            }


ai_analyst = AIAnalyst()
