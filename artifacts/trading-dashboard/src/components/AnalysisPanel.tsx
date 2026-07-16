import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

interface Props { symbol: string }

const TF_ORDER = ['1D', '4H', '2H', '1H', '30m', '15m', '5m', '1m'];

export function AnalysisPanel({ symbol }: Props) {
  const { data: analysis } = useQuery({
    queryKey: ['analysis', symbol],
    queryFn: () => api.signal(symbol) as Promise<Record<string, unknown>>,
    refetchInterval: 30_000,
  });

  // Fetch the full MTF analysis
  const { data: mtf } = useQuery<Record<string, Record<string, unknown>>>({
    queryKey: ['mtf-analysis', symbol],
    queryFn: async () => {
      const r = await fetch(`${import.meta.env.BASE_URL?.replace(/\/$/, '') ?? ''}/api/analysis/${symbol}`);
      if (!r.ok) return {};
      return r.json();
    },
    refetchInterval: 15_000,
  });

  if (!mtf) return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
      <h3 className="text-zinc-400 text-xs uppercase tracking-widest font-semibold mb-2">MTF Analysis</h3>
      <p className="text-zinc-600 text-sm text-center py-4">Loading analysis…</p>
    </div>
  );

  const availableTFs = TF_ORDER.filter(tf => mtf[tf]);

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 space-y-2">
      <h3 className="text-zinc-400 text-xs uppercase tracking-widest font-semibold">MTF Analysis</h3>

      {/* Market regime */}
      {mtf['_regime'] && (
        <RegimeBadge regime={mtf['_regime'] as Record<string, unknown>} />
      )}

      <div className="space-y-1">
        {availableTFs.map(tf => {
          const d = mtf[tf] as Record<string, unknown>;
          const structure = d?.structure as Record<string, unknown> | undefined;
          const ema = d?.ema as Record<string, unknown> | undefined;
          const rsi = d?.rsi as number | undefined;
          const trend = structure?.trend as string | undefined;

          return (
            <div key={tf} className="flex items-center gap-2 bg-zinc-800/40 rounded px-2.5 py-1.5 text-xs">
              <span className="text-zinc-500 font-mono w-6">{tf}</span>
              <TrendDot trend={trend} />
              <span className={`capitalize ${trendColor(trend)}`}>{trend ?? '—'}</span>
              {ema?.alignment && (
                <span className="text-zinc-600 text-xs ml-1">EMA:{String(ema.alignment).replace('_', ' ')}</span>
              )}
              {rsi != null && (
                <span className={`ml-auto font-mono ${rsiColor(rsi)}`}>RSI {rsi.toFixed(0)}</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function RegimeBadge({ regime }: { regime: Record<string, unknown> }) {
  const type = regime.regime as string;
  const tradeable = regime.tradeable as boolean;
  const COLORS: Record<string, string> = {
    trending_bullish: 'text-emerald-400 bg-emerald-950/40 border-emerald-900/40',
    trending_bearish: 'text-red-400 bg-red-950/40 border-red-900/40',
    range: 'text-zinc-400 bg-zinc-800/40 border-zinc-700/40',
    high_volatility: 'text-amber-400 bg-amber-950/40 border-amber-900/40',
    mixed: 'text-zinc-500 bg-zinc-800/30 border-zinc-700/30',
    accumulation: 'text-blue-400 bg-blue-950/40 border-blue-900/40',
    distribution: 'text-orange-400 bg-orange-950/40 border-orange-900/40',
  };
  return (
    <div className={`inline-flex items-center gap-2 rounded px-2 py-1 border text-xs ${COLORS[type] ?? 'text-zinc-400 bg-zinc-800 border-zinc-700'}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${tradeable ? 'bg-emerald-400' : 'bg-zinc-600'}`} />
      <span className="capitalize font-semibold">{type?.replace('_', ' ')}</span>
      <span className="text-zinc-600">— {tradeable ? 'tradeable' : 'gated'}</span>
    </div>
  );
}

function TrendDot({ trend }: { trend?: string }) {
  const color = trendColor(trend);
  return <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
    color === 'text-emerald-400' ? 'bg-emerald-500' :
    color === 'text-red-400' ? 'bg-red-500' : 'bg-zinc-600'
  }`} />;
}

function trendColor(t?: string) {
  if (!t) return 'text-zinc-600';
  if (t === 'bullish') return 'text-emerald-400';
  if (t === 'bearish') return 'text-red-400';
  return 'text-zinc-400';
}

function rsiColor(rsi: number) {
  if (rsi >= 70) return 'text-red-400';
  if (rsi <= 30) return 'text-emerald-400';
  return 'text-zinc-400';
}
