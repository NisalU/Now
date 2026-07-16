"""AI Trading Signal Bot — aiohttp + WebSocket server.

Run on Termux or any local machine:
    pip install -r requirements.txt
    python server.py
You will be prompted for your API keys in the terminal.
Then open http://<local-ip>:8000 from any device on the same network.

The dashboard talks to /ws for realtime ticks, moving candles, analysis
snapshots and signal events. REST endpoints are kept as a fallback.
"""
import asyncio
import contextlib
import getpass
import json
import os
import socket
import sys
import traceback
from pathlib import Path

from aiohttp import WSMsgType, web

BASE_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# These module-level names are populated by _load_app_modules() inside
# __main__, AFTER API keys have been collected from the terminal.
# All route handlers reference them by name at call time, so deferred
# assignment is safe.
# ---------------------------------------------------------------------------
config = None       # type: ignore[assignment]
ai_analyst = None   # type: ignore[assignment]
engine = None       # type: ignore[assignment]
manager = None      # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Terminal key prompting
# ---------------------------------------------------------------------------

def _prompt_for_keys() -> None:
    """Interactively collect API keys from the terminal.

    Input is hidden (getpass) so keys never appear on screen.
    All keys are optional — the app degrades gracefully when they are absent.
    """
    print()
    print("=" * 56)
    print("  AI Trading Signal Bot — API Key Setup")
    print("  Input is hidden; keys will not be displayed.")
    print("=" * 56)
    print()

    # ---- Google Gemini ----
    print("  Google Gemini API key is required for the AI analyst.")
    print("  Get a free key at https://aistudio.google.com")
    print("  Press Enter to skip (AI analyst will be disabled).")
    gemini_key = getpass.getpass("  Gemini API key: ").strip()
    if gemini_key:
        os.environ["GEMINI_API_KEY"] = gemini_key
        print("  [ok] Gemini API key set.")
    else:
        print("  [skip] No Gemini key — AI analyst disabled.")

    print()

    # ---- Binance ----
    print("  Binance API credentials are REQUIRED.")
    while True:
        binance_key = getpass.getpass("  Binance API key: ").strip()
        if binance_key:
            break
        print("  [error] Binance API key cannot be empty. Please enter your key.")
    os.environ["BINANCE_API_KEY"] = binance_key
    print("  [ok] Binance API key set.")

    while True:
        binance_secret = getpass.getpass("  Binance API secret: ").strip()
        if binance_secret:
            break
        print("  [error] Binance API secret cannot be empty. Please enter your secret.")
    os.environ["BINANCE_API_SECRET"] = binance_secret
    print("  [ok] Binance API secret set.")

    print()
    print("=" * 56)
    print()


def _load_app_modules() -> None:
    """Import app modules after os.environ has been populated.

    This ensures ai_analyst.enabled is set correctly (it reads the
    Gemini key at class instantiation time).
    """
    global config, ai_analyst, engine, manager

    import config as _config
    from ai_analyst import ai_analyst as _ai_analyst
    from engine import engine as _engine
    from stream import manager as _manager

    # Patch Binance credentials into the already-imported config object so
    # that any code reading config.BINANCE_API_KEY gets the user's input.
    _config.BINANCE_API_KEY    = os.environ.get("BINANCE_API_KEY", "")
    _config.BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")

    config     = _config
    ai_analyst = _ai_analyst
    engine     = _engine
    manager    = _manager


# ---------------------------------------------------------------------------
# Static file
# ---------------------------------------------------------------------------

async def index(_request: web.Request) -> web.StreamResponse:
    return web.FileResponse(BASE_DIR / "static" / "index.html")


# ---------------------------------------------------------------------------
# REST fallback
# ---------------------------------------------------------------------------

async def api_config(_request: web.Request) -> web.Response:
    return web.json_response({
        "symbols":         config.SYMBOLS,
        "intervals":       config.INTERVALS,
        "default_symbol":  config.DEFAULT_SYMBOL,
        "default_interval": config.DEFAULT_INTERVAL,
        "threshold":       config.SIGNAL_THRESHOLD,
        "refresh_seconds": config.REFRESH_SECONDS,
    })


async def api_state(request: web.Request) -> web.Response:
    symbol   = request.query.get("symbol",   config.DEFAULT_SYMBOL)
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
        return web.json_response({"error": "GEMINI_API_KEY not set"}, status=503)
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
        "api_key_configured":    bool(config.BINANCE_API_KEY),
        "api_secret_configured": bool(config.BINANCE_API_SECRET),
    })


async def api_pipeline_events(_request: web.Request) -> web.Response:
    """Return recent AI pipeline events for the live-processing view.

    Each event is a dict with:
      ts        – Unix timestamp (float, seconds)
      run_id    – "<symbol>:<epoch>" identifying a single pipeline run
      stage     – stage name: market_data, memory_context, ai_call,
                  model_attempt, model_rate_limited, model_success,
                  model_error, provider_fallback, provider_recovered,
                  ai_parsed, trade_quality, critic, signal_out, …
      + stage-specific fields (symbol, model, provider, latency_ms, …)

    Events are newest-first.  Clients should poll every ~5 s or receive
    events via the WebSocket ``pipeline_log`` push after each AI run.
    """
    return web.json_response({
        "events":     ai_analyst.get_pipeline_log(),
        "active_run": ai_analyst.get_active_run(),
    })


# ---------------------------------------------------------------------------
# Realtime WebSocket
# ---------------------------------------------------------------------------

async def ws_endpoint(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    from stream import Client
    client = Client(ws)

    async def sender():
        """Drain the client queue and write to the WebSocket.
        Exits cleanly on cancellation or any send error (closed socket, etc.)."""
        try:
            while True:
                msg = await client.queue.get()
                try:
                    await ws.send_str(json.dumps(msg, default=str))
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    send_task = asyncio.create_task(sender())
    try:
        # hello: config + signal history
        client.send({
            "type":             "config",
            "symbols":          config.SYMBOLS,
            "intervals":        config.INTERVALS,
            "default_symbol":   config.DEFAULT_SYMBOL,
            "default_interval": config.DEFAULT_INTERVAL,
            "threshold":        config.SIGNAL_THRESHOLD,
        })
        if config.ENGINE_SIGNAL_FEED:
            client.send({"type": "signals", "data": list(reversed(engine.signals[-50:]))})
        if ai_analyst.enabled:
            cached_ai = ai_analyst.get_cached(client.symbol)
            if cached_ai:
                client.send({"type": "ai", "data": cached_ai})
            client.send({"type": "engine_status",    "data": ai_analyst.get_status()})
            client.send({"type": "ai_signals_table", "data": ai_analyst.get_recent_signals()})
            client.send({"type": "pipeline_log",     "data": ai_analyst.get_pipeline_log()[:40]})
        manager.add_client(client)

        async def push_snapshot(symbol, interval):
            try:
                data = await asyncio.to_thread(engine.get_state, symbol, interval)
                if client.market() == (symbol, interval):
                    client.send({"type": "snapshot", "data": data})
            except Exception as e:  # noqa: BLE001
                client.send({"type": "error", "message": str(e)})

        asyncio.create_task(push_snapshot(client.symbol, client.interval))

        async for frame in ws:
            if frame.type != WSMsgType.TEXT:
                if frame.type == WSMsgType.ERROR:
                    break
                continue
            try:
                msg = json.loads(frame.data)
            except (json.JSONDecodeError, TypeError):
                continue
            kind = msg.get("type")
            if kind == "subscribe":
                sym = msg.get("symbol", config.DEFAULT_SYMBOL)
                ivl = msg.get("interval", config.DEFAULT_INTERVAL)
                if sym in config.SYMBOLS and ivl in config.INTERVALS:
                    client.symbol   = sym
                    client.interval = ivl
                    asyncio.create_task(push_snapshot(sym, ivl))
    finally:
        send_task.cancel()
        manager.remove_client(client)
        with contextlib.suppress(asyncio.CancelledError):
            await send_task
        with contextlib.suppress(Exception):
            await ws.close()

    return ws


# ---------------------------------------------------------------------------
# Background loops
# ---------------------------------------------------------------------------

async def _push_ai_charts(symbol: str, result: dict) -> None:
    """Push 15m and 1h chart snapshots to all clients subscribed to *symbol*
    when an AI signal fires, so the chart updates immediately."""
    if not (result or {}).get("signal"):
        return
    for ivl in (config.AI_INTERVAL, config.AI_HTF_INTERVAL):
        try:
            data = await asyncio.to_thread(engine.get_state, symbol, ivl)
            payload = {"type": "snapshot", "data": data}
            for c in manager.clients:
                if c.symbol == symbol and c.interval == ivl:
                    c.send(payload)
        except Exception:  # noqa: BLE001
            pass


async def _status_loop() -> None:
    """Broadcast engine status + AI signals table every 10 s."""
    while True:
        try:
            status_payload  = {"type": "engine_status",    "data": ai_analyst.get_status()}
            signals_payload = {"type": "ai_signals_table", "data": ai_analyst.get_recent_signals()}
            for c in manager.clients:
                c.send(status_payload)
                c.send(signals_payload)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        await asyncio.sleep(10)


async def _ai_loop() -> None:
    """Run AI analyst one symbol per cycle, rotating through active symbols.

    Uses exponential backoff when all Gemini models are rate-limited so the
    bot stops hammering a quota that is exhausted for the day.

    Backoff schedule (consecutive all-fail cycles):
      1st fail  →  wait 5 min  (normal cadence, might be transient)
      2nd fail  →  wait 10 min
      3rd fail  →  wait 20 min
      4th+ fail →  wait 30 min (cap — retry once every half hour)
    """
    _symbol_queue: list[str] = []
    _consecutive_failures = 0
    _BACKOFF = [300, 600, 1200, 1800]  # seconds per failure count

    while True:
        try:
            # Build the current set of interesting symbols
            active = list({c.symbol for c in manager.clients} | {config.DEFAULT_SYMBOL})

            # Rotate: drop symbols no longer active, append new ones
            _symbol_queue[:] = [s for s in _symbol_queue if s in active]
            for s in active:
                if s not in _symbol_queue:
                    _symbol_queue.append(s)

            symbol = _symbol_queue.pop(0) if _symbol_queue else config.DEFAULT_SYMBOL
            if _symbol_queue is not None and symbol not in _symbol_queue:
                _symbol_queue.append(symbol)

            result = await asyncio.to_thread(ai_analyst.analyze_safe, symbol)

            # Detect all-rate-limited outcome vs real success
            if result.get("error", "").startswith("RATE_LIMIT:"):
                _consecutive_failures += 1
                backoff = _BACKOFF[min(_consecutive_failures - 1, len(_BACKOFF) - 1)]
                print(
                    f"[ai] All Gemini models rate-limited (failure #{_consecutive_failures}). "
                    f"Quota may be exhausted — backing off {backoff // 60} min before next attempt."
                )
                await asyncio.sleep(backoff)
                continue
            else:
                _consecutive_failures = 0  # reset on success

            ai_payload       = {"type": "ai",               "data": result}
            status_payload   = {"type": "engine_status",    "data": ai_analyst.get_status()}
            signals_payload  = {"type": "ai_signals_table", "data": ai_analyst.get_recent_signals()}
            pipeline_payload = {"type": "pipeline_log",     "data": ai_analyst.get_pipeline_log()[:40]}
            for c in manager.clients:
                if c.symbol == symbol:
                    c.send(ai_payload)
                    c.send(pipeline_payload)
                c.send(status_payload)
                c.send(signals_payload)
            asyncio.create_task(_push_ai_charts(symbol, result))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        await asyncio.sleep(config.AI_REFRESH_SECONDS)


# ---------------------------------------------------------------------------
# App wiring
# ---------------------------------------------------------------------------

async def on_startup(app: web.Application) -> None:
    manager.start()
    if ai_analyst.enabled:
        app["ai_task"]     = asyncio.create_task(_ai_loop())
        app["status_task"] = asyncio.create_task(_status_loop())
        print("[ai] Google Gemini AI analyst enabled — auto model cycling active")
    else:
        print("[ai] No Gemini key — AI analysis disabled")
    if config.BINANCE_API_KEY:
        print("[binance] API key configured — authenticated endpoints available")
    else:
        print("[binance] No API key — market data only (public endpoints)")


async def on_cleanup(app: web.Application) -> None:
    for key in ("ai_task", "status_task"):
        task = app.get(key)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    await manager.stop()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/",                     index)
    app.router.add_get("/api/config",           api_config)
    app.router.add_get("/api/state",            api_state)
    app.router.add_get("/api/signals",          api_signals)
    app.router.add_get("/api/ai",               api_ai)
    app.router.add_get("/api/engine-status",    api_engine_status)
    app.router.add_get("/api/ai-signals",       api_ai_signals)
    app.router.add_get("/api/binance-key-status", api_binance_key_status)
    app.router.add_get("/api/pipeline-events",  api_pipeline_events)
    app.router.add_get("/ws",                   ws_endpoint)
    app.router.add_static("/static", BASE_DIR / "static", name="static")
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 1. Prompt for keys BEFORE importing app modules.
    #    ai_analyst reads GEMINI_API_KEY at class instantiation, so env
    #    must be set first.
    _prompt_for_keys()

    # 2. Import app modules now that os.environ is populated.
    _load_app_modules()

    # 3. Start the server.
    print("=" * 56)
    print("  AI Trading Signal Bot  (aiohttp + WebSocket)")
    print(f"  Local:   http://127.0.0.1:{config.PORT}")
    print(f"  Network: http://{_local_ip()}:{config.PORT}")
    print("=" * 56)
    web.run_app(
        create_app(),
        host=config.HOST,
        port=config.PORT,
        access_log=None,
        print=None,
    )
