import { Link } from 'react-router-dom'
import { StatusRing } from './StatusRing'
import { Badge } from './Badge'
import { STATUS_COLORS, type Status } from './status'
import type { FleetStatusItem } from '../lib/types'

const STATUS_LABEL: Record<Status, string> = {
  ok: 'OK',
  watch: 'Watch',
  act: 'Action',
  info: 'Info',
  offline: 'Offline',
}

function kw(w: number | null): string {
  return w == null ? '—' : (w / 1000).toFixed(2)
}

// SHP2 gridSta -> {label, status colour}. null = the panel didn't report a
// grid state in its latest reading, surfaced as "Grid ?" rather than hidden.
function gridState(g: number | null): { label: string; status: Status } {
  if (g === 0) return { label: 'Grid outage', status: 'act' }
  if (g === 1) return { label: 'On grid', status: 'ok' }
  return { label: 'Grid ?', status: 'info' }
}

// §13.1 home card: name, status dot, SoC ring, current load, grid/DR badge.
export function HomeCard({ home }: { home: FleetStatusItem }) {
  const dot = STATUS_COLORS[home.status].fg
  const grid = gridState(home.grid_status)
  return (
    <Link to={`/homes/${home.home_id}`} className="block">
      <div
        className="bg-card rounded-lg transition-shadow hover:shadow-card"
        style={{ border: '0.5px solid var(--border)', padding: '16px 20px' }}
      >
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2">
              <span
                style={{ width: 8, height: 8, borderRadius: '50%', background: dot, display: 'inline-block' }}
              />
              <h3 className="text-[15px] font-medium text-text">{home.home_name}</h3>
            </div>
            {home.city && <div className="mt-0.5 text-[13px] text-text-muted">{home.city}</div>}
          </div>
          <StatusRing value={home.battery_soc_pct ?? 0} status={home.status} size={56} />
        </div>

        <div className="mt-3 flex items-end justify-between">
          <div>
            <div className="text-[13px] text-text-muted">Home load</div>
            <div className="text-text" style={{ fontSize: 22, fontWeight: 500, lineHeight: 1.2 }}>
              {kw(home.home_load_w)}
              <span className="ml-1 text-[13px] font-normal text-text-muted">kW</span>
            </div>
          </div>
          <div className="flex flex-col items-end gap-1">
            {home.dr_active && <Badge status="act">DR active</Badge>}
            <Badge status={home.status}>{STATUS_LABEL[home.status]}</Badge>
          </div>
        </div>

        {/* Panel status: grid (on-grid / outage) + EPS backup ("saving") mode. */}
        <div
          className="mt-3 flex items-center justify-between pt-3"
          style={{ borderTop: '0.5px solid var(--border)' }}
        >
          <Badge status={grid.status}>{grid.label}</Badge>
          <span className="text-[12px] text-text-muted">
            Saving mode{' '}
            <span
              className="font-medium"
              style={{ color: home.eps_mode_active ? 'var(--ok)' : 'var(--text-faint)' }}
            >
              {home.eps_mode_active == null ? '—' : home.eps_mode_active ? 'active' : 'off'}
            </span>
          </span>
        </div>
      </div>
    </Link>
  )
}
