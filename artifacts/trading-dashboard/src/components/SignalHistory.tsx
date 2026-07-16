import { Signal } from '@/hooks/useTrading';
import { SignalBadge } from './SignalBadge';

interface Props {
  history: Signal[];
}

export function SignalHistory({ history }: Props) {
  const tradeable = history.filter(s => s.signal !== 'WAIT');

  if (tradeable.length === 0) return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
      <h3 className="text-zinc-400 text-xs uppercase tracking-widest font-semibold mb-3">Signal History</h3>
      <p className="text-zinc-600 text-sm text-center py-3">No actionable signals yet</p>
    </div>
  );

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
      <h3 className="text-zinc-400 text-xs uppercase tracking-widest font-semibold mb-3">
        Signal History <span className="text-zinc-600 normal-case">({tradeable.length})</span>
      </h3>
      <div className="space-y-1.5 max-h-52 overflow-y-auto pr-1">
        {tradeable.map((sig, i) => (
          <div key={i} className="flex items-center gap-2 bg-zinc-800/40 rounded px-2.5 py-1.5 text-xs">
            <SignalBadge signal={sig.signal} size="sm" />
            <span className="text-zinc-400 font-mono w-16">{sig.symbol}</span>
            {sig.entry && <span className="text-zinc-400 font-mono">{formatPrice(sig.entry)}</span>}
            {sig.confidence > 0 && (
              <span className={`ml-auto ${
                sig.confidence >= 0.7 ? 'text-emerald-400' :
                sig.confidence >= 0.5 ? 'text-amber-400' : 'text-zinc-500'
              }`}>{Math.round(sig.confidence * 100)}%</span>
            )}
            <span className="text-zinc-700 w-12 text-right">{formatAge(sig.updated)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function formatPrice(p: number) {
  if (p >= 1000) return p.toFixed(0);
  if (p >= 1) return p.toFixed(4);
  return p.toFixed(6);
}

function formatAge(ts: number) {
  const secs = Math.floor(Date.now() / 1000 - ts);
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  return `${Math.floor(secs / 3600)}h`;
}
