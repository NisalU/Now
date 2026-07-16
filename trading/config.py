"""Configuration for the AI Crypto Trading System.

All values can be overridden with environment variables or a .env file.
Never hardcode secrets — use environment variables.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# ── Server ────────────────────────────────────────────────────────────────────
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "*").split(",")

# ── Binance ───────────────────────────────────────────────────────────────────
BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")

SPOT_ENDPOINTS: list[str] = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://data-api.binance.vision",
]
FUTURES_ENDPOINTS: list[str] = [
    "https://fapi.binance.com",
]
WS_ENDPOINTS: list[str] = [
    "wss://data-stream.binance.vision/ws",
    "wss://stream.binance.com:9443/ws",
    "wss://stream.binance.com:443/ws",
]

# ── Markets ───────────────────────────────────────────────────────────────────
DEFAULT_SYMBOL: str = os.getenv("DEFAULT_SYMBOL", "BTCUSDT")
SYMBOLS: list[str] = os.getenv(
    "SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT"
).split(",")

# Canonical timeframe names → Binance interval strings
TIMEFRAMES: list[str] = ["1D", "4H", "2H", "1H", "30m", "15m", "5m", "1m"]
TF_MAP: dict[str, str] = {
    "1D": "1d",
    "4H": "4h",
    "2H": "2h",
    "1H": "1h",
    "30m": "30m",
    "15m": "15m",
    "5m": "5m",
    "1m": "1m",
}

KLINE_LIMIT: int = int(os.getenv("KLINE_LIMIT", "300"))

# ── AI (Groq) ─────────────────────────────────────────────────────────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_FALLBACK_MODELS: list[str] = [
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]
AI_MAX_TOKENS: int = int(os.getenv("AI_MAX_TOKENS", "1024"))
AI_TEMPERATURE: float = float(os.getenv("AI_TEMPERATURE", "0.1"))
AI_TIMEOUT: float = float(os.getenv("AI_TIMEOUT", "30.0"))

# ── AI call triggers ───────────────────────────────────────────────────────────
AI_MIN_INTERVAL_SECONDS: int = int(os.getenv("AI_MIN_INTERVAL_SECONDS", "60"))
MIN_AI_CALL_INTERVAL: int = AI_MIN_INTERVAL_SECONDS  # alias used by engine
AI_TRIGGER_15M_CLOSE: bool = os.getenv("AI_TRIGGER_15M_CLOSE", "true").lower() == "true"
AI_TRIGGER_STRUCTURE_CHANGE: bool = (
    os.getenv("AI_TRIGGER_STRUCTURE_CHANGE", "true").lower() == "true"
)
AI_TRIGGER_LIQUIDITY_SWEEP: bool = (
    os.getenv("AI_TRIGGER_LIQUIDITY_SWEEP", "true").lower() == "true"
)
AI_TRIGGER_HIGH_VOLATILITY: bool = (
    os.getenv("AI_TRIGGER_HIGH_VOLATILITY", "true").lower() == "true"
)

# ── Risk validation ────────────────────────────────────────────────────────────
MIN_RISK_REWARD: float = float(os.getenv("MIN_RISK_REWARD", "2.0"))
MAX_ENTRY_ATR_DISTANCE: float = float(os.getenv("MAX_ENTRY_ATR_DISTANCE", "3.0"))

# ── Trade manager ──────────────────────────────────────────────────────────────
BREAK_EVEN_TRIGGER_RR: float = float(os.getenv("BREAK_EVEN_TRIGGER_RR", "1.0"))
TRAILING_STOP_ATR_MULT: float = float(os.getenv("TRAILING_STOP_ATR_MULT", "1.5"))
PARTIAL_TP_PERCENT: float = float(os.getenv("PARTIAL_TP_PERCENT", "0.5"))
MAX_TRADE_HISTORY: int = int(os.getenv("MAX_TRADE_HISTORY", "500"))

# ── Market regime thresholds ────────────────────────────────────────────────────
REGIME_VOLATILITY_SPIKE: float = float(os.getenv("REGIME_VOLATILITY_SPIKE", "1.8"))
REGIME_COMPRESSION_TIGHT: float = float(os.getenv("REGIME_COMPRESSION_TIGHT", "0.45"))

# ── WebSocket ─────────────────────────────────────────────────────────────────
WS_RECONNECT_DELAY: float = float(os.getenv("WS_RECONNECT_DELAY", "5.0"))
WS_PING_INTERVAL: float = float(os.getenv("WS_PING_INTERVAL", "20.0"))
WS_PING_TIMEOUT: float = float(os.getenv("WS_PING_TIMEOUT", "10.0"))
