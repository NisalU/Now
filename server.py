"""AI Trading Signal Bot — aiohttp + WebSocket server.

Run on any local Linux/Mac machine:
    pip install -r requirements.txt
    python server.py
Then open http://<local-ip>:8000 from any device on the same network.
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

config     = None  # type: ignore[assignment]
ai_analyst = None  # type: ignore[assignment]
engine     = None  # type: ignore[assignment]
manager    = None  # type: ignore[assignment]
scanner    = None  # type: ignore[assignment]


def _ws_broadcast(msg: dict) -> None:
    """Push a message to every connected WebSocket client."""
    if manager is None:
        return
    for c in list(manager.clients):
        c.send(msg)


# ---------------------------------------------------------------------------
# Terminal key prompting
# ---------------------------------------------------------------------------

def _prompt_for_keys() -> None:
    print()
    print("=" * 56)
    print("  AI Trading Signal Bot — API Key Setup")
    print("  Input is hidden; keys will not be displayed.")
    print("=" * 56)
    print()

    print("  Groq API key is required for the AI analyst.")
    print("  Get a free key at https://console.groq.com/keys")
    print("  Press Enter to skip (AI analyst will be disabled).")
    groq_key = getpass.getpass("  Groq API key: ").strip()
    if groq_key:
        os.environ["GROQ_API_KEY"] = groq_key
        print("  [ok] Groq API key set.")
    else:
        print("  [skip] No Groq key — AI analyst disabled.")

    print()

    print("  Binance API credentials (optional — for authenticated endpoints).")
    binance_key = getpass.getpass("  Binance API key (Enter to skip): ").strip()
    if binance_key:
        os.environ["BINANCE_API_KEY"] = binance_key
        binance_secret = getpass.getpass("  Binance API secret: ").strip()
        if binance_secret:
            os.environ["BINANCE_API_SECRET"] = binance_secret
            print("  [ok] Binance credentials set.")
        else:
            print("  [skip] No secret — skipping Binance auth.")
    else:
        print("  [skip] No Binance key — public market data only.")

    print()
    print("=" * 56)
    print()


def _load_app_modules() -> None:
    global config, ai_analyst, engine, manager, scanner

    import config as _config
    from ai_analyst import ai_analyst as _ai_analyst
    from engine import engine as _engine
    from stream import manager as _manager
    from scanner import scanner as _scanner

    _config.BINANCE_API_KEY    = os.environ.get("BINANCE_API_KEY", "")
    _config.BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")

    config     = _config
    ai_analyst = _ai_analyst
    engine     = _engine
    manager    = _manager
    scanner    = _scanner


# ---------------------------------------------------------------------------
# Static / index
# ---------------------------------------------------------------------------

async def index(_request: web.Request) -> web.StreamResponse:
    return web.FileResponse(BASE_DIR / "static" / "index.html")


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

async def api_config(_request: web.Request) -> web.Response:
    return web.json_response({
        "symbols":          config.SYMBOLS,
        "intervals":        config.INTERVALS,
        "default_symbol":   config.DEFAULT_SYMBOL,
        "default_interval": config.DEFAULT_INTERVAL,
        "threshold":        config.SIGNAL_THRESHOLD,
        "refresh_seconds":  config.REFRESH_SECONDS,
        "ai_refresh_seconds": config.AI_REFRESH_SECONDS,
    })


async def api_state(request: web.Request) -> web.Response:
    symbol   = request.query.get("symbol",   config.DEFAULT_SYMBOL)
    interval = request.query.get("interval", config.DEFAULT_INTERVAL)
    if symbol not in config.SYMBOLS or interval not in config.INTERVALS:
        return web.json_response({"error": "invalid symbol or interval"}, status=400)
    try:
        data = await asyncio.to_thread(engine.get_state, symbol, interval)
        return web.json_response(data)
    except Exception as e:
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
    return web.json_response(ai_analyst.get_status())


async def api_ai_signals(_request: web.Request) -> web.Response:
    return web.json_response(ai_analyst.get_recent_signals())


async def api_binance_key_status(_request: web.Request) -> web.Response:
    return web.json_response({
        "api_key_configured":    bool(config.BINANCE_API_KEY),
        "api_secret_configured": bool(config.BINANCE_API_SECRET),
    })


async def api_pipeline_events(_request: web.Request) -> web.Response:
    return web.json_response({
        "events":     ai_analyst.get_pipeline_log(),
        "active_run": ai_analyst.get_active_run(),
    })


async def api_scanner(_request: web.Request) -> web.Response:
    """Return current hot-coin scanner results."""
    coins     = scanner.get_hot_coins() if scanner else []
    last_scan = scanner.get_last_scan() if scanner else 0
    return web.json_response({"coins": coins, "last_scan": last_scan, "count": len(coins)})



async def api_scanner_scan(request: web.Request) -> web.Response:
    """Trigger a manual coin scan and broadcast results via WebSocket."""
    if not scanner:
        return web.json_response({"ok": False, "error": "Scanner not available"}, status=503)
    if scanner.is_scanning():
        return web.json_response({"ok": False, "already_scanning": True})
    triggered = scanner.trigger_scan()
    return web.json_response({"ok": triggered, "already_scanning": not triggered})


async def api_exchange(request: web.Request) -> web.Response:
    """GET → current exchange. POST {exchange: 'spot'|'futures'} → switch."""
    if request.method == 'GET':
        return web.json_response({'exchange': getattr(config, 'ACTIVE_EXCHANGE', 'spot')})
    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'invalid JSON'}, status=400)
    exchange = body.get('exchange', 'spot')
    if exchange not in ('spot', 'futures'):
        return web.json_response({'error': 'use spot or futures'}, status=400)
    config.ACTIVE_EXCHANGE = exchange
    # Clear engine state so next request fetches from the new exchange
    with engine._lock:
        engine._state.clear()
    # Broadcast to all clients
    _ws_broadcast({'type': 'exchange_changed', 'exchange': exchange})
    # Trigger a fresh scan for the new exchange
    if scanner:
        scanner.trigger_scan()
    return web.json_response({'ok': True, 'exchange': exchange})

async def api_pending_limits(request: web.Request) -> web.Response:
    """Return pending LIMIT order signals (waiting for price to reach entry)."""
    symbol = request.query.get("symbol")
    limits = ai_analyst.get_pending_limits(symbol)
    return web.json_response({"pending": limits})


async def api_signal_status(request: web.Request) -> web.Response:
    """Active-signal lock status for each symbol."""
    symbol = request.query.get("symbol")
    status = ai_analyst.get_status()
    if symbol:
        return web.json_response({
            "symbol":        symbol,
            "active_signal": status["active_signals"].get(symbol),
            "next_analysis": status["next_analysis_ts"].get(symbol),
        })
    return web.json_response({
        "active_signals":   status["active_signals"],
        "next_analysis_ts": status["next_analysis_ts"],
    })


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

async def ws_endpoint(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    from stream import Client
    client = Client(ws)

    async def sender():
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
        # ── Hello burst ──────────────────────────────────────────────────
        client.send({
            "type":               "config",
            "symbols":            config.SYMBOLS,
            "intervals":          config.INTERVALS,
            "default_symbol":     config.DEFAULT_SYMBOL,
            "default_interval":   config.DEFAULT_INTERVAL,
            "threshold":          config.SIGNAL_THRESHOLD,
            "ai_refresh_seconds": config.AI_REFRESH_SECONDS,
            "exchange":           getattr(config, "ACTIVE_EXCHANGE", "spot"),
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
            client.send({"type": "pending_limits",   "data": ai_analyst.get_pending_limits(client.symbol)})
            # Send countdown for default symbol
            _push_countdown(client, client.symbol)
        # Send current scanner state
        if scanner:
            _coins = scanner.get_hot_coins()
            if _coins:
                client.send({"type": "scanner_update", "data": _coins})
        manager.add_client(client)

        async def push_snapshot(symbol, interval):
            try:
                data = await asyncio.to_thread(engine.get_state, symbol, interval)
                if client.market() == (symbol, interval):
                    client.send({"type": "snapshot", "data": data})
            except Exception as e:
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
                    # retarget() updates client.symbol/interval AND calls
                    # _resub.set() so the Binance WebSocket immediately
                    # subscribes to the new symbol's aggTrade + kline streams.
                    # Without this, price ticks never arrive for the new coin.
                    manager.retarget(client, sym, ivl)
                    asyncio.create_task(push_snapshot(sym, ivl))
                    # Push cached AI immediately so dashboard updates without wait
                    if ai_analyst.enabled:
                        cached_ai = ai_analyst.get_cached(sym)
                        if cached_ai:
                            client.send({"type": "ai", "data": cached_ai})
                        else:
                            # No cached result — kick off an immediate analysis
                            # so the user gets a signal in seconds, not 45s.
                            asyncio.create_task(_quick_ai(sym))
                        _push_countdown(client, sym)
                        client.send({"type": "engine_status",    "data": ai_analyst.get_status()})
                        client.send({"type": "ai_signals_table", "data": ai_analyst.get_recent_signals()})
                        client.send({"type": "pipeline_log",     "data": ai_analyst.get_pipeline_log()[:40]})
                        client.send({"type": "pending_limits",   "data": ai_analyst.get_pending_limits(sym)})

            elif kind == "ping":
                # Application-level ping → pong with echo timestamp
                client.send({"type": "pong", "t": msg.get("t", 0)})

            elif kind == "scan_now":
                # Manual coin scan triggered by user button
                if scanner:
                    scanner.trigger_scan()

            elif kind == "set_exchange":
                exchange = msg.get("exchange", "spot")
                if exchange in ("spot", "futures"):
                    config.ACTIVE_EXCHANGE = exchange
                    with engine._lock:
                        engine._state.clear()
                    _ws_broadcast({"type": "exchange_changed", "exchange": exchange})
                    if scanner:
                        scanner.trigger_scan()

    finally:
        send_task.cancel()
        manager.remove_client(client)
        with contextlib.suppress(asyncio.CancelledError):
            await send_task
        with contextlib.suppress(Exception):
            await ws.close()

    return ws


def _push_countdown(client, symbol):
    """Push next-analysis countdown for `symbol` to a single client."""
    next_ts = ai_analyst.get_next_analysis_ts(symbol)
    if next_ts:
        client.send({
            "type":        "ai_countdown",
            "symbol":      symbol,
            "next_ts":     next_ts,
            "interval_s":  config.AI_REFRESH_SECONDS,
        })


async def _quick_ai(symbol: str) -> None:
    """Run an immediate AI analysis for `symbol` and broadcast the result.

    Called when a client subscribes to a symbol that has no cached AI result
    so the dashboard shows a signal within seconds rather than waiting up to
    AI_REFRESH_SECONDS for the regular loop to get around to it.
    """
    try:
        result = await asyncio.to_thread(ai_analyst.analyze_safe, symbol)
        if not result:
            return
        ai_payload       = {"type": "ai",               "data": result}
        pipeline_payload = {"type": "pipeline_log",     "data": ai_analyst.get_pipeline_log()[:40]}
        status_payload   = {"type": "engine_status",    "data": ai_analyst.get_status()}
        signals_payload  = {"type": "ai_signals_table", "data": ai_analyst.get_recent_signals()}
        limits_payload   = {"type": "pending_limits",   "data": ai_analyst.get_pending_limits(symbol)}
        for c in manager.clients:
            if c.symbol == symbol:
                c.send(ai_payload)
                c.send(pipeline_payload)
                c.send(limits_payload)
            c.send(status_payload)
            c.send(signals_payload)
        asyncio.create_task(_push_ai_charts(symbol, result))
    except Exception:
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Background loops
# ---------------------------------------------------------------------------

async def _push_ai_charts(symbol: str, result: dict) -> None:
    if not (result or {}).get("signal"):
        return
    for ivl in (config.AI_INTERVAL, config.AI_HTF_INTERVAL):
        try:
            data    = await asyncio.to_thread(engine.get_state, symbol, ivl)
            payload = {"type": "snapshot", "data": data}
            for c in manager.clients:
                if c.symbol == symbol and c.interval == ivl:
                    c.send(payload)
        except Exception:
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
        except Exception:
            traceback.print_exc()
        await asyncio.sleep(10)


async def _ai_loop() -> None:
    """Run AI analyst for each active symbol, every AI_REFRESH_SECONDS.

    - Skips analysis when an active signal is live (price hasn't hit stop/target).
    - Broadcasts countdown so the dashboard can show a live timer.
    - Uses exponential backoff when all models are rate-limited.
    """
    _symbol_queue: list[str] = []
    _consecutive_failures = 0
    _BACKOFF = [300, 600, 1200, 1800]

    while True:
        try:
            active = list({c.symbol for c in manager.clients} | {config.DEFAULT_SYMBOL})
            _symbol_queue[:] = [s for s in _symbol_queue if s in active]
            for s in active:
                if s not in _symbol_queue:
                    _symbol_queue.append(s)

            symbol = _symbol_queue.pop(0) if _symbol_queue else config.DEFAULT_SYMBOL
            if symbol not in _symbol_queue:
                _symbol_queue.append(symbol)

            # Set next-analysis timestamp and broadcast countdown BEFORE running
            import time as _t; next_ts = int(_t.time() + config.AI_REFRESH_SECONDS)
            ai_analyst.set_next_analysis_ts(symbol, next_ts)
            countdown_payload = {
                "type":       "ai_countdown",
                "symbol":     symbol,
                "next_ts":    next_ts,
                "interval_s": config.AI_REFRESH_SECONDS,
            }
            for c in manager.clients:
                c.send(countdown_payload)

            result = await asyncio.to_thread(ai_analyst.analyze_safe, symbol)

            if result.get("error", "").startswith("RATE_LIMIT:"):
                _consecutive_failures += 1
                backoff = _BACKOFF[min(_consecutive_failures - 1, len(_BACKOFF) - 1)]
                print(
                    f"[ai] All models rate-limited (failure #{_consecutive_failures}). "
                    f"Backing off {backoff // 60} min."
                )
                # Update next-ts for extended backoff
                backoff_next = int(__import__("time").time() + backoff)
                ai_analyst.set_next_analysis_ts(symbol, backoff_next)
                backoff_payload = {
                    "type":       "ai_countdown",
                    "symbol":     symbol,
                    "next_ts":    backoff_next,
                    "interval_s": backoff,
                    "rate_limited": True,
                }
                for c in manager.clients:
                    c.send(backoff_payload)
                await asyncio.sleep(backoff)
                continue
            else:
                _consecutive_failures = 0

            # Broadcast results
            ai_payload       = {"type": "ai",               "data": result}
            status_payload   = {"type": "engine_status",    "data": ai_analyst.get_status()}
            signals_payload  = {"type": "ai_signals_table", "data": ai_analyst.get_recent_signals()}
            pipeline_payload = {"type": "pipeline_log",     "data": ai_analyst.get_pipeline_log()[:40]}
            limits_payload   = {"type": "pending_limits",   "data": ai_analyst.get_pending_limits(symbol)}

            for c in manager.clients:
                if c.symbol == symbol:
                    c.send(ai_payload)
                    c.send(pipeline_payload)
                    c.send(limits_payload)
                c.send(status_payload)
                c.send(signals_payload)

            asyncio.create_task(_push_ai_charts(symbol, result))

        except asyncio.CancelledError:
            raise
        except Exception:
            traceback.print_exc()

        await asyncio.sleep(config.AI_REFRESH_SECONDS)


import time as _time_mod  # noqa: E402 (used in _ai_loop)


# ---------------------------------------------------------------------------
# App wiring
# ---------------------------------------------------------------------------

async def on_startup(app: web.Application) -> None:
    manager.start()
    if ai_analyst.enabled:
        app["ai_task"]     = asyncio.create_task(_ai_loop())
        app["status_task"] = asyncio.create_task(_status_loop())
        print(f"[ai] Groq AI analyst enabled — {config.AI_REFRESH_SECONDS}s scalp refresh active")
    else:
        print("[ai] No Groq key — AI analysis disabled")
    if config.BINANCE_API_KEY:
        print("[binance] API key configured")
    else:
        print("[binance] No API key — public endpoints only")
    # Start coin scanner
    if getattr(config, "SCANNER_ENABLED", True) and scanner:
        scanner.set_broadcaster(_ws_broadcast)
        # Run initial scan in background; manual scans are user-triggered after that
        import threading as _threading
        _threading.Thread(
            target=scanner.initial_scan, daemon=True, name="scanner-init"
        ).start()
        print("[scanner] Coin scanner ready — manual scan mode")


async def on_cleanup(app: web.Application) -> None:
    for key in ("ai_task", "status_task"):
        task = app.get(key)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    # scanner has no persistent loop in manual-scan mode
    await manager.stop()



# ---------------------------------------------------------------------------
# Strategy Builder — REST API
# ---------------------------------------------------------------------------

async def api_strategy_builder_list(_request: web.Request) -> web.Response:
    """GET /api/strategy-builder/strategies — list all custom strategies."""
    from strategy_store import list_strategies
    return web.json_response({"strategies": list_strategies()})


async def api_strategy_builder_create(request: web.Request) -> web.Response:
    """POST /api/strategy-builder/strategies — create a new custom strategy."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    from strategy_store import create_strategy
    strategy = create_strategy(data)
    return web.json_response({"strategy": strategy}, status=201)


async def api_strategy_builder_update(request: web.Request) -> web.Response:
    """PUT /api/strategy-builder/strategies/{id} — update a custom strategy."""
    sid = request.match_info.get("id", "")
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    from strategy_store import update_strategy
    updated = update_strategy(sid, data)
    if updated is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"strategy": updated})


async def api_strategy_builder_delete(request: web.Request) -> web.Response:
    """DELETE /api/strategy-builder/strategies/{id} — delete a custom strategy."""
    sid = request.match_info.get("id", "")
    from strategy_store import delete_strategy
    ok = delete_strategy(sid)
    if not ok:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"deleted": sid})


async def api_strategy_builder_test(request: web.Request) -> web.Response:
    """POST /api/strategy-builder/test — test a strategy definition on live candles.

    Body: { strategy_def: {...}, symbol: "APTUSDT", interval: "5m" }
    Returns: { triggered: bool, score: float, reasons: [...], condition_results: [...] }
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    strategy_def = body.get("strategy_def", {})
    symbol   = body.get("symbol",   config.DEFAULT_SYMBOL)
    interval = body.get("interval", config.DEFAULT_INTERVAL)

    if symbol not in config.SYMBOLS:
        symbol = config.DEFAULT_SYMBOL
    if interval not in config.INTERVALS:
        interval = config.DEFAULT_INTERVAL

    try:
        import data_feed
        from strategies.custom import _eval_condition, evaluate as eval_custom

        candles = data_feed.get_klines(symbol, interval)
        result  = eval_custom(strategy_def, candles)

        # Per-condition results for the UI
        cond_results = []
        for cond in strategy_def.get("conditions", []):
            try:
                fired = _eval_condition(cond, candles)
            except Exception:
                fired = False
            cond_results.append({
                "type":    cond.get("type"),
                "label":   cond.get("label", cond.get("type")),
                "params":  cond.get("params", {}),
                "fired":   fired,
            })

        return web.json_response({
            "triggered":        result["score"] != 0,
            "score":            result["score"],
            "reasons":          result["reasons"],
            "condition_results": cond_results,
            "symbol":           symbol,
            "interval":         interval,
        })
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def api_strategy_builder_get_config(_request: web.Request) -> web.Response:
    """GET /api/strategy-builder/config — all strategy weights, enabled state, labels."""
    from engine import STRATEGIES, _runtime_weights, _runtime_disabled
    strategies_out = []
    for key, (label, _fn) in STRATEGIES.items():
        strategies_out.append({
            "key":     key,
            "label":   label,
            "weight":  _runtime_weights.get(key, config.WEIGHTS.get(key, 0)),
            "enabled": key not in _runtime_disabled,
            "default_weight": config.WEIGHTS.get(key, 0),
        })
    return web.json_response({
        "strategies":       strategies_out,
        "signal_threshold": config.SIGNAL_THRESHOLD,
        "strong_threshold": config.STRONG_THRESHOLD,
    })


async def api_strategy_builder_set_config(request: web.Request) -> web.Response:
    """POST /api/strategy-builder/config — update weights and enabled state at runtime."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    from engine import set_runtime_config, STRATEGIES

    new_weights  = {}
    new_disabled = set()
    for item in body.get("strategies", []):
        key = item.get("key", "")
        if key not in STRATEGIES:
            continue
        if "weight" in item:
            new_weights[key] = max(0, min(20, int(item["weight"])))
        if item.get("enabled") is False:
            new_disabled.add(key)

    # Optional threshold updates
    if "signal_threshold" in body:
        config.SIGNAL_THRESHOLD = max(5, min(60, int(body["signal_threshold"])))
    if "strong_threshold" in body:
        config.STRONG_THRESHOLD = max(10, min(80, int(body["strong_threshold"])))

    set_runtime_config(new_weights, new_disabled)
    return web.json_response({"ok": True})

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/",                       index)
    app.router.add_get("/api/config",             api_config)
    app.router.add_get("/api/state",              api_state)
    app.router.add_get("/api/signals",            api_signals)
    app.router.add_get("/api/ai",                 api_ai)
    app.router.add_get("/api/engine-status",      api_engine_status)
    app.router.add_get("/api/ai-signals",         api_ai_signals)
    app.router.add_get("/api/binance-key-status", api_binance_key_status)
    app.router.add_get("/api/pipeline-events",    api_pipeline_events)
    app.router.add_get("/api/signal-status",      api_signal_status)
    app.router.add_get("/api/pending-limits",     api_pending_limits)
    app.router.add_get("/api/scanner",            api_scanner)
    app.router.add_post("/api/scanner/scan",       api_scanner_scan)
    # Strategy Builder
    app.router.add_get( "/api/strategy-builder/strategies",        api_strategy_builder_list)
    app.router.add_post("/api/strategy-builder/strategies",        api_strategy_builder_create)
    app.router.add_put( "/api/strategy-builder/strategies/{id}",   api_strategy_builder_update)
    app.router.add_delete("/api/strategy-builder/strategies/{id}", api_strategy_builder_delete)
    app.router.add_post("/api/strategy-builder/test",              api_strategy_builder_test)
    app.router.add_get( "/api/strategy-builder/config",            api_strategy_builder_get_config)
    app.router.add_post("/api/strategy-builder/config",            api_strategy_builder_set_config)
    app.router.add_get("/api/exchange",             api_exchange)
    app.router.add_post("/api/exchange",            api_exchange)
    app.router.add_get("/ws",                     ws_endpoint)
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
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    _prompt_for_keys()
    _load_app_modules()

    print("=" * 56)
    print("  AI Trading Signal Bot  (aiohttp + WebSocket)")
    print(f"  Local:   http://127.0.0.1:{config.PORT}")
    print(f"  Network: http://{_local_ip()}:{config.PORT}")
    print(f"  AI refresh: every {config.AI_REFRESH_SECONDS}s")
    print("=" * 56)
    web.run_app(
        create_app(),
        host=config.HOST,
        port=config.PORT,
        access_log=None,
        print=None,
    )
