"""Coin Scanner — finds high-volatility altcoins on Binance on demand.

Manual-scan mode: no background polling loop.
Call trigger_scan() to kick off a one-shot scan in a daemon thread.
Results are broadcast via the registered broadcaster function.

Broadcast messages:
  {"type": "scanner_scanning", "scanning": True}   — scan started
  {"type": "scanner_update",   "data": [...]}       — results ready
  {"type": "scanner_scanning", "scanning": False}  — scan finished
  {"type": "config", ...}                           — updated symbol list
"""
import logging
import threading
import time
import traceback

import requests

import config

log = logging.getLogger("scanner")

# Binance REST endpoints tried in order
_TICKER_ENDPOINTS = [
    "https://api.binance.com/api/v3/ticker/24hr",
    "https://api1.binance.com/api/v3/ticker/24hr",
    "https://api2.binance.com/api/v3/ticker/24hr",
    "https://data-api.binance.vision/api/v3/ticker/24hr",
]

# Base tokens to always exclude regardless of volume (stables, leveraged, ETFs)
_EXCLUDE_BASES = {
    "USDC", "BUSD", "TUSD", "USDT", "DAI", "FDUSD", "USDP", "EUR", "GBP",
    "BIFI",
}
_EXCLUDE_FRAGMENTS = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "2L", "2S")


def _vol_score(t: dict) -> float:
    """Composite volatility score — strongly biased toward raw price movement."""
    pct    = abs(float(t.get("priceChangePercent", 0)))
    vol    = float(t.get("quoteVolume", 0))
    high   = float(t.get("highPrice", 0))
    low    = max(float(t.get("lowPrice", 1e-12)), 1e-12)
    amp    = (high / low - 1.0) * 100.0
    trades = float(t.get("count", 0))
    # Raw move % is the dominant signal; amplitude secondary; volume is tie-breaker only
    return pct * 6.0 + amp * 2.5 + min(vol / 1e9, 3.0) * 0.5 + min(trades / 2e5, 2.0) * 0.2


class CoinScanner:
    """Manual-trigger scanner: no auto-loop, user-initiated scans only."""

    def __init__(self):
        self._lock      = threading.Lock()
        self._hot_coins: list = []
        self._last_scan: int  = 0
        self._scanning:  bool = False
        self._broadcast       = None   # set via set_broadcaster()

    # ── public ──────────────────────────────────────────────────────────────

    def set_broadcaster(self, fn):
        """Register fn(dict) — called to push WS messages to all clients."""
        self._broadcast = fn

    def get_hot_coins(self) -> list:
        with self._lock:
            return list(self._hot_coins)

    def get_last_scan(self) -> int:
        with self._lock:
            return self._last_scan

    def is_scanning(self) -> bool:
        with self._lock:
            return self._scanning

    # ── scan ────────────────────────────────────────────────────────────────

    def scan(self) -> list:
        """Fetch 24hr tickers; return sorted list of volatile USDT pairs."""
        raw = None
        for url in _TICKER_ENDPOINTS:
            try:
                resp = requests.get(url, timeout=12)
                if resp.status_code == 200:
                    raw = resp.json()
                    break
            except Exception:
                continue

        if not raw:
            log.warning("[scanner] All Binance ticker endpoints failed")
            return []

        min_vol = getattr(config, "SCANNER_MIN_VOLUME_USDT",  20_000_000)
        min_pct = getattr(config, "SCANNER_VOLATILITY_MIN_PCT", 2.0)
        top_n   = getattr(config, "SCANNER_TOP_N", 20)

        # Merge slow-cap exclusions from config
        slow_caps = getattr(config, "SCANNER_EXCLUDE_SLOW_CAPS", set())
        exclude_bases = _EXCLUDE_BASES | set(slow_caps)

        candidates = []
        for t in raw:
            sym  = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            base = sym[:-4]
            if base in exclude_bases:
                continue
            if any(frag in base for frag in _EXCLUDE_FRAGMENTS):
                continue
            vol = float(t.get("quoteVolume", 0))
            if vol < min_vol:
                continue
            pct = abs(float(t.get("priceChangePercent", 0)))
            if pct < min_pct:
                continue
            score = _vol_score(t)
            candidates.append({
                "symbol":      sym,
                "base":        base,
                "price":       round(float(t.get("lastPrice",          0)), 8),
                "change_pct":  round(float(t.get("priceChangePercent", 0)), 2),
                "volume_usdt": round(vol / 1e6, 1),
                "amp_pct":     round((float(t.get("highPrice", 0)) /
                                      max(float(t.get("lowPrice", 1e-12)), 1e-12) - 1) * 100, 2),
                "score":       round(score, 1),
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:top_n]

    # ── symbol merge ────────────────────────────────────────────────────────

    def _merge_symbols(self, coins: list) -> None:
        """Merge hot coins into config.SYMBOLS, keeping pinned coins first."""
        pinned = getattr(config, "PINNED_SYMBOLS", [config.DEFAULT_SYMBOL])
        new_syms = [c["symbol"] for c in coins]
        merged = list(pinned)
        for sym in new_syms:
            if sym not in merged:
                merged.append(sym)
        config.SYMBOLS = merged[:35]   # hard cap

    # ── internal scan worker ─────────────────────────────────────────────────

    def _run_scan(self):
        """Execute a scan, update state, and broadcast results. Runs in a thread."""
        try:
            coins = self.scan()
            now   = int(time.time())
            with self._lock:
                self._hot_coins = coins
                self._last_scan = now
                self._scanning  = False

            self._merge_symbols(coins)

            if self._broadcast:
                if coins:
                    self._broadcast({"type": "scanner_update", "data": coins})
                # Always send updated config so clients can add new symbols to dropdown
                self._broadcast({
                    "type":             "config",
                    "symbols":          config.SYMBOLS,
                    "intervals":        config.INTERVALS,
                    "default_symbol":   config.DEFAULT_SYMBOL,
                    "default_interval": config.DEFAULT_INTERVAL,
                    "threshold":        config.SIGNAL_THRESHOLD,
                    "ai_refresh_seconds": config.AI_REFRESH_SECONDS,
                })
                # Tell clients scan is done
                self._broadcast({"type": "scanner_scanning", "scanning": False,
                                  "count": len(coins), "last_scan": now})

        except Exception:
            traceback.print_exc()
            with self._lock:
                self._scanning = False
            if self._broadcast:
                self._broadcast({"type": "scanner_scanning", "scanning": False, "error": True})

    # ── public trigger ────────────────────────────────────────────────────────

    def trigger_scan(self) -> bool:
        """Start a manual scan in a background thread.

        Returns True if a new scan was started, False if one is already running.
        """
        with self._lock:
            if self._scanning:
                return False
            self._scanning = True

        log.info("[scanner] Manual scan triggered")
        # Notify clients immediately so they can show a loading state
        if self._broadcast:
            self._broadcast({"type": "scanner_scanning", "scanning": True})

        t = threading.Thread(target=self._run_scan, daemon=True, name="coin-scanner")
        t.start()
        return True

    def initial_scan(self) -> None:
        """Run one scan synchronously at startup (called from a daemon thread).

        Does NOT send scanner_scanning=True so the UI doesn't flicker on load.
        """
        try:
            log.info("[scanner] Running initial scan…")
            coins = self.scan()
            now   = int(time.time())
            with self._lock:
                self._hot_coins = coins
                self._last_scan = now
            self._merge_symbols(coins)
            if self._broadcast and coins:
                self._broadcast({"type": "scanner_update", "data": coins})
                self._broadcast({
                    "type":             "config",
                    "symbols":          config.SYMBOLS,
                    "intervals":        config.INTERVALS,
                    "default_symbol":   config.DEFAULT_SYMBOL,
                    "default_interval": config.DEFAULT_INTERVAL,
                    "threshold":        config.SIGNAL_THRESHOLD,
                    "ai_refresh_seconds": config.AI_REFRESH_SECONDS,
                })
            log.info("[scanner] Initial scan complete — %d coins found", len(coins))
        except Exception:
            traceback.print_exc()


scanner = CoinScanner()
