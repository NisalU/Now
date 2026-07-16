"""WebSocket broadcast manager for the FastAPI server.

Manages connections from dashboard clients and broadcasts:
  - Live price ticks (1Hz throttled)
  - Candle closes (per timeframe)
  - New signals
  - Trade lifecycle events
  - System status
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages all active WebSocket client connections."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._last_tick: dict[str, float] = {}  # symbol -> timestamp
        self._tick_interval = 0.5  # seconds between tick broadcasts

    @property
    def count(self) -> int:
        return len(self._connections)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)
        logger.info("WS client connected — total: %d", self.count)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)
        logger.info("WS client disconnected — total: %d", self.count)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a message to all connected clients, removing dead connections."""
        if not self._connections:
            return
        text = json.dumps(message, default=_json_default)
        dead: list[WebSocket] = []

        async with self._lock:
            conns = set(self._connections)

        for ws in conns:
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_text(text)
            except Exception:  # noqa: BLE001
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    async def broadcast_tick(
        self, symbol: str, price: float, extra: dict[str, Any] | None = None
    ) -> None:
        """Throttled price tick broadcast."""
        now = time.monotonic()
        last = self._last_tick.get(symbol, 0.0)
        if now - last < self._tick_interval:
            return
        self._last_tick[symbol] = now
        msg: dict[str, Any] = {"type": "tick", "symbol": symbol, "price": price, "ts": int(time.time() * 1000)}
        if extra:
            msg.update(extra)
        await self.broadcast(msg)

    async def broadcast_signal(self, signal: dict[str, Any]) -> None:
        await self.broadcast({"type": "signal", **signal})

    async def broadcast_trade_event(self, event: str, trade: dict[str, Any]) -> None:
        await self.broadcast({"type": "trade_event", "event": event, "trade": trade})

    async def broadcast_analysis(self, symbol: str, analysis: dict[str, Any]) -> None:
        await self.broadcast({"type": "analysis", "symbol": symbol, "data": analysis})

    async def broadcast_status(self, status: dict[str, Any]) -> None:
        await self.broadcast({"type": "status", **status})


def _json_default(obj: Any) -> Any:
    try:
        return float(obj)
    except (TypeError, ValueError):
        return str(obj)


async def ws_endpoint(ws: WebSocket, manager: "ConnectionManager") -> None:
    """FastAPI WebSocket endpoint handler."""
    await manager.connect(ws)
    try:
        while True:
            # Keep connection alive — listen for client pings
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                if data == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                # Send keepalive
                try:
                    await ws.send_text(json.dumps({"type": "heartbeat", "ts": int(time.time() * 1000)}))
                except Exception:  # noqa: BLE001
                    break
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("WS error: %s", exc)
    finally:
        await manager.disconnect(ws)


# Module-level singleton
ws_manager = ConnectionManager()
