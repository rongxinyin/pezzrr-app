import { Badge } from './Badge'
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
          <div key={c.circuit_id} className="flex items-center gap-3">
            <div className="w-40 shrink-0 truncate text-[13px] text-text" title={c.circuit_name ?? undefined}>
              {c.circuit_name ?? `Channel ${c.channel_num}`}
              {c.is_critical && <span className="ml-1 text-[11px] text-act">●</span>}
            </div>
            <div className="relative h-4 flex-1 overflow-hidden rounded bg-subtle">
              <div
                className="h-full rounded"
                style={{ width: `${pct}%`, background: off ? 'var(--text-faint)' : 'var(--accent)' }}
              />
            </div>
            <div className="w-16 shrink-0 text-right text-[13px] text-text-muted tabular-nums">
              {w} W
            </div>
            {off && <Badge status="offline">off</Badge>}
          </div>
        )
      })}
    </div>
  )
}
