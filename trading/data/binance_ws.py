"""Binance WebSocket stream manager.

Subscribes to aggTrade and kline streams for all active symbols.
Auto-reconnects with exponential back-off. Triggers AI analysis
when meaningful market events are detected (15m close, BOS, etc.).

One persistent connection handles all subscriptions via the combined
stream endpoint (/stream?streams=...).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Coroutine

import websockets
from websockets.exceptions import ConnectionClosed

from trading import config
from trading.data import candle_cache

logger = logging.getLogger(__name__)

# Callbacks registered by the application layer
OnTickCallback = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]
OnKlineCloseCallback = Callable[
    [str, str, dict[str, Any]], Coroutine[Any, Any, None]
]


class BinanceWebSocket:
    """Manages a single multiplexed Binance WebSocket connection."""

    def __init__(self) -> None:
        self._symbols: set[str] = set()
        self._connected = False
        self._endpoint: str = ""
        self._reconnects = 0
        self._last_msg_at: float = 0.0
        self._task: asyncio.Task[None] | None = None

        # External callbacks
        self._on_tick: OnTickCallback | None = None
        self._on_kline_close: OnKlineCloseCallback | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def register_tick_callback(self, cb: OnTickCallback) -> None:
        self._on_tick = cb

    def register_kline_close_callback(self, cb: OnKlineCloseCallback) -> None:
        self._on_kline_close = cb

    def set_symbols(self, symbols: list[str]) -> None:
        self._symbols = set(symbols)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "connected": self._connected,
            "endpoint": self._endpoint,
            "reconnects": self._reconnects,
            "last_msg_age_s": (
                round(time.monotonic() - self._last_msg_at, 1)
                if self._last_msg_at
                else None
            ),
        }

    async def start(self) -> None:
        """Start the WebSocket loop as a background task."""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_forever(), name="binance-ws")
        logger.info("WebSocket background task started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._connected = False
        logger.info("WebSocket stopped")

    # ── Internal ───────────────────────────────────────────────────────────────

    def _build_streams(self) -> list[str]:
        streams: list[str] = []
        for sym in self._symbols:
            s = sym.lower()
            streams.append(f"{s}@aggTrade")
            for tf in config.TIMEFRAMES:
                streams.append(f"{s}@kline_{config.TF_MAP[tf]}")
        return streams

    def _build_url(self, streams: list[str]) -> str:
        combined = "/".join(streams)
        for ep in config.WS_ENDPOINTS:
            # Use combined stream endpoint
            base = ep.replace("/ws", "")
            return f"{base}/stream?streams={combined}"
        return f"wss://stream.binance.com:9443/stream?streams={combined}"

    async def _run_forever(self) -> None:
        """Reconnect loop — runs until task is cancelled."""
        delay = config.WS_RECONNECT_DELAY
        while True:
            try:
                await self._connect_and_run()
                delay = config.WS_RECONNECT_DELAY  # reset on clean disconnect
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._connected = False
                self._reconnects += 1
                logger.warning(
                    "WebSocket disconnected (reconnect #%d in %.0fs): %s",
                    self._reconnects,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 60.0)  # exponential back-off, cap 60s

    async def _connect_and_run(self) -> None:
        streams = self._build_streams()
        if not streams:
            logger.warning("No streams to subscribe — waiting for symbols")
            await asyncio.sleep(5)
            return

        url = self._build_url(streams)
        self._endpoint = url.split("stream?")[0]
        logger.info("Connecting WebSocket: %d streams", len(streams))

        async with websockets.connect(
            url,
            ping_interval=config.WS_PING_INTERVAL,
            ping_timeout=config.WS_PING_TIMEOUT,
            max_size=10 * 1024 * 1024,
        ) as ws:
            self._connected = True
            logger.info("WebSocket connected ✓")
            async for raw in ws:
                self._last_msg_at = time.monotonic()
                try:
                    await self._dispatch(json.loads(raw))
                except Exception as exc:  # noqa: BLE001
                    logger.error("Error dispatching WS message: %s", exc)

        self._connected = False

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        """Route a combined-stream message to the appropriate handler."""
        stream: str = msg.get("stream", "")
        data: dict[str, Any] = msg.get("data", msg)
        event = data.get("e", "")

        if event == "aggTrade":
            await self._handle_trade(data)
        elif event == "kline":
            await self._handle_kline(stream, data)

    async def _handle_trade(self, data: dict[str, Any]) -> None:
        symbol: str = data["s"]
        tick = {
            "symbol": symbol,
            "price": float(data["p"]),
            "qty": float(data["q"]),
            "time": data["T"] // 1000,
            "is_buyer_maker": data["m"],
        }
        if self._on_tick:
            try:
                await self._on_tick(symbol, tick)
            except Exception as exc:  # noqa: BLE001
                logger.error("on_tick callback error: %s", exc)

    async def _handle_kline(self, stream: str, data: dict[str, Any]) -> None:
        k = data["k"]
        symbol: str = k["s"]

        # Map Binance interval string back to our TF name
        binance_interval: str = k["i"]
        tf_name = next(
            (tf for tf, bi in config.TF_MAP.items() if bi == binance_interval),
            binance_interval,
        )

        candle = {
            "time": k["t"] // 1000,
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
            "delta": float(k["V"]) * 2 - float(k["v"]),
            "taker_buy_vol": float(k["V"]),
        }
        closed: bool = k["x"]

        # Update the local cache
        new_close = await candle_cache.update_candle(symbol, tf_name, candle, closed)

        if new_close and self._on_kline_close:
            try:
                await self._on_kline_close(symbol, tf_name, candle)
            except Exception as exc:  # noqa: BLE001
                logger.error("on_kline_close callback error: %s", exc)


# Module-level singleton
binance_ws = BinanceWebSocket()
