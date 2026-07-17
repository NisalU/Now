"""Coin Scanner — finds high-volatility altcoins on Binance in real time.

Polls Binance /api/v3/ticker/24hr every SCANNER_INTERVAL seconds.
Filters USDT spot pairs by 24h volume and price movement, ranks by a
composite volatility score, then:
  1. Updates config.SYMBOLS dynamically so the bot watches hot coins.
  2. Broadcasts {"type": "scanner_update", "data": [...]} to all WS clients.
  3. Broadcasts an updated {"type": "config", ...} so clients refresh dropdowns.
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
    """Background scanner that keeps the hot-coin list fresh."""

    def __init__(self):
        self._lock      = threading.Lock()
        self._hot_coins = []
        self._last_scan = 0
        self._broadcast = None   # set via set_broadcaster()
        self._thread    = None
        self._running   = False

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

            high   = float(t.get("highPrice", 0))
            low    = max(float(t.get("lowPrice", 1e-12)), 1e-12)
            amp    = round((high / low - 1.0) * 100, 2)
            chg    = round(float(t.get("priceChangePercent", 0)), 2)
            price  = float(t.get("lastPrice", 0))
            score  = _vol_score(t)

            # Compute price precision for display
            digits = 2
            if price < 0.001:
                digits = 8
            elif price < 1:
                digits = 5
            elif price < 10:
                digits = 4
            elif price < 1000:
                digits = 3

            candidates.append({
                "symbol":      sym,
                "base":        base,
                "price":       round(price, digits),
                "change_pct":  chg,
                "volume_usdt": round(vol / 1e6, 1),
                "high":        round(high, digits),
                "low":         round(low, digits),
                "amp_pct":     amp,
                "trades_k":    round(float(t.get("count", 0)) / 1000, 1),
                "score":       round(score, 2),
                "bullish":     chg >= 0,
            })

        candidates.sort(key=lambda x: -x["score"])
        result = candidates[:top_n]
        log.info("[scanner] %d hot coins found from %d USDT pairs", len(result), len(candidates))
        return result

    # ── symbol merge ────────────────────────────────────────────────────────

    def _merge_symbols(self, hot: list):
        """Add hot coins to config.SYMBOLS while keeping pinned coins first."""
        pinned  = getattr(config, "PINNED_SYMBOLS", ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        merged  = list(pinned)
        for c in hot:
            sym = c["symbol"]
            if sym not in merged:
                merged.append(sym)
        config.SYMBOLS = merged[:35]   # hard cap

    # ── background loop ─────────────────────────────────────────────────────

    def _loop(self):
        interval = getattr(config, "SCANNER_INTERVAL", 300)
        while self._running:
            try:
                coins = self.scan()
                now   = int(time.time())
                with self._lock:
                    self._hot_coins = coins
                    self._last_scan = now
                self._merge_symbols(coins)

                if self._broadcast and coins:
                    # Send scanner list
                    self._broadcast({"type": "scanner_update", "data": coins})
                    # Send refreshed config so clients can add new symbols to dropdown
                    self._broadcast({
                        "type":             "config",
                        "symbols":          config.SYMBOLS,
                        "intervals":        config.INTERVALS,
                        "default_symbol":   config.DEFAULT_SYMBOL,
                        "default_interval": config.DEFAULT_INTERVAL,
                        "threshold":        config.SIGNAL_THRESHOLD,
                        "ai_refresh_seconds": config.AI_REFRESH_SECONDS,
                    })

            except Exception:
                traceback.print_exc()
            time.sleep(interval)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="coin-scanner"
        )
        self._thread.start()
        log.info("[scanner] Started — scanning every %ds", getattr(config, "SCANNER_INTERVAL", 300))

    def stop(self):
        self._running = False
        log.info("[scanner] Stopped")


scanner = CoinScanner()
