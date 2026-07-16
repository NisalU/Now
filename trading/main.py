"""FastAPI application entry point.

Startup sequence:
  1. Pre-load candle cache from Binance REST API (all symbols + timeframes)
  2. Start Binance WebSocket stream
  3. Register WS callbacks → trigger Engine analysis
  4. Mount REST routes and WebSocket endpoint

Run:
    uvicorn trading.main:app --host 0.0.0.0 --port 8000 --reload
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from trading import config
from trading.api.routes import router, set_analysis_cache, set_engine, set_signal_cache
from trading.api.websocket import ws_endpoint, ws_manager
from trading.data import candle_cache
from trading.data.binance_ws import binance_ws
from trading.engine import Engine

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Module-level engine
engine = Engine(ws_manager=ws_manager)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown lifecycle."""
    logger.info("═══ AI Trading System starting ═══")
    logger.info("Symbols: %s", config.SYMBOLS)
    logger.info("Timeframes: %s", config.TIMEFRAMES)

    # 1. Pre-load cache from REST API
    logger.info("Pre-loading candle cache…")
    await candle_cache.preload_all()

    # 2. Configure WebSocket
    binance_ws.set_symbols(config.SYMBOLS)
    binance_ws.register_kline_close_callback(engine.on_kline_close)
    binance_ws.register_tick_callback(engine.on_tick)

    # 3. Start WebSocket stream
    await binance_ws.start()
    logger.info("Binance WebSocket stream started")

    # 4. Wire engine into routes
    set_engine(engine)
    set_signal_cache(engine.signal_cache)
    set_analysis_cache(engine.analysis_cache)

    logger.info("═══ AI Trading System ready ═══")
    yield

    # Shutdown
    logger.info("Shutting down…")
    await binance_ws.stop()
    logger.info("Goodbye")


app = FastAPI(
    title="AI Crypto Trading System",
    version="1.0.0",
    description="Production AI cryptocurrency trading with SMC analysis",
    lifespan=lifespan,
)

# CORS — allow the React dev server and deployed frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST routes
app.include_router(router, prefix="/api")


# WebSocket endpoint
@app.websocket("/ws")
async def websocket_route(ws: WebSocket) -> None:
    await ws_endpoint(ws, ws_manager)


# Serve React build output in production (when dist/ exists)
_dist = os.path.join(os.path.dirname(__file__), "..", "artifacts", "trading-dashboard", "dist")
if os.path.isdir(_dist):
    from fastapi.responses import FileResponse

    @app.get("/{path:path}")
    async def serve_frontend(path: str) -> FileResponse:
        file_path = os.path.join(_dist, path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(_dist, "index.html"))

    logger.info("Serving React build from %s", _dist)
