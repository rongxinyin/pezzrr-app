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

// §13.1 home card: name, status dot, SoC ring, current load, grid/DR badge.
export function HomeCard({ home }: { home: FleetStatusItem }) {
  const dot = STATUS_COLORS[home.status].fg
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
      </div>
    </Link>
  )
}
