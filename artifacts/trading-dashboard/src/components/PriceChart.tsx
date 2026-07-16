import { useEffect, useRef } from 'react';
import { Candle } from '@/lib/api';

interface Props {
  candles: Candle[];
  signal?: {
    signal: string;
    entry?: number | null;
    stop_loss?: number | null;
    take_profit_1?: number | null;
  };
  height?: number;
}

// We use a canvas-based mini chart to avoid heavy dependencies.
// Renders as OHLCV candlesticks with optional signal lines.
export function PriceChart({ candles, signal, height = 300 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || candles.length === 0) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const W = canvas.offsetWidth;
    const H = height;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.scale(dpr, dpr);

    const PADDING = { top: 20, right: 60, bottom: 30, left: 10 };
    const chartW = W - PADDING.left - PADDING.right;
    const chartH = H - PADDING.top - PADDING.bottom;

    // Price range
    const slice = candles.slice(-100);
    const highs = slice.map(c => c.high);
    const lows = slice.map(c => c.low);
    const priceMin = Math.min(...lows);
    const priceMax = Math.max(...highs);
    const priceRange = priceMax - priceMin || 1;

    const toX = (i: number) => PADDING.left + (i / (slice.length - 1)) * chartW;
    const toY = (p: number) => PADDING.top + chartH - ((p - priceMin) / priceRange) * chartH;

    // Background
    ctx.fillStyle = '#09090b';
    ctx.fillRect(0, 0, W, H);

    // Grid lines
    ctx.strokeStyle = '#27272a';
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= 4; i++) {
      const y = PADDING.top + (chartH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(PADDING.left, y);
      ctx.lineTo(W - PADDING.right, y);
      ctx.stroke();
      const price = priceMax - (priceRange / 4) * i;
      ctx.fillStyle = '#71717a';
      ctx.font = '10px monospace';
      ctx.textAlign = 'left';
      ctx.fillText(formatPrice(price), W - PADDING.right + 4, y + 4);
    }

    // Candles
    const candleW = Math.max(1, chartW / slice.length - 1);
    slice.forEach((c, i) => {
      const x = toX(i);
      const isUp = c.close >= c.open;
      const color = isUp ? '#10b981' : '#ef4444';
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 1;

      // Wick
      ctx.beginPath();
      ctx.moveTo(x, toY(c.high));
      ctx.lineTo(x, toY(c.low));
      ctx.stroke();

      // Body
      const bodyTop = toY(Math.max(c.open, c.close));
      const bodyH = Math.max(1, Math.abs(toY(c.open) - toY(c.close)));
      ctx.fillRect(x - candleW / 2, bodyTop, candleW, bodyH);
    });

    // Signal lines
    if (signal) {
      const drawLine = (price: number, color: string, dash: number[]) => {
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.setLineDash(dash);
        ctx.beginPath();
        ctx.moveTo(PADDING.left, toY(price));
        ctx.lineTo(W - PADDING.right, toY(price));
        ctx.stroke();
        ctx.fillStyle = color;
        ctx.font = '10px monospace';
        ctx.textAlign = 'left';
        ctx.fillText(formatPrice(price), W - PADDING.right + 4, toY(price) + 4);
        ctx.setLineDash([]);
      };

      if (signal.entry) drawLine(signal.entry, '#fbbf24', [4, 2]);
      if (signal.stop_loss) drawLine(signal.stop_loss, '#ef4444', [2, 3]);
      if (signal.take_profit_1) drawLine(signal.take_profit_1, '#10b981', [2, 3]);
    }

    // Close price marker (last candle)
    const last = slice[slice.length - 1];
    if (last) {
      const lastY = toY(last.close);
      ctx.strokeStyle = '#fbbf24';
      ctx.lineWidth = 0.5;
      ctx.setLineDash([2, 2]);
      ctx.beginPath();
      ctx.moveTo(PADDING.left, lastY);
      ctx.lineTo(W - PADDING.right, lastY);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#fbbf24';
      ctx.font = 'bold 10px monospace';
      ctx.fillText(formatPrice(last.close), W - PADDING.right + 4, lastY + 4);
    }
  }, [candles, signal, height]);

  return (
    <canvas
      ref={canvasRef}
      style={{ width: '100%', height: `${height}px`, display: 'block' }}
    />
  );
}

function formatPrice(p: number): string {
  if (p >= 1000) return p.toFixed(0);
  if (p >= 1) return p.toFixed(2);
  return p.toFixed(5);
}
