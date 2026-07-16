const BASE = import.meta.env.BASE_URL?.replace(/\/$/, '') ?? '';
const API = `${BASE}/api`;

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

export interface SymbolInfo {
  symbol: string;
  price?: number;
  change_pct?: number;
  volume_24h?: number;
  high_24h?: number;
  low_24h?: number;
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface SystemStatus {
  symbols: string[];
  timeframes: string[];
  ws: { connected: boolean; reconnects: number; last_msg_age_s?: number };
  ai: { enabled: boolean; model: string; last_call_ms?: number; calls_total: number };
  clients: number;
  active_trades: number;
}

export interface Performance {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  total_pnl: number;
  avg_win?: number;
  avg_loss?: number;
  profit_factor?: number;
  max_drawdown?: number;
  avg_risk_reward?: number;
  best_trade?: number;
  worst_trade?: number;
}

export const api = {
  status: () => get<SystemStatus>('/status'),
  symbols: () => get<SymbolInfo[]>('/symbols'),
  candles: (symbol: string, timeframe: string, limit = 200) =>
    get<Candle[]>(`/candles/${symbol}/${timeframe}?limit=${limit}`),
  signal: (symbol: string, force = false) =>
    get<Record<string, unknown>>(`/signal/${symbol}${force ? '?force=true' : ''}`),
  signalHistory: (limit = 50) =>
    get<Record<string, unknown>[]>(`/signals/history?limit=${limit}`),
  trades: () => get<Record<string, unknown>[]>('/trades'),
  tradeHistory: (limit = 50) =>
    get<Record<string, unknown>[]>(`/trades/history?limit=${limit}`),
  closeTrade: (id: string) =>
    fetch(`${API}/trades/${id}/close`, { method: 'POST' }).then(r => r.json()),
  performance: () => get<Performance>('/performance'),
};
