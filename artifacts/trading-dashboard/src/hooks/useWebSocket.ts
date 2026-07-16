import { useEffect, useRef, useCallback, useState } from 'react';

export type WSMessage =
  | { type: 'tick'; symbol: string; price: number; ts: number }
  | { type: 'signal'; symbol: string; signal: string; entry: number | null; stop_loss: number | null; take_profit_1: number | null; confidence: number; reasoning: string; setup_type: string; risk_reward?: number; updated: number; trigger?: string }
  | { type: 'trade_event'; event: string; trade: Record<string, unknown> }
  | { type: 'analysis'; symbol: string; data: Record<string, unknown> }
  | { type: 'status'; [key: string]: unknown }
  | { type: 'heartbeat'; ts: number }
  | { type: 'pong' };

type Handler = (msg: WSMessage) => void;

const BASE = import.meta.env.BASE_URL?.replace(/\/$/, '') ?? '';
const WS_URL = (() => {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}${BASE}/ws`;
})();

export function useWebSocket(onMessage: Handler) {
  const wsRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [connected, setConnected] = useState(false);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      // Heartbeat ping
      timerRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping');
      }, 20_000);
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as WSMessage;
        onMessageRef.current(msg);
      } catch {}
    };

    ws.onclose = () => {
      setConnected(false);
      if (timerRef.current) clearInterval(timerRef.current);
      // Reconnect after 3s
      setTimeout(connect, 3000);
    };

    ws.onerror = () => ws.close();
  }, []);

  useEffect(() => {
    connect();
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return connected;
}
