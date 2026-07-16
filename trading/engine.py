"""Trading Engine.

Orchestrates: data → analysis → context → AI → validate → broadcast.

Decides WHEN to call the AI based on configured triggers:
  - New 15m candle close (primary)
  - BOS or CHoCH detected on any configured timeframe
  - Liquidity sweep detected
  - High volatility expansion
  - Explicit manual call (force=True)

Enforces a minimum interval between AI calls per symbol.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from trading import config
from trading.ai.trader import ai_trader
from trading.analysis.fibonacci import compute_fibonacci
from trading.analysis.fundamentals import analyze_fundamentals
from trading.analysis.indicators import compute_all as compute_indicators
from trading.analysis.market_regime import classify_regime
from trading.analysis.market_structure import detect_bos_choch
from trading.analysis.smc import analyze_smc
from trading.analysis.support_resistance import detect_sr_levels
from trading.analysis.trendlines import fit_trendlines
from trading.analysis.volume import analyze_volume
from trading.context.builder import build_context
from trading.data import candle_cache
from trading.data.binance_rest import get_futures_stats
from trading.risk.validator import risk_validator
from trading.trade.manager import trade_manager

logger = logging.getLogger(__name__)

MAX_SIGNAL_HISTORY = 200


class Engine:
    """Main trading engine."""

    def __init__(self, ws_manager: Any | None = None) -> None:
        self._ws = ws_manager  # ConnectionManager
        self._last_ai_call: dict[str, float] = {}  # symbol -> timestamp
        self.signal_cache: dict[str, Any] = {}  # symbol -> latest signal
        self.analysis_cache: dict[str, Any] = {}  # symbol -> latest full analysis
        self.signal_history: list[Any] = []

    def set_ws_manager(self, ws_manager: Any) -> None:
        self._ws = ws_manager

    # ── Public entry points ────────────────────────────────────────────────────

    async def on_kline_close(
        self, symbol: str, timeframe: str, candle: dict[str, Any]
    ) -> None:
        """Called when a candle closes. Run full analysis; call AI if triggered."""
        trigger: str | None = None

        if timeframe == "15m":
            trigger = "15m_close"
        elif timeframe in ("1H", "4H", "1D"):
            # Always re-analyse but use slower AI gate
            trigger = f"{timeframe}_close"

        # Run local analysis (always fast — no external calls)
        analysis = await self._build_full_analysis(symbol)
        if analysis:
            self.analysis_cache[symbol] = analysis
            if self._ws:
                await self._ws.broadcast_analysis(symbol, analysis)

            # Check structural triggers
            if not trigger:
                if analysis.get("regime", {}).get("has_structure_change"):
                    trigger = "structure_change"
                elif analysis.get("1H", {}).get("has_sweep") or analysis.get("15m", {}).get("has_sweep"):
                    trigger = "liquidity_sweep"
                elif (analysis.get("regime", {}).get("volatility_expansion", 1.0) or 1.0) > config.REGIME_VOLATILITY_SPIKE:
                    trigger = "volatility"

        if trigger:
            await self._maybe_call_ai(symbol, trigger)

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        """Called on every aggTrade — update trade positions and broadcast price."""
        price = tick["price"]

        # Update active trades
        atr_val = 0.0
        candles_1m = candle_cache.get_candles(symbol, "1m")
        if candles_1m:
            from trading.analysis.helpers import atr as _atr
            atr_val = _atr(candles_1m)

        closed_trades = trade_manager.update_price(symbol, price, atr_val)
        for trade in closed_trades:
            if self._ws:
                await self._ws.broadcast_trade_event("closed", trade.to_dict())

        # Broadcast tick
        if self._ws:
            await self._ws.broadcast_tick(symbol, price)

    async def run_analysis(
        self, symbol: str, trigger: str = "manual"
    ) -> dict[str, Any]:
        """Force a full analysis + AI call. Returns the signal."""
        analysis = await self._build_full_analysis(symbol)
        if analysis:
            self.analysis_cache[symbol] = analysis
        return await self._call_ai(symbol, trigger)

    # ── Private ───────────────────────────────────────────────────────────────

    async def _maybe_call_ai(self, symbol: str, trigger: str) -> None:
        """Gate AI calls by minimum interval."""
        now = time.monotonic()
        last = self._last_ai_call.get(symbol, 0.0)
        if now - last < config.MIN_AI_CALL_INTERVAL:
            logger.debug(
                "AI call gated for %s (%.0fs since last call)",
                symbol,
                now - last,
            )
            return
        await self._call_ai(symbol, trigger)

    async def _call_ai(self, symbol: str, trigger: str) -> dict[str, Any]:
        """Run AI analysis and validate. Cache and broadcast result."""
        self._last_ai_call[symbol] = time.monotonic()

        analysis = self.analysis_cache.get(symbol)
        if not analysis:
            analysis = await self._build_full_analysis(symbol)
            if analysis:
                self.analysis_cache[symbol] = analysis

        # Build regime from analysis
        regime = analysis.get("_regime") if analysis else None
        fundamentals = analysis.get("_fundamentals") if analysis else None

        if regime and not regime.get("tradeable"):
            signal = {
                "symbol": symbol,
                "signal": "WAIT",
                "entry": None,
                "stop_loss": None,
                "take_profit_1": None,
                "take_profit_2": None,
                "confidence": 0.0,
                "htf_bias": "NEUTRAL",
                "setup_type": "",
                "reasoning": f"Market regime: {regime.get('regime')} — not tradeable",
                "invalidation": "",
                "updated": int(time.time()),
                "gated": True,
                "gate_reason": f"regime:{regime.get('regime')}",
                "trigger": trigger,
            }
        else:
            # Build compact context
            mtf = {k: v for k, v in (analysis or {}).items() if not k.startswith("_")}
            price = candle_cache.get_latest_price(symbol, "1m") or 0.0
            context = build_context(
                symbol, price, mtf, fundamentals, regime, trigger
            )
            # Call AI
            signal = await ai_trader.analyze(symbol, context)
            signal["trigger"] = trigger

            # Validate
            if signal.get("signal") != "WAIT":
                atr_val = 0.0
                c1m = candle_cache.get_candles(symbol, "1m")
                if c1m:
                    from trading.analysis.helpers import atr as _atr
                    atr_val = _atr(c1m)
                signal = risk_validator.validate(signal, price, atr_val)

        # Cache and history
        self.signal_cache[symbol] = signal
        self.signal_history.insert(0, signal)
        if len(self.signal_history) > MAX_SIGNAL_HISTORY:
            self.signal_history = self.signal_history[:MAX_SIGNAL_HISTORY]

        # Broadcast
        if self._ws:
            await self._ws.broadcast_signal(signal)

        return signal

    async def _build_full_analysis(self, symbol: str) -> dict[str, Any] | None:
        """Run all local analysis for all timeframes. Pure computation, no AI."""
        try:
            result: dict[str, Any] = {}

            # Fetch futures stats in background
            futures_task = asyncio.create_task(get_futures_stats(symbol))

            for tf in config.TIMEFRAMES:
                candles = candle_cache.get_candles(symbol, tf)
                if len(candles) < 20:
                    continue

                indicators = compute_indicators(candles)
                structure = detect_bos_choch(candles)
                smc = analyze_smc(candles)
                sr = detect_sr_levels(candles)
                tl = fit_trendlines(candles)
                fib = compute_fibonacci(candles)
                vol = analyze_volume(candles)

                result[tf] = {
                    **indicators,
                    "structure": structure,
                    "order_blocks": smc["order_blocks"],
                    "fvgs": smc["fvgs"],
                    "liquidity_sweeps": smc["liquidity_sweeps"],
                    "liquidity_pools": smc["liquidity_pools"],
                    "has_sweep": smc["has_sweep"],
                    "has_structure_change": smc["has_structure_change"],
                    "support_resistance": sr,
                    "trendlines": tl,
                    "fibonacci": fib,
                    "volume": vol,
                }

            # Await futures stats
            futures_raw = await futures_task
            fundamentals = analyze_fundamentals(futures_raw)

            # Market regime (from 1H structure)
            primary_tf = "1H" if "1H" in result else (list(result.keys())[-1] if result else None)
            regime = None
            if primary_tf:
                candles_p = candle_cache.get_candles(symbol, primary_tf)
                trend_int = result[primary_tf].get("structure", {}).get("trend_int", 0)
                regime = classify_regime(candles_p, trend_int, fundamentals)

            result["_fundamentals"] = fundamentals
            result["_regime"] = regime

            return result if result else None

        except Exception as exc:  # noqa: BLE001
            logger.error("Analysis error for %s: %s", symbol, exc, exc_info=True)
            return None
