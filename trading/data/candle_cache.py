"""Multi-timeframe candle cache.

Stores the most recent KLINE_LIMIT candles per (symbol, timeframe) pair.
The cache is updated by the WebSocket stream as new candles close, and
pre-loaded from the REST API on startup.

Thread-safe via asyncio.Lock (all access is from the same event loop).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

from trading import config

logger = logging.getLogger(__name__)

# Cache: (symbol, timeframe) -> deque of candle dicts
_cache: dict[tuple[str, str], deque[dict[str, Any]]] = {}
_updated_at: dict[tuple[str, str], float] = {}  # last update timestamp
_lock = asyncio.Lock()

MAX_CANDLES = config.KLINE_LIMIT


async def set_candles(
    symbol: str, timeframe: str, candles: list[dict[str, Any]]
) -> None:
    """Replace the entire candle list for a (symbol, timeframe) pair."""
    key = (symbol, timeframe)
    async with _lock:
        _cache[key] = deque(candles[-MAX_CANDLES:], maxlen=MAX_CANDLES)
        _updated_at[key] = time.monotonic()
    logger.debug("Cache set: %s %s (%d candles)", symbol, timeframe, len(candles))


async def update_candle(
    symbol: str, timeframe: str, candle: dict[str, Any], closed: bool
) -> bool:
    """Update or append the latest candle. Returns True if a new candle closed."""
    key = (symbol, timeframe)
    async with _lock:
        if key not in _cache:
            return False

        buf = _cache[key]
        new_candle_opened = False

        if buf and buf[-1]["time"] == candle["time"]:
            # Same candle: update in-place (live tick update)
            buf[-1] = candle
        else:
            # New candle opened
            buf.append(candle)
            new_candle_opened = True

        _updated_at[key] = time.monotonic()

    if closed and new_candle_opened:
        logger.debug("New %s %s candle closed @ %.2f", symbol, timeframe, candle["close"])
    return closed and new_candle_opened


def get_candles(symbol: str, timeframe: str) -> list[dict[str, Any]]:
    """Synchronous read — safe because Python list ops are atomic."""
    key = (symbol, timeframe)
    buf = _cache.get(key)
    if buf is None:
        return []
    return list(buf)


def get_latest_price(symbol: str, timeframe: str = "1m") -> float | None:
    """Return the current close price from the 1m cache."""
    candles = get_candles(symbol, timeframe)
    if not candles:
        return None
    return candles[-1]["close"]


def get_age_seconds(symbol: str, timeframe: str) -> float:
    """How long ago the cache was last updated, in seconds."""
    key = (symbol, timeframe)
    updated = _updated_at.get(key)
    if updated is None:
        return float("inf")
    return time.monotonic() - updated


def is_stale(symbol: str, timeframe: str, max_age: float = 60.0) -> bool:
    return get_age_seconds(symbol, timeframe) > max_age


async def preload(symbol: str, timeframes: list[str] | None = None) -> None:
    """Fetch all timeframes from REST API and populate the cache."""
    from trading.data.binance_rest import get_klines_multi

    tfs = timeframes or config.TIMEFRAMES
    logger.info("Pre-loading cache for %s: %s", symbol, tfs)
    data = await get_klines_multi(symbol, tfs)
    for tf, candles in data.items():
        if candles:
            await set_candles(symbol, tf, candles)
    logger.info("Cache pre-loaded for %s (%d timeframes)", symbol, len(data))


async def preload_all(symbols: list[str] | None = None) -> None:
    """Pre-load all symbols concurrently."""
    syms = symbols or config.SYMBOLS
    await asyncio.gather(*[preload(s) for s in syms])
