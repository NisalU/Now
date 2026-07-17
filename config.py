"""Configuration — High Volatility Altcoin Scalp Signal Bot."""
import os

# ---- Server ----
HOST = "0.0.0.0"
PORT = 8000

# ---- Market data ----
# Pinned symbols: always watched regardless of scanner results.
# Scanner adds high-vol coins on top dynamically.
DEFAULT_SYMBOL = "SOLUSDT"
PINNED_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "NEARUSDT", "APTUSDT", "ARBUSDT", "INJUSDT", "SUIUSDT",
    "TIAUSDT", "SEIUSDT", "WIFUSDT", "BONKUSDT", "PEPEUSDT",
    "FETUSDT",
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

# ---- AI analyst — scalp focus ----
AI_INTERVAL      = "5m"    # primary analysis timeframe (scalp)
AI_HTF_INTERVAL  = "15m"   # higher-timeframe confirmation
AI_REFRESH_SECONDS = 30    # re-analyze every 30 s for fast scalp signals

AI_SCALP_INTERVALS  = ["1m", "3m", "5m"]   # timeframes AI can signal on
AI_MIN_CALL_INTERVAL = 2.1

# Server-side risk gate — relaxed for high-vol altcoins
AI_MIN_RISK_REWARD        = 1.0   # 1:1 acceptable for scalps
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

# Token budgets
AI_MAX_TOKENS        = 2000
AI_MAX_TOKENS_RETRY  = 3000
AI_JSON_FAIL_COOLDOWN = 30

# Prompt sizing — more context for short timeframes
AI_PROMPT_CANDLES    = 10
AI_PROMPT_CVD_POINTS = 20
AI_PROMPT_MEMORY_ROWS = 3

# ---- Limit signals ----
LIMIT_SIGNALS_ENABLED = True

# ---- Coin scanner ----
SCANNER_ENABLED           = True
SCANNER_INTERVAL          = 300        # scan every 5 minutes
SCANNER_MIN_VOLUME_USDT   = 20_000_000 # $20 M minimum 24h USDT volume
SCANNER_VOLATILITY_MIN_PCT = 2.0       # minimum 2% 24h absolute move
SCANNER_TOP_N             = 20         # top 20 coins by volatility score
