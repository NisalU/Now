import { useState, useCallback, useRef } from 'react';
import { useWebSocket, WSMessage } from './useWebSocket';

export interface Signal {
  symbol: string;
  signal: 'LONG' | 'SHORT' | 'WAIT';
  entry: number | null;
  stop_loss: number | null;
  take_profit_1: number | null;
  take_profit_2?: number | null;
  confidence: number;
  reasoning: string;
  setup_type: string;
  risk_reward?: number;
  updated: number;
  trigger?: string;
  htf_bias?: string;
  gated?: boolean;
  gate_reason?: string | null;
  ai_latency_ms?: number | null;
}

export interface Trade {
  id: string;
  symbol: string;
  direction: 'LONG' | 'SHORT';
  entry: number;
  stop_loss: number;
  take_profit_1: number;
  status: string;
  unrealized_pnl?: number | null;
  realized_pnl?: number | null;
  risk_reward: number;
  opened_at: number;
  close_reason?: string | null;
}

export interface TickData {
  price: number;
  ts: number;
}

export interface TradingState {
  ticks: Record<string, TickData>;
  signals: Record<string, Signal>;
  signalHistory: Signal[];
  trades: Trade[];
  connected: boolean;
}

export function useTrading() {
  const [ticks, setTicks] = useState<Record<string, TickData>>({});
  const [signals, setSignals] = useState<Record<string, Signal>>({});
  const [signalHistory, setSignalHistory] = useState<Signal[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);

  const handleMessage = useCallback((msg: WSMessage) => {
    switch (msg.type) {
      case 'tick':
        setTicks(prev => ({ ...prev, [msg.symbol]: { price: msg.price, ts: msg.ts } }));
        break;
      case 'signal':
        if (msg.signal !== 'WAIT' || true) {
          const sig = msg as unknown as Signal;
          setSignals(prev => ({ ...prev, [msg.symbol]: sig }));
          setSignalHistory(prev => [sig, ...prev].slice(0, 100));
        }
        break;
      case 'trade_event':
        if (msg.event === 'closed') {
          setTrades(prev => prev.filter(t => t.id !== (msg.trade as Trade).id));
        } else if (msg.event === 'opened') {
          setTrades(prev => [...prev, msg.trade as Trade]);
        }
        break;
    }
  }, []);

  const connected = useWebSocket(handleMessage);

  return { ticks, signals, signalHistory, trades, connected };
}
