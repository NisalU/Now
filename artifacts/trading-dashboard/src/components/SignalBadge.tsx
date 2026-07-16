import { cn } from '@/lib/utils';

type Direction = 'LONG' | 'SHORT' | 'WAIT';

interface Props {
  signal: Direction;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

const COLORS: Record<Direction, string> = {
  LONG: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40',
  SHORT: 'bg-red-500/20 text-red-400 border-red-500/40',
  WAIT: 'bg-zinc-700/40 text-zinc-400 border-zinc-600/40',
};

const SIZES = {
  sm: 'text-xs px-2 py-0.5',
  md: 'text-sm px-3 py-1',
  lg: 'text-base px-4 py-1.5 font-bold',
};

export function SignalBadge({ signal, size = 'md', className }: Props) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded border font-semibold tracking-wider uppercase',
        COLORS[signal],
        SIZES[size],
        className
      )}
    >
      {signal === 'LONG' && '▲ '}
      {signal === 'SHORT' && '▼ '}
      {signal}
    </span>
  );
}
