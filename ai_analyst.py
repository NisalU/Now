"""AI analyst — discretionary structure/liquidity read on top of the
confluence engine.

Pipeline (no regime or trade-quality gates — every bar gets a full AI read):

    Market data (engine.py)
        -> Signal memory context (signal_memory.py)     -- past similar setups
        -> Primary AI analyst (Groq, single SYSTEM_PROMPT for all models)
        -> Server-side risk gate (_apply_risk_gate)      -- re-checks the math
        -> AI critic (second opinion)                    -- tries to kill it
        -> Signal memory write

Provider:
    Groq REST API (OpenAI-compatible /chat/completions), GROQ_API_KEY.
    Models tried in order with per-model rate-limit cooldown. Cooldowns are
    driven by the `retry-after` header Groq sends on 429 responses (falling
    back to MODEL_RL_COOLDOWN if that header is absent), and a global
    min-interval throttle spaces out requests so a single hot symbol/loop
    doesn't blow through the per-model RPM limit on its own.

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
# Provider — Groq REST API (OpenAI-compatible chat/completions)
# ---------------------------------------------------------------------------
GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"

# Models tried in order; first success is cached for the session.
# When a model returns 429 it is individually rate-limited (using the
# `retry-after` header when Groq sends one, else MODEL_RL_COOLDOWN) and the
# next one is tried automatically. All four below support JSON mode.
#
# NOTE: llama-3.1-8b-instant and llama-3.3-70b-versatile are on Groq's
# deprecation list (shutdown 08/16/26) — swap them out for their announced
# replacements (openai/gpt-oss-20b / openai/gpt-oss-120b, already listed
# below) once that date passes if Groq hasn't auto-upgraded the IDs.
GROQ_MODELS = [
    m for m in [
        os.environ.get("GROQ_MODEL", ""),          # user-pinned model (highest priority)
        "openai/gpt-oss-120b",                     # strong general reasoning, 30 RPM / 8K TPM free
        "llama-3.3-70b-versatile",                 # solid fallback, 30 RPM / 12K TPM free
        "openai/gpt-oss-20b",                      # fast, 30 RPM / 8K TPM free
        "llama-3.1-8b-instant",                    # lightest/highest-RPD fallback
    ] if m
]

# ---------------------------------------------------------------------------
# Prompt payload sizing — kept small to reduce prompt tokens (leaves more of
# the per-model TPM budget for the completion) and to lower the odds of the
# completion itself getting truncated. Overridable via config.py.
# ---------------------------------------------------------------------------
PROMPT_CANDLE_COUNT = getattr(config, "AI_PROMPT_CANDLES", 6)
PROMPT_CVD_POINTS = getattr(config, "AI_PROMPT_CVD_POINTS", 12)
PROMPT_MEMORY_ROWS = getattr(config, "AI_PROMPT_MEMORY_ROWS", 3)

# ---------------------------------------------------------------------------
# Primary system prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an expert cryptocurrency trader.

Analyze the provided market data using multi-timeframe analysis.

Timeframes:
- 1D: Overall market cycle and major trend.
- 4H: Primary trend and key support/resistance.
- 2H: Trend confirmation and momentum.
- 1H: Market bias and liquidity zones.
- 30m: Setup development and structure.
- 5m: Entry confirmation.
- 1m: Precise entry timing if needed.
- 15m: trade confirmation, entry ,stop loss, take profit,

Rules:
- Analyze the 1D,4H,2H,1H,30m,5m,1m chart first, then the 15m chart.
- The 1H bias has priority.
- The 15m is for execution only.
- Price action and market structure are more important than indicators.
- Use indicators only as supporting evidence.
- Never calculate or mention confluence scores.
- Never force a trade.
- If evidence conflicts or the setup is weak, return WAIT.
- Trade only high-probability setups with Risk:Reward ≥ 2.
- Do not invent information not present in the input.

Return ONLY valid JSON:

{
  "decision":"LONG|SHORT|WAIT",
  "confidence":0-100,
  "entry":null,
  "stop_loss":null,
  "take_profit":[],
  "reason":"Brief explanation under 40 words."
}

Confidence:
95-100 = Exceptional
90-94 = High probability
85-89 = Good setup
<85 = WAIT"""


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


def _get_groq_key():
    """Read GROQ_API_KEY from environment."""
    return os.environ.get("GROQ_API_KEY", "").strip()


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
        for c in candles[-PROMPT_CANDLE_COUNT:]
    ]

    cvd = ov.get("cvd") or []
    cvd_tail = [_fnum(p["value"], 2) for p in cvd[-PROMPT_CVD_POINTS:]]

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
        "recent_similar_setups": memory_rows[:PROMPT_MEMORY_ROWS],
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


def _repair_truncated_json(text):
    """Best-effort repair for a JSON object that was cut off mid-generation
    (the completion hit max_tokens before the model could close it out).
    Handles the common truncation artifacts: an unterminated string,
    unbalanced braces/brackets, and a dangling trailing comma/colon.
    Returns a repaired string, or None if there's nothing salvageable
    (no opening brace at all)."""
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    s = text[start:]

    in_string = False
    escape = False
    stack = []
    for ch in s:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()

    if in_string:
        s += '"'

    s = s.rstrip().rstrip(",")
    if s.endswith(":"):
        s += "null"

    closers = {"{": "}", "[": "]"}
    while stack:
        s += closers[stack.pop()]

    return s


def _try_parse_json(content):
    """Try to parse `content` as JSON as-is; if that fails, attempt the
    truncation repair above and retry once. Returns
    (parsed_dict_or_None, repaired_str_or_None) — repaired_str is only
    set when a repair was needed and it produced valid JSON."""
    try:
        return json.loads(content), None
    except (ValueError, TypeError):
        pass
    repaired = _repair_truncated_json(content)
    if repaired is None:
        return None, None
    try:
        return json.loads(repaired), repaired
    except (ValueError, TypeError):
        return None, None


# ---------------------------------------------------------------------------
# Main analyst class
# ---------------------------------------------------------------------------

class AIAnalyst:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache = {}          # symbol -> ai result dict
        self._or_model = None     # last successful Groq model (tried first next run)
        self.enabled = bool(_get_groq_key())
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

        # Global request throttle — spaces out consecutive Groq calls so a
        # tight polling loop across many symbols doesn't blow through the
        # per-model RPM limit (free tier is 30 RPM on most models, i.e. one
        # request every 2s sustained). This is on top of, not instead of,
        # the per-model cooldown above.
        self._rate_lock = threading.Lock()
        self._last_call_ts = 0.0
        self._MIN_CALL_INTERVAL = getattr(config, "AI_MIN_CALL_INTERVAL", 2.1)

        # Completion token budget. Start at MAX_TOKENS_PRIMARY; if a
        # response comes back truncated (finish_reason == "length" or
        # Groq's json_validate_failed error) retry the SAME model once at
        # MAX_TOKENS_RETRY before giving up on it.
        self._MAX_TOKENS_PRIMARY = getattr(config, "AI_MAX_TOKENS", 2000)
        self._MAX_TOKENS_RETRY = getattr(config, "AI_MAX_TOKENS_RETRY", 3000)

        # Short cooldown applied to a model that produced unparseable/
        # unrepairable JSON even after the token-budget retry. Deliberately
        # shorter than _MODEL_RL_SECONDS (real rate limits) so a model that
        # just had a bad output isn't punished as hard as a 429, but it
        # still won't be retried on the very next call, which is what was
        # letting one flaky model get cached as `_or_model` and "poison"
        # every subsequent request until it happened to behave.
        self._JSON_FAIL_COOLDOWN = getattr(config, "AI_JSON_FAIL_COOLDOWN", 30)

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
            "version": "v4.5",
            "provider": "groq",
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

    def _throttle(self):
        """Global min-interval spacing between outgoing Groq requests.

        Free-tier Groq models cap at 30 RPM (~1 request / 2s sustained). If
        several symbols get analyzed back-to-back in a tight loop, spacing
        calls out here avoids tripping 429s in the first place instead of
        just reacting to them after the fact."""
        with self._rate_lock:
            now = time.time()
            wait = self._MIN_CALL_INTERVAL - (now - self._last_call_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_call_ts = time.time()

    @staticmethod
    def _parse_retry_after(resp) -> float:
        """Return seconds to wait before retrying, from the `retry-after`
        header Groq sends on 429s. Falls back to 0 (caller applies its own
        default cooldown) if the header is missing or unparseable."""
        raw = resp.headers.get("retry-after")
        if not raw:
            return 0.0
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return 0.0

    def _post_groq_model(self, model, system_prompt, payload_text, timeout=60, max_tokens=2000):
        """Single HTTP POST to Groq's OpenAI-compatible chat/completions
        endpoint. Returns (model, content_str, finish_reason) on success.
        Raises RuntimeError with a tag prefix for caller to inspect:
          - 'RATE_LIMIT:<seconds>:...' → 429 received; <seconds> is the
             retry-after value from Groq's header (0 if absent)
          - 'TRUNCATED:...'   → Groq rejected/cut the JSON for length
             (json_validate_failed or an explicit max-tokens error) —
             caller retries the same model at a higher token budget
          - 'MODEL_ERROR:...' → other 400/404 model issue, try next model
          - 'AUTH_ERROR:...'  → 401/403 bad key
          - 'HTTP_ERROR:...'  → other non-200 status
        """
        key = _get_groq_key()
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": payload_text},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        self._throttle()
        resp = requests.post(GROQ_BASE_URL, json=body, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
            return model, content, finish_reason
        # Always print the real error so it can be diagnosed
        print(f"[groq] {model} HTTP {resp.status_code}: {resp.text[:300]}")
        if resp.status_code == 429:
            retry_after = self._parse_retry_after(resp)
            raise RuntimeError(f"RATE_LIMIT:{retry_after}:{model}: {resp.text[:120]}")
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"AUTH_ERROR: Groq returned {resp.status_code} — your GROQ_API_KEY "
                f"is invalid or not set. Get a free key at https://console.groq.com/keys "
                f"({resp.text[:120]})"
            )
        if resp.status_code == 400:
            try:
                err = resp.json().get("error", {}) or {}
            except ValueError:
                err = {}
            code = str(err.get("code") or "")
            err_msg = str(err.get("message") or "").lower()
            if code == "json_validate_failed" or "max_tokens" in err_msg or "max completion tokens" in err_msg:
                raise RuntimeError(f"TRUNCATED:{model}: {resp.text[:160]}")
            raise RuntimeError(f"MODEL_ERROR:{model}: {resp.status_code} {resp.text[:80]}")
        if resp.status_code == 404:
            raise RuntimeError(f"MODEL_ERROR:{model}: {resp.status_code} {resp.text[:80]}")
        raise RuntimeError(f"HTTP_ERROR:{model}: {resp.status_code} {resp.text[:160]}")

    def _post_with_truncation_retry(self, model, system_prompt, payload_text):
        """POST to `model`, automatically retrying once at a higher token
        budget (_MAX_TOKENS_RETRY) if the response looks truncated —
        either Groq explicitly rejected it (json_validate_failed / max
        tokens error) or it came back 200 with finish_reason == "length".
        If the content still isn't valid JSON, attempts the JSON-repair
        heuristic before giving up.

        Returns (content_str, ok_bool). `ok_bool` is False only if the
        content could not be parsed or repaired into valid JSON even
        after the retry — the caller treats that as a soft failure and
        moves on to the next model rather than raising. Real transport
        failures (rate limit / auth / model-not-found / other HTTP
        errors) still raise RuntimeError as before.
        """
        token_budgets = (self._MAX_TOKENS_PRIMARY, self._MAX_TOKENS_RETRY)
        content = None
        for i, max_tokens in enumerate(token_budgets):
            try:
                _, content, finish_reason = self._post_groq_model(
                    model, system_prompt, payload_text, max_tokens=max_tokens
                )
            except RuntimeError as e:
                if str(e).startswith("TRUNCATED:") and i == 0:
                    log.info(
                        "Groq %s: truncated/json_validate_failed at max_tokens=%d — "
                        "retrying at %d", model, max_tokens, self._MAX_TOKENS_RETRY,
                    )
                    continue
                raise

            parsed, repaired = _try_parse_json(content)
            if parsed is not None:
                return (repaired if repaired is not None else content), True

            if finish_reason == "length" and i == 0:
                log.info(
                    "Groq %s: response truncated (finish_reason=length) at "
                    "max_tokens=%d — retrying at %d",
                    model, max_tokens, self._MAX_TOKENS_RETRY,
                )
                continue

            # Unparseable and not (or no longer) a retryable truncation —
            # give up on this model for this call.
            return content, False

        return content, False

    def _call_groq_models(self, system_prompt, payload_text):
        """Try each Groq model in order, skipping rate-limited ones.

        Uses per-model cooldown tracking so a 429 on one model doesn't block
        the others. Cooldown length prefers Groq's `retry-after` header
        (exact, usually just a few seconds) and only falls back to the fixed
        MODEL_RL_COOLDOWN if that header wasn't sent. The last successful
        model is cached and tried first next time.  Returns
        (model_name, content_str) on success.
        """
        if not _get_groq_key():
            raise RuntimeError(
                "GROQ_API_KEY not set — restart server.py and enter your key. "
                "Get a free key at https://console.groq.com/keys"
            )

        # Build ordered candidate list: cached winner → rest
        candidates = [self._or_model] if self._or_model else []
        candidates += [m for m in GROQ_MODELS if m and m not in candidates]

        # Skip models still in individual rate-limit cooldown
        now_t = time.time()
        models = [m for m in candidates if now_t >= self._model_rl_until.get(m, 0)]
        skipped = [m for m in candidates if m not in models]
        if skipped:
            log.info("Skipping rate-limited Groq models: %s", skipped)
        if not models:
            raise RuntimeError("RATE_LIMIT:all Groq models are individually rate-limited")

        last_exc = None
        for model in models:
            prompt = system_prompt
            try:
                self._record_evt(stage="model_attempt", provider="groq", model=model)
                content, ok = self._post_with_truncation_retry(model, prompt, payload_text)
                if not ok:
                    # Valid HTTP response, but the JSON was unparseable/
                    # unrepairable even after the higher-token-budget
                    # retry. Don't cache this model as `_or_model` — that
                    # would make every subsequent call retry the same
                    # broken model first. Give it a short cooldown instead
                    # and move on to the next candidate.
                    self._model_rl_until[model] = time.time() + self._JSON_FAIL_COOLDOWN
                    if self._or_model == model:
                        self._or_model = None
                    self._record_evt(
                        stage="model_rate_limited", provider="groq", model=model,
                        message="Unparseable JSON after retry — trying next model",
                        cooldown_s=self._JSON_FAIL_COOLDOWN,
                    )
                    log.warning("Groq %s: unparseable JSON after retry, trying next model", model)
                    last_exc = RuntimeError(f"invalid JSON from {model}")
                    continue
                # Success — cache and clear individual cooldown
                self._or_model = model
                self._model_rl_until.pop(model, None)
                self._active_models.add(model)
                self._record_evt(stage="model_success", provider="groq", model=model)
                log.info("Groq success: model=%s", model)
                return model, content
            except RuntimeError as e:
                msg = str(e)
                last_exc = e
                if msg.startswith("AUTH_ERROR"):
                    raise  # bad key — no point trying other models
                if msg.startswith("RATE_LIMIT:"):
                    # Format: RATE_LIMIT:<retry_after_seconds>:<rest>
                    parts = msg.split(":", 2)
                    retry_after = 0.0
                    if len(parts) >= 2:
                        try:
                            retry_after = float(parts[1])
                        except ValueError:
                            retry_after = 0.0
                    cooldown = retry_after if retry_after > 0 else self._MODEL_RL_SECONDS
                    self._model_rl_until[model] = time.time() + cooldown
                    if self._or_model == model:
                        self._or_model = None
                    self._record_evt(
                        stage="model_rate_limited", provider="groq", model=model,
                        message="Rate limited — trying next model",
                        cooldown_s=cooldown, from_retry_after=bool(retry_after > 0),
                    )
                    log.debug("Groq rate limit on %s — cooldown %.1fs, trying next", model, cooldown)
                    continue
                if msg.startswith("MODEL_ERROR:"):
                    self._model_rl_until[model] = time.time() + self._MODEL_RL_SECONDS
                    if self._or_model == model:
                        self._or_model = None
                    self._record_evt(
                        stage="model_rate_limited", provider="groq", model=model,
                        message="Model error — trying next model",
                        cooldown_s=self._MODEL_RL_SECONDS,
                    )
                    log.debug("Groq model error on %s — trying next", model)
                    continue
                raise  # HTTP_ERROR or other — surface immediately
            except requests.RequestException as e:
                last_exc = e
                log.warning("Groq request error for %s: %s", model, e)
                continue

        raise RuntimeError(f"RATE_LIMIT:all Groq models failed: {last_exc}")

    def _call_ai(self, payload_text):
        """Primary AI analyst call — Groq with automatic model cycling."""
        return self._call_groq_models(SYSTEM_PROMPT, payload_text)

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
            _, raw = self._call_groq_models(
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

        # Provider is always Groq
        provider = "groq"
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

        # SYSTEM_PROMPT's schema is: decision, confidence, entry, stop_loss,
        # take_profit (array), reason. Map it onto the internal result dict
        # (which the rest of the pipeline — risk gate, critic, trade_quality,
        # signal memory, dashboard — expects) so nothing downstream changes.
        signal = str(out.get("decision", "WAIT")).upper()
        if signal not in ("LONG", "SHORT", "WAIT"):
            signal = "WAIT"

        def num(v):
            try:
                return round(float(v), 8) if v is not None else None
            except (TypeError, ValueError):
                return None

        take_profit = out.get("take_profit") or []
        if not isinstance(take_profit, list):
            take_profit = [take_profit]
        tp1 = num(take_profit[0]) if len(take_profit) > 0 else None
        tp2 = num(take_profit[1]) if len(take_profit) > 1 else None

        entry = num(out.get("entry"))
        stop = num(out.get("stop_loss"))
        risk_reward = None
        if entry is not None and stop is not None and tp1 is not None and abs(entry - stop) > 0:
            risk_reward = round(abs(tp1 - entry) / abs(entry - stop), 2)

        htf = market.get("higher_timeframe") or {}

        result = {
            "symbol": symbol,
            "interval": config.AI_INTERVAL,
            "updated": int(time.time()),
            "price": analysis["price"],
            "engine_score": analysis["composite"],
            "model": model,
            "model_used": model,         # explicit alias for dashboard
            "provider": provider,        # "groq"
            "signal": signal,
            "direction": signal if signal in ("LONG", "SHORT") else None,
            "setup_type": "none",
            "confidence": max(0, min(100, int(out.get("confidence") or 0))),
            "entry": entry,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "risk_reward": risk_reward,
            "orderflow_read": "",
            "reasoning": str(out.get("reason") or "")[:600],
            "invalidation": "",
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
                log.warning("All Groq models rate-limited for %s — returning cached", symbol)
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
