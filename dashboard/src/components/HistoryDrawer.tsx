import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import type { PanelEnvelope, PanelLive } from '../lib/types'
import { PowerAreaChart } from './charts/PowerAreaChart'
import { PowerFlowSankey } from './charts/PowerFlowSankey'

interface Props {
  homeId: number
  panel: PanelLive | null
  open: boolean
  onClose: () => void
}

export function HistoryDrawer({ homeId, panel, open, onClose }: Props) {
  const range = useMemo(() => {
    const to = new Date()
    const from = new Date(to.getTime() - 24 * 3600 * 1000)
    return { from: from.toISOString(), to: to.toISOString() }
  }, [open])

  const { data, isLoading, error } = useQuery({
    queryKey: ['panel-24h', homeId, range.from],
    queryFn: () =>
      apiFetch<PanelEnvelope>(
        `/homes/${homeId}/panel?from=${encodeURIComponent(range.from)}&to=${encodeURIComponent(range.to)}&bucket=5m`,
      ),
    enabled: open,
  })

  if (!open) return null

  return (
    <div className="fixed inset-0 z-40">
      <div className="absolute inset-0" style={{ background: 'rgba(0,0,0,0.35)' }} onClick={onClose} />
      <aside
        className="absolute right-0 top-0 flex h-full w-full max-w-2xl flex-col overflow-y-auto"
        style={{ background: 'var(--bg-page)', borderLeft: '0.5px solid var(--border)' }}
      >
        <header
          className="flex items-center justify-between px-6 py-4"
          style={{ borderBottom: '0.5px solid var(--border)', background: 'var(--bg-card)' }}
        >
          <h2 className="text-[16px] font-medium text-text">Last 24 hours</h2>
          <button
            onClick={onClose}
            className="rounded text-[13px] text-text-muted"
            style={{ border: '0.5px solid var(--border)', padding: '5px 12px', background: 'var(--bg-card)' }}
          >
            Close
          </button>
        </header>

        <div className="flex flex-col gap-6 p-6">
          <section>
            <h3 className="mb-2 text-[14px] font-medium text-text">Power flow (now)</h3>
            <PowerFlowSankey panel={panel} />
          </section>

          <section>
            <h3 className="mb-2 text-[14px] font-medium text-text">Panel power</h3>
            {isLoading && <div className="text-[13px] text-text-muted">Loading history…</div>}
            {error && <div className="text-[13px] text-act">Failed to load history.</div>}
            {data && data.points.length === 0 && (
              <div className="text-[13px] text-text-muted">No data in this window.</div>
            )}
            {data && data.points.length > 0 && <PowerAreaChart points={data.points} />}
          </section>
        </div>
      </aside>
    </div>
  )
}
