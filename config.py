"""Configuration for the AI Trading Signal Bot."""
import os

# ---- Server ----
HOST = "0.0.0.0"   # listen on all interfaces so other devices on LAN can open the dashboard
PORT = 8000

# ---- Market data ----
DEFAULT_SYMBOL = "BTCUSDT"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
DEFAULT_INTERVAL = "15m"
INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"]
KLINE_LIMIT = 300           # candles fetched per analysis

# Binance REST endpoints, tried in order (first that works is cached).
# data-api.binance.vision is Binance's public market-data mirror and
# usually works even where api.binance.com is geo-restricted.
SPOT_ENDPOINTS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://data-api.binance.vision",
]
FUTURES_ENDPOINTS = [
    "https://fapi.binance.com",
]

REFRESH_SECONDS = 20        # background analysis loop interval

# ---- Binance API credentials (optional) ----
# Set BINANCE_API_KEY and BINANCE_API_SECRET in your environment or .env file.
# Required for: account info, order placement, private endpoints.
# Not required for: market data (klines, ticker, futures stats).
# With an API key, public endpoints also benefit from higher rate limits.
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")


def _load_env_file():
    """Load BINANCE_API_KEY/SECRET from a local .env file if not already set."""
    global BINANCE_API_KEY, BINANCE_API_SECRET
    if BINANCE_API_KEY and BINANCE_API_SECRET:
        return
    base = os.path.dirname(__file__)
    for name in (".env", ".env.local"):
        try:
            with open(os.path.join(base, name)) as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("BINANCE_API_KEY=") and not BINANCE_API_KEY:
                        BINANCE_API_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("BINANCE_API_SECRET=") and not BINANCE_API_SECRET:
                        BINANCE_API_SECRET = line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue


_load_env_file()

# ---- Confluence engine ----
# Weight of each strategy in the composite score (must not exceed 100 total).
WEIGHTS = {
    "ema_trend": 12,
    "support_resistance": 12,
    "trendlines": 8,
    "patterns": 8,
    "fibonacci": 8,
    "smc": 14,
    "liquidity_sweep": 10,
    "orderflow_cvd": 14,
    "auction_market": 8,
    "fundamentals": 6,
}

SIGNAL_THRESHOLD = 20       # |composite| >= threshold fires a LONG/SHORT signal
STRONG_THRESHOLD = 45       # strong signal label
MAX_SIGNAL_HISTORY = 200    # kept in memory / persisted to signals.json
ENGINE_SIGNAL_FEED = False  # False: dashboard feed shows AI trade calls only;
                            # engine signals are still computed and persisted internally

# ---- OpenRouter AI analyst (discretionary structure/liquidity read) ----
# Requires OPENROUTER_API_KEY in the environment or a local .env file.
# Set OPENROUTER_MODEL to pin a specific model (overrides the priority list).
AI_INTERVAL = "1h"          # primary chart the AI analyst monitors
AI_HTF_INTERVAL = "4h"      # higher-timeframe chart used for top-down context
AI_REFRESH_SECONDS = 60     # how often the AI re-analyzes each active symbol

# Server-side risk gate — arithmetic checks only (entry/stop/tp1 validity,
# minimum R:R, entry-not-chasing). Market regime and trade quality are passed
# to the AI as *context* but do NOT block or filter any signal.
AI_MIN_RISK_REWARD = 1.2        # reject any LONG/SHORT below this R:R to TP1
AI_MAX_ENTRY_ATR_DISTANCE = 2.5  # reject entries this many ATRs from live price (chase guard)

# ---- OpenRouter model priority list ----
# Models are tried in order.  When a model returns 429 it is skipped for
# MODEL_RL_COOLDOWN seconds and the next one is tried automatically.
# The last successful model is cached and tried first on the next run.
#
# Setup:
#   1. Get a free key at https://openrouter.ai
#   2. Add to your .env file:  OPENROUTER_API_KEY=sk-or-v1-...
#   3. Optionally pin a specific model:  OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct:free
MODEL_RL_COOLDOWN = 90      # seconds to skip a rate-limited model before retrying

# ---- Market regime (informational context only — not a gate) ----
# These thresholds drive the regime classifier whose output is passed to the AI
# as context. The regime no longer blocks any AI call.
REGIME_COMPRESSION_TIGHT = 0.45
REGIME_VOLATILITY_SPIKE = 1.8

# ---- AI critic (second-pass review) ----
AI_CRITIC_ENABLED = True

# ---- Signal memory ----
SIGNAL_MEMORY_LOOKBACK = 3  # past setups (same symbol) shown to the AI as context
