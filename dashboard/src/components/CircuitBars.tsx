import { Badge } from './Badge'
import { PRIORITY_LABEL, PRIORITY_STATUS, STATUS_COLORS } from './status'
import type { CircuitLive } from '../lib/types'

// Ranked horizontal load bars (§13.2). Bar width is relative to the
// highest-drawing circuit in the set.
export function CircuitBars({ circuits }: { circuits: CircuitLive[] }) {
  const max = Math.max(1, ...circuits.map((c) => c.power_w ?? 0))

  return (
    <div className="flex flex-col gap-2">
      {circuits.map((c) => {
        const w = c.power_w ?? 0
        const pct = Math.round((w / max) * 100)
        const off = c.is_enabled === false
        return (
          <div key={c.circuit_id} className="flex flex-col gap-1">
            <div className="flex items-center gap-2 text-[13px] text-text">
              {c.circuit_priority && (
                <span
                  className="shrink-0 text-[11px]"
                  style={{ color: STATUS_COLORS[PRIORITY_STATUS[c.circuit_priority]].fg }}
                  title={`${PRIORITY_LABEL[c.circuit_priority]} circuit`}
                >
                  ●
                </span>
              )}
              <span className="min-w-0 flex-1">{c.circuit_name ?? `Channel ${c.channel_num}`}</span>
              {off && <Badge status="offline">off</Badge>}
              <span className="shrink-0 text-text-muted tabular-nums">{w} W</span>
            </div>
            <div className="relative h-4 overflow-hidden rounded bg-subtle">
              <div
                className="h-full rounded"
                style={{ width: `${pct}%`, background: off ? 'var(--text-faint)' : 'var(--accent)' }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}
