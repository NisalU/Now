import { useQuery } from '@tanstack/react-query';
import { api, Performance } from '@/lib/api';

export function PerformancePanel() {
  const { data: perf } = useQuery<Performance>({
    queryKey: ['performance'],
    queryFn: api.performance,
    refetchInterval: 10_000,
  });

  if (!perf || perf.total_trades === 0) {
    return (
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
        <h3 className="text-zinc-400 text-xs uppercase tracking-widest font-semibold mb-3">Performance</h3>
        <p className="text-zinc-600 text-sm text-center py-3">No closed trades yet</p>
      </div>
    );
  }

  const isProfit = (perf.total_pnl ?? 0) >= 0;

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 space-y-3">
      <h3 className="text-zinc-400 text-xs uppercase tracking-widest font-semibold">Performance</h3>

      <div className="grid grid-cols-2 gap-2">
        <Stat label="Win Rate" value={`${perf.win_rate}%`}
          color={perf.win_rate >= 55 ? 'text-emerald-400' : perf.win_rate >= 40 ? 'text-amber-400' : 'text-red-400'} />
        <Stat label="Total PnL" value={`${isProfit ? '+' : ''}${perf.total_pnl?.toFixed(2)}%`}
          color={isProfit ? 'text-emerald-400' : 'text-red-400'} />
        <Stat label="Trades" value={`${perf.winning_trades}W / ${perf.losing_trades}L`} />
        <Stat label="Profit Factor"
          value={perf.profit_factor != null ? perf.profit_factor.toFixed(2) : '—'}
          color={perf.profit_factor != null && perf.profit_factor >= 1.5 ? 'text-emerald-400' : 'text-zinc-300'} />
        <Stat label="Avg Win" value={perf.avg_win != null ? `+${perf.avg_win.toFixed(2)}%` : '—'} color="text-emerald-400" />
        <Stat label="Avg Loss" value={perf.avg_loss != null ? `${perf.avg_loss.toFixed(2)}%` : '—'} color="text-red-400" />
        <Stat label="Max DD" value={perf.max_drawdown != null ? `${perf.max_drawdown.toFixed(2)}%` : '—'} color="text-red-400" />
        <Stat label="Avg RR" value={perf.avg_risk_reward != null ? `1:${perf.avg_risk_reward.toFixed(2)}` : '—'} />
      </div>
    </div>
  );
}

function Stat({ label, value, color = 'text-zinc-200' }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-zinc-800/50 rounded-lg p-2">
      <div className="text-zinc-600 text-xs mb-0.5">{label}</div>
      <div className={`font-mono text-sm font-semibold ${color}`}>{value}</div>
    </div>
  );
}
