import { useQuery } from '@tanstack/react-query';
import { api, SymbolInfo } from '@/lib/api';
import { TickData } from '@/hooks/useTrading';
import { cn } from '@/lib/utils';

interface Props {
  selected: string;
  onSelect: (s: string) => void;
  ticks: Record<string, TickData>;
}

export function SymbolTicker({ selected, onSelect, ticks }: Props) {
  const { data: symbols = [] } = useQuery<SymbolInfo[]>({
    queryKey: ['symbols'],
    queryFn: api.symbols,
    refetchInterval: 30_000,
  });

  return (
    <div className="flex gap-1.5 overflow-x-auto pb-1 scrollbar-none">
      {symbols.map(s => {
        const live = ticks[s.symbol];
        const price = live?.price ?? s.price;
        const change = s.change_pct;
        const isPos = (change ?? 0) >= 0;
        const isActive = selected === s.symbol;

        return (
          <button
            key={s.symbol}
            onClick={() => onSelect(s.symbol)}
            className={cn(
              'flex-shrink-0 rounded-lg px-3 py-2 text-left border transition-all',
              isActive
                ? 'bg-zinc-800 border-zinc-600 shadow-inner'
                : 'bg-zinc-900 border-zinc-800 hover:border-zinc-700'
            )}
          >
            <div className="text-xs font-semibold text-zinc-200 whitespace-nowrap">
              {s.symbol.replace('USDT', '')}
              <span className="text-zinc-500">/USDT</span>
            </div>
            {price && (
              <div className="font-mono text-xs text-zinc-300 mt-0.5">{formatPrice(price)}</div>
            )}
            {change != null && (
              <div className={`text-xs font-mono ${isPos ? 'text-emerald-400' : 'text-red-400'}`}>
                {isPos ? '+' : ''}{change.toFixed(2)}%
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}

function formatPrice(p: number) {
  if (p >= 10000) return p.toFixed(0);
  if (p >= 100) return p.toFixed(2);
  if (p >= 1) return p.toFixed(4);
  return p.toFixed(5);
}
