# AI Crypto Trading System

## Overview

A production-ready AI cryptocurrency trading system with a React dashboard and Python FastAPI backend.

### Architecture

```
Binance REST/WS → Analysis Engine → Context Builder → Groq AI → Risk Validator → Trade Manager
                                                                                   ↓
                                                                          React Dashboard (WebSocket)
```

**Components:**
- **`trading/`** — Python FastAPI backend (standalone, not in pnpm monorepo)
- **`artifacts/trading-dashboard/`** — React + Vite + Tailwind dashboard
- **`lib/api-spec/`** — OpenAPI spec + generated TypeScript client (orval)

### Trading Logic

1. **Data Layer** — Binance REST + WebSocket, auto-reconnect, multi-timeframe cache: 1D, 4H, 2H, 1H, 30m, 15m, 5m, 1m
2. **Analysis Engine** — 100% local (no AI): EMA 20/50/200, VWAP, ATR, RSI, MACD, Volume/CVD, Order Blocks, FVGs, Liquidity Sweeps, BOS/CHoCH, Fibonacci, Funding Rate, OI, Market Regime
3. **Context Builder** — compact token-efficient JSON for AI
4. **AI Trader** — single Groq model (`llama-3.3-70b-versatile`), returns LONG | SHORT | WAIT
5. **Risk Validator** — SL/TP/RR≥2/entry validity checks; gates invalid signals
6. **Trade Manager** — break-even, trailing stop, partial TP, history, performance

### AI Call Triggers

- New 15m candle closes (primary)
- BOS or CHoCH detected
- Liquidity sweep detected
- Volatility spike
- Manual force via `GET /signal/{symbol}?force=true`

### Minimum interval between AI calls: 60s per symbol

---

## Running the project

### Python backend
```bash
cd trading
pip install -r requirements.txt
uvicorn trading.main:app --host 0.0.0.0 --port 8000 --reload
```

### React dashboard
The dashboard workflow is managed by Replit (`artifacts/trading-dashboard: web`).
The Vite dev server proxies `/api` and `/ws` to `localhost:8000`.

### Tests
```bash
cd trading
pip install -r requirements.txt
python -m pytest tests/ -v
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | — | **Required** for AI signals |
| `BINANCE_API_KEY` | — | Optional (public endpoints work without) |
| `BINANCE_API_SECRET` | — | Optional |
| `SYMBOLS` | `BTCUSDT,...` | Comma-separated symbols |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model |
| `MIN_RISK_REWARD` | `2.0` | Minimum RR for a valid trade |
| `AI_MIN_INTERVAL_SECONDS` | `60` | Minimum seconds between AI calls per symbol |
| `LOG_LEVEL` | `INFO` | Python log level |

See `trading/.env.example` for the full list.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/healthz` | Health check |
| GET | `/api/status` | System status (WS, AI, clients) |
| GET | `/api/symbols` | Configured symbols with 24h stats |
| GET | `/api/candles/{symbol}/{timeframe}` | OHLCV candles |
| GET | `/api/signal/{symbol}` | Latest AI signal |
| GET | `/api/signal/{symbol}?force=true` | Force fresh AI analysis |
| GET | `/api/signals/history` | Recent signal history |
| GET | `/api/analysis/{symbol}` | Full MTF analysis |
| GET | `/api/market-regime/{symbol}` | Current market regime |
| GET | `/api/trades` | Active trades |
| GET | `/api/trades/history` | Closed trade history |
| POST | `/api/trades/{id}/close` | Manually close a trade |
| GET | `/api/performance` | Performance metrics |
| WS | `/ws` | Real-time feed (tick, signal, trade_event, analysis, status) |

---

## User Preferences

- No confluence score — analysis is purely informational context for AI
- Single Groq model — no critic, no second pass
- MTF priority: 1D → 4H → 2H → 1H → 30m → 15m → 5m → 1m
- Backend is standalone Python; not inside the pnpm monorepo
