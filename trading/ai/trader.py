"""Groq AI Trader.

Single model, single call. Returns LONG | SHORT | WAIT with entry, SL, TP.
No critic. No second opinion. No confluence score.
The AI receives compact multi-timeframe context and decides.

Call conditions (enforced by the engine, not here):
  - New 15m candle closes
  - BOS or CHoCH detected
  - Liquidity sweep
  - High volatility event
  - Min interval respected (default 60s per symbol)
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from groq import AsyncGroq, APIError, RateLimitError

from trading import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a professional cryptocurrency trader specialising in Smart Money Concepts (SMC) and multi-timeframe analysis.

TIMEFRAME PRIORITY (top-down confluence):
  1D  → Market cycle — is the macro trend bullish or bearish?
  4H  → Primary trend — what direction is the dominant flow?
  2H  → Momentum — is momentum supporting the trend?
  1H  → Bias — intraday directional bias
  30m → Setup — is a valid setup forming?
  15m → Entry — is the entry condition met?
  5m  → Confirmation — does lower TF confirm?
  1m  → Precision — exact timing

DECISION RULES:
- LONG only when: HTF trend bullish, price at valid OB/FVG/S or golden Fib zone, structure intact, RR ≥ 2.0
- SHORT only when: HTF trend bearish, price at valid supply/resistance, structure confirming, RR ≥ 2.0
- WAIT when: conflicting timeframes, no clear entry level, chasing price, insufficient RR, or regime not tradeable

ENTRY PLACEMENT: at order block, FVG midpoint, key S/R, VWAP, or Fibonacci 0.5/0.618
STOP LOSS: beyond the last swing point / OB invalidation level
TAKE PROFIT: at next liquidity pool, resistance/support, or swing extreme
MIN RR: 2.0 to TP1 required. TP2 at 3–4× RR.

OUTPUT FORMAT — return ONLY valid JSON, no markdown, no prose:
{
  "signal": "LONG" | "SHORT" | "WAIT",
  "entry": <number | null>,
  "stop_loss": <number | null>,
  "take_profit_1": <number | null>,
  "take_profit_2": <number | null>,
  "confidence": <0.0–1.0>,
  "htf_bias": "LONG" | "SHORT" | "NEUTRAL",
  "setup_type": "<brief setup name, e.g. OB reclaim + BOS>",
  "reasoning": "<≤200 chars — key confluences driving decision>",
  "invalidation": "<what would invalidate this — price level or condition>"
}
If WAIT, set entry/stop/tp to null and confidence < 0.5."""


class AITrader:
    """Single Groq model AI trader."""

    def __init__(self) -> None:
        self._client: AsyncGroq | None = None
        self._current_model = config.GROQ_MODEL
        self._calls_total = 0
        self._last_call_ms: int | None = None
        self._last_error: str | None = None
        self.enabled = bool(config.GROQ_API_KEY)

        if self.enabled:
            self._client = AsyncGroq(api_key=config.GROQ_API_KEY)
            logger.info("AI Trader enabled — model: %s", self._current_model)
        else:
            logger.warning("GROQ_API_KEY not set — AI Trader disabled")

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "model": self._current_model,
            "last_call_ms": self._last_call_ms,
            "calls_total": self._calls_total,
            "error": self._last_error,
        }

    async def analyze(
        self, symbol: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Call Groq with the market context. Returns structured signal dict.

        Tries fallback models on rate-limit errors.
        Never raises — returns a WAIT signal on any failure.
        """
        if not self.enabled or self._client is None:
            return self._wait_signal(symbol, reason="AI disabled — GROQ_API_KEY not set")

        context_json = json.dumps(context, separators=(",", ":"))
        prompt = (
            f"Analyze this market context and return your trading decision as JSON:\n\n"
            f"{context_json}"
        )

        models_to_try = [self._current_model] + [
            m for m in config.GROQ_FALLBACK_MODELS if m != self._current_model
        ]

        for model in models_to_try:
            try:
                result = await self._call_groq(symbol, prompt, model)
                self._current_model = model  # stick with working model
                return result
            except RateLimitError:
                logger.warning("Rate limit on model %s — trying next", model)
                continue
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)[:200]
                logger.error("AI call failed on %s: %s", model, exc)
                continue

        self._last_error = "All Groq models rate-limited or failed"
        return self._wait_signal(symbol, reason=self._last_error)

    async def _call_groq(
        self, symbol: str, prompt: str, model: str
    ) -> dict[str, Any]:
        """Make the actual Groq API call."""
        assert self._client is not None
        t0 = time.monotonic()

        response = await self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=config.AI_MAX_TOKENS,
            temperature=config.AI_TEMPERATURE,
            timeout=config.AI_TIMEOUT,
        )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        self._last_call_ms = elapsed_ms
        self._calls_total += 1
        self._last_error = None

        raw_text = response.choices[0].message.content or ""
        logger.info(
            "AI call [%s] model=%s latency=%dms tokens=%d",
            symbol,
            model,
            elapsed_ms,
            response.usage.total_tokens if response.usage else 0,
        )

        parsed = self._parse_response(raw_text, symbol)
        parsed["ai_latency_ms"] = elapsed_ms
        parsed["model_used"] = model
        return parsed

    def _parse_response(
        self, text: str, symbol: str
    ) -> dict[str, Any]:
        """Extract and validate JSON from the model response."""
        # Strip markdown code fences if present
        text = re.sub(r"```(?:json)?\s*", "", text).strip()

        # Find the JSON object
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            logger.warning("No JSON found in AI response for %s: %s", symbol, text[:200])
            return self._wait_signal(symbol, reason="No JSON in AI response")

        try:
            data = json.loads(match.group())
        except json.JSONDecodeError as exc:
            logger.warning("JSON parse error for %s: %s", symbol, exc)
            return self._wait_signal(symbol, reason=f"JSON parse error: {exc}")

        signal = str(data.get("signal", "WAIT")).upper()
        if signal not in ("LONG", "SHORT", "WAIT"):
            signal = "WAIT"

        def _num(key: str) -> float | None:
            v = data.get(key)
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        return {
            "symbol": symbol,
            "signal": signal,
            "entry": _num("entry"),
            "stop_loss": _num("stop_loss"),
            "take_profit_1": _num("take_profit_1"),
            "take_profit_2": _num("take_profit_2"),
            "confidence": min(1.0, max(0.0, float(data.get("confidence", 0.0)))),
            "htf_bias": str(data.get("htf_bias", "NEUTRAL")).upper(),
            "setup_type": str(data.get("setup_type", ""))[:100],
            "reasoning": str(data.get("reasoning", ""))[:300],
            "invalidation": str(data.get("invalidation", ""))[:200],
            "updated": int(time.time()),
            "gated": False,
            "gate_reason": None,
        }

    @staticmethod
    def _wait_signal(symbol: str, reason: str = "") -> dict[str, Any]:
        return {
            "symbol": symbol,
            "signal": "WAIT",
            "entry": None,
            "stop_loss": None,
            "take_profit_1": None,
            "take_profit_2": None,
            "confidence": 0.0,
            "htf_bias": "NEUTRAL",
            "setup_type": "",
            "reasoning": reason,
            "invalidation": "",
            "updated": int(time.time()),
            "gated": bool(reason),
            "gate_reason": reason or None,
            "ai_latency_ms": None,
            "model_used": None,
        }


# Module-level singleton
ai_trader = AITrader()
