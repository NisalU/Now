"""FastAPI REST routes.

All routes are registered on a single APIRouter and mounted in main.py.
The analysis engine is called lazily — routes read from caches, not from
live Binance calls (which are done by the background engine).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from trading import config
from trading.data import candle_cache
from trading.trade.manager import trade_manager

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level state injected by main.py
_engine: Any = None  # trading.engine.Engine
_signal_cache: dict[str, Any] = {}  # symbol -> latest signal
_analysis_cache: dict[str, Any] = {}  # symbol -> latest analysis


def set_engine(engine: Any) -> None:
    global _engine
    _engine = engine


def set_signal_cache(cache: dict[str, Any]) -> None:
    global _signal_cache
    _signal_cache = cache


def set_analysis_cache(cache: dict[str, Any]) -> None:
    global _analysis_cache
    _analysis_cache = cache


# ── Health / Status ───────────────────────────────────────────────────────────

@router.get("/healthz", tags=["system"])
async def health():
    return {"status": "ok", "ts": int(time.time())}


@router.get("/status", tags=["system"])
async def status():
    from trading.data.binance_ws import binance_ws
    from trading.ai.trader import ai_trader
    from trading.api.websocket import ws_manager

    ws_stats = binance_ws.stats
    return {
        "symbols": config.SYMBOLS,
        "timeframes": config.TIMEFRAMES,
        "ws": ws_stats,
        "ai": ai_trader.stats,
        "clients": ws_manager.count,
        "active_trades": len(trade_manager.get_active()),
        "cached_signals": len(_signal_cache),
        "uptime_ts": int(time.time()),
    }


# ── Market data ───────────────────────────────────────────────────────────────

@router.get("/symbols", tags=["market"])
async def get_symbols():
    from trading.data.binance_rest import get_ticker
    out = []
    for sym in config.SYMBOLS:
        try:
            ticker = await get_ticker(sym)
            out.append({"symbol": sym, **ticker})
        except Exception:
            out.append({"symbol": sym})
    return out


@router.get("/candles/{symbol}/{timeframe}", tags=["market"])
async def get_candles(
    symbol: str,
    timeframe: str,
    limit: int = Query(200, le=500, ge=1),
):
    sym = symbol.upper()
    if sym not in config.SYMBOLS:
        raise HTTPException(404, f"Symbol {sym} not configured")
    if timeframe not in config.TIMEFRAMES:
        raise HTTPException(404, f"Timeframe {timeframe} not supported")

    candles = candle_cache.get_candles(sym, timeframe)
    if not candles:
        raise HTTPException(503, "Cache not yet populated — please wait")

    return candles[-limit:]


# ── Signals ───────────────────────────────────────────────────────────────────

@router.get("/signal/{symbol}", tags=["signals"])
async def get_signal(symbol: str, force: bool = Query(False)):
    """Get the latest signal for a symbol, optionally force a fresh AI call."""
    sym = symbol.upper()
    if sym not in config.SYMBOLS:
        raise HTTPException(404, f"Symbol {sym} not configured")

    if force and _engine:
        try:
            signal = await _engine.run_analysis(sym, trigger="manual")
            return signal
        except Exception as exc:
            logger.error("Forced analysis failed: %s", exc)
            raise HTTPException(500, str(exc)) from exc

    cached = _signal_cache.get(sym)
    if cached is None:
        raise HTTPException(503, "No signal cached yet — analysis in progress")

    return cached


@router.get("/signals/history", tags=["signals"])
async def get_signal_history(
    limit: int = Query(50, le=200),
    symbol: str | None = Query(None),
):
    """Return recent signal records from all symbols."""
    if _engine is None:
        return []
    history: list[Any] = _engine.signal_history
    if symbol:
        sym = symbol.upper()
        history = [s for s in history if s.get("symbol") == sym]
    return history[:limit]


# ── Analysis ──────────────────────────────────────────────────────────────────

@router.get("/analysis/{symbol}", tags=["analysis"])
async def get_analysis(symbol: str):
    """Return the full multi-timeframe analysis for a symbol."""
    sym = symbol.upper()
    if sym not in config.SYMBOLS:
        raise HTTPException(404, f"Symbol {sym} not configured")

    cached = _analysis_cache.get(sym)
    if cached is None:
        raise HTTPException(503, "Analysis not yet available")
    return cached


@router.get("/market-regime/{symbol}", tags=["analysis"])
async def get_market_regime(symbol: str):
    sym = symbol.upper()
    if sym not in config.SYMBOLS:
        raise HTTPException(404, f"Symbol {sym} not configured")

    analysis = _analysis_cache.get(sym)
    if analysis is None:
        raise HTTPException(503, "Analysis not yet available")
    return analysis.get("regime", {"regime": "unknown"})


# ── Trades ────────────────────────────────────────────────────────────────────

@router.get("/trades", tags=["trades"])
async def get_active_trades():
    return trade_manager.get_active()


@router.get("/trades/history", tags=["trades"])
async def get_trade_history(limit: int = Query(50, le=200)):
    return trade_manager.get_history(limit)


@router.post("/trades/{trade_id}/close", tags=["trades"])
async def close_trade(trade_id: str, price: float | None = Query(None)):
    trade = trade_manager.close_trade(trade_id, price)
    if trade is None:
        raise HTTPException(404, f"Trade {trade_id} not found")
    return trade.to_dict()


# ── Performance ───────────────────────────────────────────────────────────────

@router.get("/performance", tags=["performance"])
async def get_performance():
    return trade_manager.get_performance()
