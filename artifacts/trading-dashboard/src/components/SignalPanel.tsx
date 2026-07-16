import { Signal } from '@/hooks/useTrading';
import { SignalBadge } from './SignalBadge';
import { api } from '@/lib/api';
import { useState } from 'react';

interface Props {
  symbol: string;
  signal?: Signal;
  currentPrice?: number;
  onRefresh?: () => void;
}

export function SignalPanel({ symbol, signal, currentPrice, onRefresh }: Props) {
  const [loading, setLoading] = useState(false);

  const forceAnalysis = async () => {
    setLoading(true);
    try {
      await api.signal(symbol, true);
      onRefresh?.();
    } catch {}
    finally { setLoading(false); }
  };

  const rr = signal?.risk_reward;
  const confidence = signal ? Math.round(signal.confidence * 100) : null;

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-zinc-400 text-xs uppercase tracking-widest font-semibold">AI Signal</h3>
        <button
          onClick={forceAnalysis}
          disabled={loading}
          className="text-xs text-zinc-500 hover:text-zinc-300 border border-zinc-700 rounded px-2 py-0.5 transition-colors disabled:opacity-50"
        >
          {loading ? 'Analyzing…' : '↻ Force'}
        </button>
      </div>

      {!signal ? (
        <div className="text-zinc-600 text-sm text-center py-4">Waiting for first analysis…</div>
      ) : (
        <>
          <div className="flex items-center gap-3">
            <SignalBadge signal={signal.signal} size="lg" />
            {confidence !== null && (
              <div className="flex-1">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-zinc-500">Confidence</span>
                  <span className="text-xs text-zinc-300">{confidence}%</span>
                </div>
                <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      confidence >= 70 ? 'bg-emerald-500' :
                      confidence >= 40 ? 'bg-amber-500' : 'bg-zinc-600'
                    }`}
                    style={{ width: `${confidence}%` }}
                  />
                </div>
              </div>
            )}
          </div>

          {signal.signal !== 'WAIT' && (
            <div className="grid grid-cols-2 gap-2 text-sm">
              <LevelRow label="Entry" value={signal.entry} color="text-amber-400" />
              <LevelRow label="Stop Loss" value={signal.stop_loss} color="text-red-400" />
              <LevelRow label="Take Profit" value={signal.take_profit_1} color="text-emerald-400" />
              {rr && (
                <div className="bg-zinc-800/50 rounded p-2">
                  <div className="text-zinc-500 text-xs">Risk : Reward</div>
                  <div className={`font-mono font-semibold ${rr >= 3 ? 'text-emerald-400' : rr >= 2 ? 'text-amber-400' : 'text-zinc-300'}`}>
                    1 : {rr.toFixed(2)}
                  </div>
                </div>
              )}
            </div>
          )}

          {signal.setup_type && (
            <div className="text-xs text-zinc-400">
              <span className="text-zinc-600">Setup: </span>{signal.setup_type}
            </div>
          )}

          {signal.reasoning && (
            <div className="bg-zinc-800/40 rounded-lg p-3 text-xs text-zinc-400 leading-relaxed">
              {signal.reasoning}
            </div>
          )}

          {signal.gated && signal.gate_reason && (
            <div className="bg-amber-950/30 border border-amber-900/40 rounded p-2 text-xs text-amber-400">
              ⚠ Gated: {signal.gate_reason}
            </div>
          )}

          <div className="flex items-center justify-between text-xs text-zinc-600">
            <span>Updated {formatAge(signal.updated)}</span>
            {signal.ai_latency_ms && <span>{signal.ai_latency_ms}ms</span>}
            {signal.trigger && <span>via {signal.trigger}</span>}
          </div>
        </>
      )}
    </div>
  );
}

function LevelRow({ label, value, color }: { label: string; value: number | null | undefined; color: string }) {
  return (
    <div className="bg-zinc-800/50 rounded p-2">
      <div className="text-zinc-500 text-xs">{label}</div>
      <div className={`font-mono font-semibold ${color}`}>{value != null ? formatPrice(value) : '—'}</div>
    </div>
  );
}

function formatPrice(p: number) {
  if (p >= 1000) return p.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  if (p >= 1) return p.toFixed(4);
  return p.toFixed(6);
}

function formatAge(ts: number) {
  const secs = Math.floor(Date.now() / 1000 - ts);
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}
