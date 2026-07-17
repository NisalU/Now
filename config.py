"""Configuration — High Volatility Altcoin Scalp Signal Bot."""
import os

# ---- Server ----
HOST = "0.0.0.0"
PORT = 8000

# ---- Market data ----
# Pinned symbols: always watched regardless of scanner results.
# Scanner adds high-vol coins on top dynamically.
DEFAULT_SYMBOL = "APTUSDT"
# Pinned symbols — keep only genuinely volatile altcoins.
# Slow large-caps (ETH, BNB, SOL, LTC, ADA, DOT, etc.) are excluded;
# they dilute the scanner and the AI analysis queue.
PINNED_SYMBOLS = [
    "BTCUSDT",   # market reference only
    "APTUSDT", "INJUSDT", "SUIUSDT", "TIAUSDT",
    "SEIUSDT", "WIFUSDT", "BONKUSDT", "PEPEUSDT",
    "FETUSDT", "NOTUSDT", "TURBOUSDT", "MEWUSDT",
]
SYMBOLS = list(PINNED_SYMBOLS)   # Scanner updates this at runtime

DEFAULT_INTERVAL = "5m"
INTERVALS        = ["1m", "3m", "5m", "15m", "1h", "4h", "1d"]
KLINE_LIMIT      = 300

# Binance REST endpoints (tried in order)
SPOT_ENDPOINTS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://data-api.binance.vision",
]
FUTURES_ENDPOINTS = [
    "https://fapi.binance.com",
]

REFRESH_SECONDS = 15   # faster refresh cycle for scalp focus

# ---- Binance API credentials (optional) ----
BINANCE_API_KEY    = ""
BINANCE_API_SECRET = ""

# ---- Confluence engine — tuned for high-vol altcoins ----
WEIGHTS = {
    "ema_trend":          10,
    "support_resistance": 14,   # S/R key for altcoin bounce entries
    "trendlines":          6,
    "patterns":            8,
    "fibonacci":           8,
    "smc":                16,   # SMC (order blocks, FVGs) heavily weighted for alts
    "liquidity_sweep":    14,   # sweeps are the #1 signal on volatile coins
    "orderflow_cvd":      14,
    "auction_market":      6,
    "fundamentals":        4,
}

SIGNAL_THRESHOLD = 18    # slightly lower threshold — alts are noisier
STRONG_THRESHOLD = 40
MAX_SIGNAL_HISTORY = 500
ENGINE_SIGNAL_FEED = False

# ---- AI analyst — one-shot directional swing signals ----
AI_INTERVAL      = "15m"   # primary analysis timeframe (15-min candles)
AI_HTF_INTERVAL  = "1h"    # higher-timeframe confirmation
AI_REFRESH_SECONDS = 45    # re-analyze every 45 s (15m candles change slowly)

AI_MIN_CALL_INTERVAL = 2.1

# Server-side risk gate
AI_MIN_RISK_REWARD        = 1.5   # 1.5:1 minimum for swing entries
AI_MAX_ENTRY_ATR_DISTANCE = 3.0

# Model rotation cooldown
MODEL_RL_COOLDOWN = 60

# ---- AI critic ----
AI_CRITIC_ENABLED = False

# ---- Signal memory ----
SIGNAL_MEMORY_LOOKBACK = 3

# ---- Pipeline log ----
PIPELINE_LOG_MAX = 100

# ---- Active-signal lock ----
ACTIVE_SIGNAL_LOCK = True

# ---- Market regime — calibrated for altcoin volatility ----
REGIME_COMPRESSION_TIGHT  = 0.35  # alts compress harder before explosive moves
REGIME_VOLATILITY_SPIKE   = 2.5   # alts regularly spike 3–10% in one candle

# Token budgets — response JSON is tiny (~150 tokens); keep limits tight
# to stay within Groq TPM quotas (llama-3.3-70b: 12k TPM).
AI_MAX_TOKENS        = 350
AI_MAX_TOKENS_RETRY  = 500
AI_JSON_FAIL_COOLDOWN = 30

# Prompt sizing — lean context keeps total request < 3000 tokens
AI_PROMPT_CANDLES    = 5
AI_PROMPT_CVD_POINTS = 6
AI_PROMPT_MEMORY_ROWS = 2

# ---- Limit signals ----
LIMIT_SIGNALS_ENABLED = True

# ---- Coin scanner — focused on fast movers, not slow large-caps ----
SCANNER_ENABLED           = True
SCANNER_INTERVAL          = 240        # scan every 4 minutes
SCANNER_MIN_VOLUME_USDT   = 3_000_000  # $3 M minimum — catch micro-cap rockets
SCANNER_VOLATILITY_MIN_PCT = 4.0       # 4%+ 24h move — only real movers
SCANNER_TOP_N             = 25         # top 25 coins by volatility score
# Large-caps excluded from scanner (too slow for high-vol altcoin mode)
SCANNER_EXCLUDE_SLOW_CAPS = {
    "BTC", "ETH", "BNB", "SOL", "LTC", "ADA", "DOT", "AVAX",
    "LINK", "UNI", "AAVE", "CRV", "MATIC", "OP", "ARB",
    "XRP", "ATOM", "ALGO", "FTM", "SAND", "MANA", "ICP",
}
