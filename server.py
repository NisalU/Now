"""AI Trading Signal Bot — aiohttp + WebSocket server.

Run on Termux:
    pkg install python
    pip install -r requirements.txt
    python server.py
Then open http://<phone-local-ip>:8000 from any device on the same network.

The dashboard talks to /ws for realtime ticks, moving candles, analysis
snapshots and signal events. REST endpoints are kept as a fallback.
"""
import asyncio
import contextlib
import json
import socket
import traceback
from pathlib import Path

from aiohttp import WSMsgType, web

import config
from ai_analyst import ai_analyst
from engine import engine
from stream import Client, manager

BASE_DIR = Path(__file__).parent


async def index(_request: web.Request) -> web.StreamResponse:
    return web.FileResponse(BASE_DIR / "static" / "index.html")


# ---------------- REST fallback ----------------
async def api_config(_request: web.Request) -> web.Response:
    return web.json_response({
        "symbols": config.SYMBOLS,
        "intervals": config.INTERVALS,
        "default_symbol": config.DEFAULT_SYMBOL,
        "default_interval": config.DEFAULT_INTERVAL,
        "threshold": config.SIGNAL_THRESHOLD,
        "refresh_seconds": config.REFRESH_SECONDS,
    })


async def api_state(request: web.Request) -> web.Response:
    symbol = request.query.get("symbol", config.DEFAULT_SYMBOL)
    interval = request.query.get("interval", config.DEFAULT_INTERVAL)
    if symbol not in config.SYMBOLS or interval not in config.INTERVALS:
        return web.json_response({"error": "invalid symbol or interval"}, status=400)
    try:
        data = await asyncio.to_thread(engine.get_state, symbol, interval)
        return web.json_response(data)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"error": str(e)}, status=502)


async def api_signals(_request: web.Request) -> web.Response:
    return web.json_response(list(reversed(engine.signals[-50:])))


async def api_ai(request: web.Request) -> web.Response:
    symbol = request.query.get("symbol", config.DEFAULT_SYMBOL)
    if symbol not in config.SYMBOLS:
        return web.json_response({"error": "invalid symbol"}, status=400)
    if not ai_analyst.enabled:
        return web.json_response({"error": "GROQ_API_KEY not set"}, status=503)
    cached = ai_analyst.get_cached(symbol)
    if cached:
        return web.json_response(cached)
    result = await asyncio.to_thread(ai_analyst.analyze_safe, symbol)
    return web.json_response(result)


async def api_engine_status(_request: web.Request) -> web.Response:
    """AI engine metrics for the status widget."""
    return web.json_response(ai_analyst.get_status())


async def api_ai_signals(_request: web.Request) -> web.Response:
    """Recent AI LONG/SHORT signals for the signals table."""
    return web.json_response(ai_analyst.get_recent_signals())


async def api_binance_key_status(_request: web.Request) -> web.Response:
    """Returns whether Binance API credentials are configured (never reveals keys)."""
    return web.json_response({
        "api_key_configured": bool(config.BINANCE_API_KEY),
        "api_secret_configured": bool(config.BINANCE_API_SECRET),
    })


# ---------------- realtime WebSocket ----------------
async def ws_endpoint(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    client = Client(ws)

    async def sender():
        while True:
            msg = await client.queue.get()
            await ws.send_str(json.dumps(msg))

    send_task = asyncio.create_task(sender())
    try:
        # hello: config + signal history
        client.send({
            "type": "config",
            "symbols": config.SYMBOLS,
            "intervals": config.INTERVALS,
            "default_symbol": config.DEFAULT_SYMBOL,
            "default_interval": config.DEFAULT_INTERVAL,
            "threshold": config.SIGNAL_THRESHOLD,
        })
        if config.ENGINE_SIGNAL_FEED:
            client.send({"type": "signals", "data": list(reversed(engine.signals[-50:]))})
        if ai_analyst.enabled:
            cached_ai = ai_analyst.get_cached(client.symbol)
            if cached_ai:
                client.send({"type": "ai", "data": cached_ai})
            # Push initial engine status + recent signals table.
            client.send({"type": "engine_status", "data": ai_analyst.get_status()})
            client.send({"type": "ai_signals_table", "data": ai_analyst.get_recent_signals()})
        manager.add_client(client)

        async def push_snapshot(symbol, interval):
            try:
                data = await asyncio.to_thread(engine.get_state, symbol, interval)
                if client.market() == (symbol, interval):
                    client.send({"type": "snapshot", "data": data})
            except Exception as e:  # noqa: BLE001
                client.send({"type": "error", "message": str(e)})

        # initial snapshot for the default market
        asyncio.create_task(push_snapshot(client.symbol, client.interval))

        async for frame in ws:
            if frame.type != WSMsgType.TEXT:
                if frame.type == WSMsgType.ERROR:
                    break
                continue
            try:
                msg = json.loads(frame.data)
            except ValueError:
                continue
            if msg.get("type") == "subscribe":
                symbol = msg.get("symbol", client.symbol)
                interval = msg.get("interval", client.interval)
                if symbol in config.SYMBOLS and interval in config.INTERVALS:
                    manager.retarget(client, symbol, interval)
                    asyncio.create_task(push_snapshot(symbol, interval))
                    if ai_analyst.enabled:
                        cached_ai = ai_analyst.get_cached(symbol)
                        if cached_ai:
                            client.send({"type": "ai", "data": cached_ai})
            elif msg.get("type") == "ping":
                client.send({"type": "pong", "t": msg.get("t")})
    finally:
        manager.remove_client(client)
        send_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await send_task
    return ws


# ---------------- AI analyst loop ----------------
async def _ai_loop():
    """Periodically run the Groq analyst for active symbols and push results
    to connected clients, including live engine status and signals table."""
    while True:
        try:
            symbols = {c.symbol for c in manager.clients}
            symbols.add(config.DEFAULT_SYMBOL)
            for symbol in symbols:
                result = await asyncio.to_thread(ai_analyst.analyze_safe, symbol)
                ai_payload = {"type": "ai", "data": result}
                status_payload = {"type": "engine_status", "data": ai_analyst.get_status()}
                signals_payload = {"type": "ai_signals_table", "data": ai_analyst.get_recent_signals()}
                for c in manager.clients:
                    if c.symbol == symbol:
                        c.send(ai_payload)
                    # Push status and signals table to all clients.
                    c.send(status_payload)
                    c.send(signals_payload)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        await asyncio.sleep(config.AI_REFRESH_SECONDS)


# ---------------- app wiring ----------------
async def on_startup(app: web.Application):
    manager.start()
    if ai_analyst.enabled:
        app["ai_task"] = asyncio.create_task(_ai_loop())
        print("[ai] Groq order-flow analyst enabled — no regime/quality gates active")
    else:
        print("[ai] GROQ_API_KEY not set — AI analysis disabled")
    if config.BINANCE_API_KEY:
        print("[binance] API key configured — authenticated endpoints available")
    else:
        print("[binance] No API key — market data only (public endpoints)")


async def on_cleanup(app: web.Application):
    task = app.get("ai_task")
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await manager.stop()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/config", api_config)
    app.router.add_get("/api/state", api_state)
    app.router.add_get("/api/signals", api_signals)
    app.router.add_get("/api/ai", api_ai)
    app.router.add_get("/api/engine-status", api_engine_status)
    app.router.add_get("/api/ai-signals", api_ai_signals)
    app.router.add_get("/api/binance-key-status", api_binance_key_status)
    app.router.add_get("/ws", ws_endpoint)
    app.router.add_static("/static", BASE_DIR / "static", name="static")
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def _local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


if __name__ == "__main__":
    print("=" * 52)
    print("  AI Trading Signal Bot  (aiohttp + WebSocket)")
    print(f"  Local:   http://127.0.0.1:{config.PORT}")
    print(f"  Network: http://{_local_ip()}:{config.PORT}")
    print("=" * 52)
    web.run_app(
        create_app(),
        host=config.HOST,
        port=config.PORT,
        access_log=None,
        print=None,
    )
