import { useQuery } from '@tanstack/react-query'
import { Card } from './Card'
import { Badge } from './Badge'
import { apiFetch } from '../lib/api'
import type { ControlAction, ActionStatus } from '../lib/types'
import type { Status } from './status'

const STATUS_BADGE: Record<ActionStatus, Status> = {
  pending: 'watch',
  acknowledged: 'info',
  success: 'ok',
  failed: 'act',
}

function fmtTime(ts: string): string {
  return new Date(ts).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

// Recent dispatch audit, polling every 4s so pending → acknowledged/success
// flips become visible without a refresh (§13.5).
export function ActionLog({ homeId }: { homeId: number }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['control-actions', homeId],
    queryFn: () => apiFetch<ControlAction[]>(`/control/actions?home_id=${homeId}`),
    refetchInterval: 4000,
  })

  return (
    <Card title="Recent actions">
      {isLoading && <div className="text-[13px] text-text-muted">Loading…</div>}
      {error && <div className="text-[13px] text-act">Failed to load actions.</div>}
      {data && data.length === 0 && <div className="text-[13px] text-text-muted">No actions yet.</div>}
      {data && data.length > 0 && (
        <div className="flex flex-col gap-2">
          {data.map((a) => (
            <div
              key={a.action_id}
              className="flex items-center justify-between rounded px-3 py-2"
              style={{ background: 'var(--bg-subtle)' }}
            >
              <div className="min-w-0">
                <div className="text-[13px] text-text capitalize">
                  {a.action_type.replace(/_/g, ' ')}
                  {a.circuit_id != null && <span className="text-text-muted"> · circuit {a.circuit_id}</span>}
                  {a.device_id != null && <span className="text-text-muted"> · device {a.device_id}</span>}
                </div>
                <div className="text-[12px] text-text-faint">
                  {fmtTime(a.ts)} · {a.triggered_by}
                  {a.error_msg && <span className="text-act"> · {a.error_msg}</span>}
                </div>
              </div>
              <Badge status={STATUS_BADGE[a.status]}>{a.status}</Badge>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
