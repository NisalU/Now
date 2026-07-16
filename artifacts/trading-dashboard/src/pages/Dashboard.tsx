import { useState, useEffect, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTrading } from '@/hooks/useTrading';
import { api, Candle } from '@/lib/api';
import { StatusBar } from '@/components/StatusBar';
import { SymbolTicker } from '@/components/SymbolTicker';
import { PriceChart } from '@/components/PriceChart';
import { SignalPanel } from '@/components/SignalPanel';
import { AnalysisPanel } from '@/components/AnalysisPanel';
import { PerformancePanel } from '@/components/PerformancePanel';
import { TradeHistory } from '@/components/TradeHistory';
import { SignalHistory } from '@/components/SignalHistory';

const TIMEFRAMES = ['1m', '5m', '15m', '1H', '4H', '1D'];

export default function Dashboard() {
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [timeframe, setTimeframe] = useState('15m');
  const { ticks, signals, signalHistory, connected } = useTrading();

  const currentPrice = ticks[symbol]?.price;
  const signal = signals[symbol];

  const { data: candles = [], refetch: refetchCandles } = useQuery<Candle[]>({
    queryKey: ['candles', symbol, timeframe],
    queryFn: () => api.candles(symbol, timeframe, 200),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  // Update last candle close when live tick arrives
  const liveCandles: Candle[] = candles.length > 0 && currentPrice
    ? [
        ...candles.slice(0, -1),
        { ...candles[candles.length - 1], close: currentPrice },
      ]
    : candles;

  const handleSymbolChange = useCallback((s: string) => {
    setSymbol(s);
  }, []);

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 flex flex-col font-sans">
      {/* Status bar */}
      <StatusBar wsConnected={connected} />

      {/* Header */}
      <header className="border-b border-zinc-800 px-4 py-3 flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span className="text-lg font-bold bg-gradient-to-r from-violet-400 to-blue-400 bg-clip-text text-transparent select-none">
            ⚡ TradingAI
          </span>
        </div>
        <div className="flex-1 overflow-x-auto">
          <SymbolTicker selected={symbol} onSelect={handleSymbolChange} ticks={ticks} />
        </div>
      </header>

      {/* Main content */}
      <div className="flex-1 grid grid-cols-1 xl:grid-cols-[1fr_340px] gap-0 overflow-hidden">
        {/* Left: Chart + Analysis */}
        <div className="flex flex-col min-w-0 border-r border-zinc-800">
          {/* Chart header */}
          <div className="flex items-center gap-3 px-4 pt-3 pb-2 border-b border-zinc-800/50">
            <div className="flex items-baseline gap-2">
              <span className="text-xl font-bold font-mono">
                {currentPrice ? formatPrice(currentPrice) : '—'}
              </span>
              <span className="text-zinc-500 text-sm">{symbol}</span>
            </div>
            <div className="flex gap-1 ml-auto">
              {TIMEFRAMES.map(tf => (
                <button
                  key={tf}
                  onClick={() => setTimeframe(tf)}
                  className={`text-xs px-2 py-0.5 rounded transition-all ${
                    timeframe === tf
                      ? 'bg-zinc-700 text-zinc-100'
                      : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  {tf}
                </button>
              ))}
            </div>
          </div>

          {/* Chart */}
          <div className="flex-1 min-h-0 px-0 pt-0">
            <PriceChart
              candles={liveCandles}
              signal={signal}
              height={340}
            />
          </div>

          {/* Bottom panels */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 p-3 border-t border-zinc-800/50 overflow-y-auto max-h-[50vh]">
            <AnalysisPanel symbol={symbol} />
            <div className="space-y-3">
              <SignalHistory history={signalHistory} />
            </div>
          </div>
        </div>

        {/* Right sidebar */}
        <div className="flex flex-col gap-3 p-3 overflow-y-auto bg-zinc-950">
          <SignalPanel
            symbol={symbol}
            signal={signal}
            currentPrice={currentPrice}
          />
          <PerformancePanel />
          <TradeHistory />
        </div>
      </div>
    </div>
  );
}

function formatPrice(p: number) {
  if (p >= 10000) return p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (p >= 100) return p.toFixed(3);
  if (p >= 1) return p.toFixed(4);
  return p.toFixed(6);
}
