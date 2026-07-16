"""AI analyst — discretionary structure/liquidity read on top of the
confluence engine.

Pipeline (no regime or trade-quality gates — every bar gets a full AI read):

    Market data (engine.py)
        -> Signal memory context (signal_memory.py)     -- past similar setups
        -> Primary AI analyst (Google Gemini, SYSTEM_PROMPT)
        -> Server-side risk gate (_apply_risk_gate)      -- re-checks the math
        -> AI critic (second opinion)                    -- tries to kill it
        -> Signal memory write

Provider:
    Google Gemini REST API (GEMINI_API_KEY).
    Models tried in order with per-model rate-limit cooldown.

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
# Provider — Google Gemini REST API
# ---------------------------------------------------------------------------
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Models tried in order; first success is cached for the session.
# When a model returns 429 it is individually rate-limited for
# MODEL_RL_COOLDOWN seconds and the next one is tried automatically.
GEMINI_MODELS = [
    m for m in [
        os.environ.get("GEMINI_MODEL", ""),  # user-pinned model (highest priority)
        "gemini-2.0-flash",                  # fast, capable, generous free tier
        "gemini-2.0-flash-lite",             # lighter fallback
        "gemini-1.5-flash",                  # reliable fallback
        "gemini-1.5-pro",                    # high-quality last resort
    ] if m
]

# ---------------------------------------------------------------------------
# Primary system prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a discretionary crypto market analyst on Binance.

DEFAULT: WAIT. A trade must be earned by clear price behavior.

DECISION ORDER:
1. Context — trend/range/compression/expansion/exhaustion
2. Structure — BOS, CHoCH, continuation, reversal attempt
3. Liquidity — sweeps, equal highs/lows, resting pools, trapped traders
4. Location — order block, FVG, S/R, POC, fib, value edge
5. Confirmation — reclaim after sweep, LTF structure shift, absorption, CVD
6. Tradeability — entry quality, stop logic, R:R, target realism

RULES (non-negotiable):
- No clear thesis → WAIT
- No clean location → WAIT
- No logical invalidation → WAIT
- Poor R:R → WAIT
- Chasing extended move → WAIT
- Conflicting structure → WAIT
- A missed trade is fine. A bad trade is not.

OUTPUT: a single valid JSON object only. No markdown, no text outside the JSON.

{
  "signal": "LONG" | "SHORT" | "WAIT",
  "setup_type": "<brief label or 'none'>",
  "confidence": <integer 0-100>,
  "entry": <number or null>,
  "stop": <number or null>,
  "tp1": <number or null>,
  "tp2": <number or null>,
  "risk_reward": <number or null>,
  "orderflow_read": "<one sentence on delta/CVD or state absent>",
  "reasoning": "<2-3 sentences: thesis, location, confirmation, target>",
  "invalidation": "<one sentence: exact level or behavior that kills the idea>"
}

WAIT → set entry, stop, tp1, tp2, risk_reward to null.
Confidence: 0-39 unclear, 40-59 developing, 60-74 decent, 75-89 strong, 90-100 rare.
You are paid for selectivity, not activity. Never manufacture a signal."""


# ---------------------------------------------------------------------------
# Enhanced system prompt  (used with smaller / fallback models)
#
# Design goals vs SYSTEM_PROMPT:
#   - JSON schema shown first and repeated — weaker models lose the format
#   - All instructions in short, direct sentences — reduces misinterpretation
#   - WAIT bias made even more explicit — free models tend to over-trade
#   - Anti-hallucination rule: only use levels from the provided data
#   - No abstract framing — every rule is concrete and checkable
#   - Anti-preamble instruction — prevents text before the JSON object
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_ENHANCED = """OUTPUT RULE: respond with a single JSON object only.
Start with { and end with }. No text, markdown, or code fences outside the JSON.

SCHEMA:
{
  "signal": "LONG" | "SHORT" | "WAIT",
  "setup_type": "<label or 'none'>",
  "confidence": <0-100>,
  "entry": <number or null>,
  "stop": <number or null>,
  "tp1": <number or null>,
  "tp2": <number or null>,
  "risk_reward": <number or null>,
  "orderflow_read": "<one sentence>",
  "reasoning": "<2-3 sentences>",
  "invalidation": "<one sentence>"
}

RULES:
- Default answer is WAIT.
- Only use price levels from the provided data — never invent numbers.
- WAIT → entry/stop/tp1/tp2/risk_reward all null.
- LONG/SHORT → all fields required and logically consistent.
- No clear thesis, location, or invalidation → WAIT.
- Output ONLY the JSON object. Begin with {."""


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

def _parse_env_value(line: str, key: str) -> str:
    """Extract value for `key` from a .env line, handling both
    ``KEY=value`` and ``export KEY=value`` formats."""
    line = line.strip()
    if line.startswith("#") or "=" not in line:
        return ""
    # strip optional leading 'export '
    if line.startswith("export "):
        line = line[len("export "):].lstrip()
    if not line.startswith(key + "="):
        return ""
    return line.split("=", 1)[1].strip().strip('"').strip("'")


def _get_gemini_key():
    """Read GEMINI_API_KEY from environment."""
    return os.environ.get("GEMINI_API_KEY", "").strip()


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
        for c in candles[-10:]
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
        self._or_model = None     # last successful Gemini model (tried first next run)
        self.enabled = bool(_get_gemini_key())
        self.last_error = None

        # Engine status metrics
        self._last_latency = None       # ms of last AI round-trip
        self._inference_count = 0       # total AI calls made
        self._inference_window = deque(maxlen=120)  # timestamps for rate calculation
        self._active_models = set()     # models that have responded this session

        # Per-model individual rate-limit tracking.
        # Key: model name  Value: epoch time when individual cooldown expires
        self._model_rl_until: dict = {}
        self._MODEL_RL_SECONDS = getattr(config, "MODEL_RL_COOLDOWN", 90)

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
            cur_model = self._or_model
            # Collect any models currently in individual rate-limit cooldown
            rl_models = {m: round(until - now) for m, until in self._model_rl_until.items()
                         if until > now}

        return {
            "online": self.enabled,
            "version": "v4.4",
            "provider": "gemini",
            "active_models": len(config.WEIGHTS),  # strategy count (10)
            "latency_ms": self._last_latency,
            "inference_per_min": rate_per_min,
            "total_inferences": self._inference_count,
            "current_model": cur_model,
            "rate_limited_models": rl_models,
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

    def _post_gemini_model(self, model, system_prompt, payload_text, timeout=60):
        """Single HTTP POST to the Gemini generateContent REST endpoint.
        Returns (model, content_str) on success.
        Raises RuntimeError with a tag prefix for caller to inspect:
          - 'RATE_LIMIT:...'  → 429 received
          - 'MODEL_ERROR:...' → 400/404 model issue, try next model
          - 'AUTH_ERROR:...'  → 401/403 bad key
          - 'HTTP_ERROR:...'  → other non-200 status
        """
        key = _get_gemini_key()
        url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={key}"
        body = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": payload_text}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 700,
                "responseMimeType": "application/json",
            },
        }
        resp = requests.post(url, json=body, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            return model, content
        if resp.status_code == 429:
            raise RuntimeError(f"RATE_LIMIT:{model}: {resp.text[:120]}")
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"AUTH_ERROR: Gemini returned {resp.status_code} — your GEMINI_API_KEY "
                f"is invalid or not set. Get a free key at https://aistudio.google.com "
                f"({resp.text[:120]})"
            )
        if resp.status_code in (400, 404):
            raise RuntimeError(f"MODEL_ERROR:{model}: {resp.status_code} {resp.text[:80]}")
        raise RuntimeError(f"HTTP_ERROR:{model}: {resp.status_code} {resp.text[:160]}")

    def _call_gemini_models(self, system_prompt, payload_text):
        """Try each Gemini model in order, skipping rate-limited ones.

        Uses per-model cooldown tracking so a 429 on one model doesn't block
        the others.  The last successful model is cached and tried first next
        time.  Returns (model_name, content_str) on success.
        """
        if not _get_gemini_key():
            raise RuntimeError(
                "GEMINI_API_KEY not set — restart server.py and enter your key. "
                "Get a free key at https://aistudio.google.com"
            )

        # Build ordered candidate list: cached winner → rest
        candidates = [self._or_model] if self._or_model else []
        candidates += [m for m in GEMINI_MODELS if m and m not in candidates]

        # Skip models still in individual rate-limit cooldown
        now_t = time.time()
        models = [m for m in candidates if now_t >= self._model_rl_until.get(m, 0)]
        skipped = [m for m in candidates if m not in models]
        if skipped:
            log.info("Skipping rate-limited Gemini models: %s", skipped)
        if not models:
            raise RuntimeError("RATE_LIMIT:all Gemini models are individually rate-limited")

        last_exc = None
        for model in models:
            try:
                self._record_evt(stage="model_attempt", provider="gemini", model=model)
                result = self._post_gemini_model(model, system_prompt, payload_text)
                # Success — cache and clear individual cooldown
                self._or_model = model
                self._model_rl_until.pop(model, None)
                self._active_models.add(model)
                self._record_evt(stage="model_success", provider="gemini", model=model)
                log.info("Gemini success: model=%s", model)
                return result
            except RuntimeError as e:
                msg = str(e)
                last_exc = e
                if msg.startswith("AUTH_ERROR"):
                    raise  # bad key — no point trying other models
                if msg.startswith("RATE_LIMIT:") or msg.startswith("MODEL_ERROR:"):
                    self._model_rl_until[model] = time.time() + self._MODEL_RL_SECONDS
                    if self._or_model == model:
                        self._or_model = None
                    self._record_evt(
                        stage="model_rate_limited", provider="gemini", model=model,
                        message="Rate limited — trying next model",
                        cooldown_s=self._MODEL_RL_SECONDS,
                    )
                    log.warning("Gemini rate limit on %s — trying next", model)
                    continue
                raise  # HTTP_ERROR or other — surface immediately
            except requests.RequestException as e:
                last_exc = e
                log.warning("Gemini request error for %s: %s", model, e)
                continue

        raise RuntimeError(f"RATE_LIMIT:all Gemini models failed: {last_exc}")

    def _call_ai(self, payload_text):
        """Primary AI analyst call — Google Gemini with automatic model cycling."""
        return self._call_gemini_models(SYSTEM_PROMPT, payload_text)

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
            _, raw = self._call_gemini_models(
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
        model, raw = self._call_ai(user_text)
        latency_ms = int((time.time() - t0) * 1000)

        # Provider is always Gemini
        provider = "gemini"
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
            "provider": provider,        # "gemini"
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
            msg = str(e)
            self.last_error = msg
            is_rate_limit = msg.startswith("RATE_LIMIT:")
            if is_rate_limit:
                # All models are in cooldown — return cached data silently.
                log.warning("All Gemini models rate-limited for %s — returning cached", symbol)
            else:
                traceback.print_exc()
            cached = self.get_cached(symbol)
            if cached:
                return cached
            return {
                "symbol": symbol,
                "interval": config.AI_INTERVAL,
                "updated": int(time.time()),
                "error": msg[:200],
            }


ai_analyst = AIAnalyst()
