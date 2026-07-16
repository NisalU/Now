import { useQuery } from '@tanstack/react-query';
import { api, SystemStatus } from '@/lib/api';

interface Props {
  wsConnected: boolean;
}

export function StatusBar({ wsConnected }: Props) {
  const { data: status } = useQuery<SystemStatus>({
    queryKey: ['status'],
    queryFn: api.status,
    refetchInterval: 5_000,
  });

  return (
    <div className="flex items-center gap-4 px-4 py-2 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur text-xs text-zinc-500 flex-wrap">
      {/* WS */}
      <div className="flex items-center gap-1.5">
        <span className={`w-1.5 h-1.5 rounded-full ${wsConnected ? 'bg-emerald-500 shadow-[0_0_6px_#10b981]' : 'bg-red-500'}`} />
        <span className={wsConnected ? 'text-emerald-400' : 'text-red-400'}>
          {wsConnected ? 'Live' : 'Connecting…'}
        </span>
      </div>

      {/* Binance WS */}
      {status && (
        <div className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full ${status.ws.connected ? 'bg-blue-500' : 'bg-zinc-600'}`} />
          <span>Binance {status.ws.connected ? 'connected' : 'disconnected'}</span>
          {status.ws.reconnects > 0 && <span className="text-amber-500">({status.ws.reconnects} reconnects)</span>}
        </div>
      )}

      {/* AI */}
      {status?.ai && (
        <div className="flex items-center gap-1.5">
          <span className={`w-1.5 h-1.5 rounded-full ${status.ai.enabled ? 'bg-violet-500' : 'bg-zinc-700'}`} />
          <span>AI: {status.ai.enabled ? status.ai.model.split('-').slice(0, 2).join('-') : 'disabled'}</span>
          {status.ai.calls_total > 0 && <span>({status.ai.calls_total} calls)</span>}
          {status.ai.last_call_ms && <span className="text-violet-400">{status.ai.last_call_ms}ms</span>}
        </div>
      )}

      {/* Clients */}
      {status && (
        <span>{status.clients} dashboard{status.clients !== 1 ? 's' : ''}</span>
      )}

      <span className="ml-auto text-zinc-700">{new Date().toLocaleTimeString()}</span>
    </div>
  );
}
