import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { SignalBadge } from './SignalBadge';

interface HistoricalTrade {
  id: string;
  symbol: string;
  direction: 'LONG' | 'SHORT';
  entry: number;
  stop_loss: number;
  take_profit_1: number;
  realized_pnl?: number;
  close_reason?: string;
  closed_at?: number;
  risk_reward: number;
  setup_type?: string;
}

export function TradeHistory() {
  const { data: history = [] } = useQuery<HistoricalTrade[]>({
    queryKey: ['trade-history'],
    queryFn: () => api.tradeHistory(30) as unknown as Promise<HistoricalTrade[]>,
    refetchInterval: 15_000,
  });

  if (history.length === 0) {
    return (
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
        <h3 className="text-zinc-400 text-xs uppercase tracking-widest font-semibold mb-3">Trade History</h3>
        <p className="text-zinc-600 text-sm text-center py-4">No closed trades yet</p>
      </div>
    );
  }

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
      <h3 className="text-zinc-400 text-xs uppercase tracking-widest font-semibold mb-3">
        Trade History <span className="text-zinc-600 normal-case">({history.length})</span>
      </h3>
      <div className="space-y-1.5 max-h-64 overflow-y-auto pr-1">
        {history.map(t => {
          const isWin = (t.realized_pnl ?? 0) > 0;
          return (
            <div key={t.id} className="flex items-center gap-2 bg-zinc-800/40 rounded-lg px-3 py-2 text-xs">
              <SignalBadge signal={t.direction} size="sm" />
              <span className="text-zinc-400 font-mono">{t.symbol}</span>
              <span className="text-zinc-600 font-mono">{formatPrice(t.entry)}</span>
              <span className="ml-auto font-mono font-semibold"
                style={{ color: isWin ? '#10b981' : '#ef4444' }}>
                {isWin ? '+' : ''}{t.realized_pnl?.toFixed(2) ?? '0.00'}%
              </span>
              <span className="text-zinc-600 w-16 text-right capitalize">{t.close_reason?.replace('_', ' ')}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function formatPrice(p: number) {
  if (p >= 1000) return p.toFixed(0);
  if (p >= 1) return p.toFixed(4);
  return p.toFixed(6);
}
