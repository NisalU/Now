"""Async Binance REST client with endpoint fallback and TTL caching.

Supports both spot and futures endpoints. Falls back automatically to
geo-unrestricted mirrors (data-api.binance.vision) when primary endpoints fail.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
import urllib.parse
from typing import Any

import httpx

from trading import config

logger = logging.getLogger(__name__)

# Module-level state: last working base URL per endpoint group
_spot_base: str | None = None
_fut_base: str | None = None
_fut_disabled_until: float = 0.0

# Simple TTL cache: key -> (expires_at, data)
_cache: dict[str, tuple[float, Any]] = {}
_TICKER_TTL = 10.0
_FUTURES_TTL = 120.0


class DataError(Exception):
    """Raised when all Binance endpoints fail or return an error."""


def _sign(params: dict[str, Any]) -> str:
    if not config.BINANCE_API_SECRET:
        raise DataError("BINANCE_API_SECRET not configured")
    query = urllib.parse.urlencode(sorted(params.items()))
    return hmac.new(
        config.BINANCE_API_SECRET.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def _get(
    client: httpx.AsyncClient,
    bases: list[str],
    cached_base: str | None,
    path: str,
    params: dict[str, Any],
    signed: bool = False,
) -> tuple[Any, str]:
    """Try each base URL in order, caching the last working one."""
    if signed:
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = _sign(params)

    headers: dict[str, str] = {}
    if config.BINANCE_API_KEY:
        headers["X-MBX-APIKEY"] = config.BINANCE_API_KEY

    ordered = ([cached_base] if cached_base else []) + [
        b for b in bases if b != cached_base
    ]
    last_err = "no endpoints configured"

    for base in ordered:
        try:
            r = await client.get(
                base + path, params=params, headers=headers, timeout=10.0
            )
            if r.status_code == 200:
                data = r.json()
                # Binance geo-block returns 200 with {code, msg}
                if isinstance(data, dict) and "code" in data and "msg" in data:
                    last_err = data.get("msg", "geo-blocked")
                    continue
                return data, base
            last_err = f"HTTP {r.status_code}: {r.text[:100]}"
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            logger.debug("Endpoint %s%s failed: %s", base, path, exc)

    raise DataError(f"All Binance endpoints failed for {path}: {last_err}")


async def get_klines(
    symbol: str,
    interval: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch OHLCV klines. Returns list oldest→newest with delta computed."""
    global _spot_base
    limit = limit or config.KLINE_LIMIT

    async with httpx.AsyncClient() as client:
        raw, _spot_base = await _get(
            client,
            config.SPOT_ENDPOINTS,
            _spot_base,
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )

    candles: list[dict[str, Any]] = []
    for k in raw:
        total_vol = float(k[5])
        taker_buy_vol = float(k[9])
        delta = taker_buy_vol * 2 - total_vol  # buy vol - sell vol
        candles.append(
            {
                "time": k[0] // 1000,  # ms → seconds
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": total_vol,
                "delta": delta,
                "taker_buy_vol": taker_buy_vol,
            }
        )
    return candles


async def get_ticker(symbol: str) -> dict[str, Any]:
    """24h ticker stats with 10s TTL cache."""
    global _spot_base
    key = f"ticker:{symbol}"
    now = time.monotonic()
    if key in _cache and now < _cache[key][0]:
        return _cache[key][1]

    async with httpx.AsyncClient() as client:
        raw, _spot_base = await _get(
            client,
            config.SPOT_ENDPOINTS,
            _spot_base,
            "/api/v3/ticker/24hr",
            {"symbol": symbol},
        )

    result: dict[str, Any] = {
        "price": float(raw["lastPrice"]),
        "change_pct": float(raw["priceChangePercent"]),
        "volume_24h": float(raw["volume"]),
        "quote_volume_24h": float(raw["quoteVolume"]),
        "high_24h": float(raw["highPrice"]),
        "low_24h": float(raw["lowPrice"]),
        "count": int(raw["count"]),
    }
    _cache[key] = (now + _TICKER_TTL, result)
    return result


async def get_futures_stats(symbol: str) -> dict[str, Any] | None:
    """Funding rate + open interest + L/S ratio with 2-min TTL cache.

    Returns None when futures data is unavailable (spot-only symbol,
    geo-restriction, etc.). Never raises.
    """
    global _fut_base, _fut_disabled_until
    key = f"futures:{symbol}"
    now = time.monotonic()

    if now < _fut_disabled_until:
        return _cache.get(key, (0, None))[1]

    if key in _cache and now < _cache[key][0]:
        return _cache[key][1]

    async with httpx.AsyncClient() as client:
        try:
            # Funding rate
            fr_raw, _fut_base = await _get(
                client,
                config.FUTURES_ENDPOINTS,
                _fut_base,
                "/fapi/v1/premiumIndex",
                {"symbol": symbol},
            )
            funding_rate = float(fr_raw["lastFundingRate"])

            # Open interest
            oi_raw, _ = await _get(
                client,
                config.FUTURES_ENDPOINTS,
                _fut_base,
                "/fapi/v1/openInterest",
                {"symbol": symbol},
            )
            open_interest = float(oi_raw["openInterest"])

            # Long/short ratio (top trader accounts, 1h)
            ls_raw, _ = await _get(
                client,
                config.FUTURES_ENDPOINTS,
                _fut_base,
                "/futures/data/topLongShortAccountRatio",
                {"symbol": symbol, "period": "1h", "limit": 2},
            )
            long_short_ratio = float(ls_raw[0]["longShortRatio"]) if ls_raw else 1.0
            prev_ratio = float(ls_raw[1]["longShortRatio"]) if len(ls_raw) > 1 else long_short_ratio
            oi_change_pct = 0.0  # requires historical OI endpoint

            result: dict[str, Any] = {
                "funding_rate": funding_rate,
                "open_interest": open_interest,
                "long_short_ratio": long_short_ratio,
                "ls_ratio_change": long_short_ratio - prev_ratio,
                "oi_change_pct": oi_change_pct,
            }
            _cache[key] = (now + _FUTURES_TTL, result)
            return result

        except Exception as exc:  # noqa: BLE001
            logger.warning("Futures stats unavailable for %s: %s", symbol, exc)
            _fut_disabled_until = now + 300.0  # back-off 5 min
            return None


async def get_klines_multi(
    symbol: str,
    timeframes: list[str],
    limit: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch candles for multiple timeframes concurrently."""
    tf_map = config.TF_MAP
    tasks = {
        tf: get_klines(symbol, tf_map[tf], limit)
        for tf in timeframes
        if tf in tf_map
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    out: dict[str, list[dict[str, Any]]] = {}
    for tf, res in zip(tasks.keys(), results):
        if isinstance(res, Exception):
            logger.error("Failed to fetch %s candles for %s: %s", tf, symbol, res)
            out[tf] = []
        else:
            out[tf] = res  # type: ignore[assignment]
    return out
