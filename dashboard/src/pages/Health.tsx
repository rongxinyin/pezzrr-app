import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card } from '../components/Card'
import { Badge } from '../components/Badge'
import { apiFetch } from '../lib/api'
import type { DeviceHealth, CoverageReport, CoverageRow } from '../lib/types'
import type { Status } from '../components/status'

interface HomeOpt {
  home_id: number
  home_name: string
}

function todayISO(): string {
  return new Date().toISOString().slice(0, 10)
}

function onlineStatus(v: boolean | null): { status: Status; label: string } {
  if (v === true) return { status: 'ok', label: 'online' }
  if (v === false) return { status: 'act', label: 'offline' }
  return { status: 'offline', label: 'unknown' }
}

function timeAgo(iso: string | null): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  const mins = Math.round((Date.now() - then) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.round(hrs / 24)}d ago`
}

function coverageStatus(pct: number | null): Status {
  if (pct == null) return 'offline'
  if (pct < 80) return 'act'
  if (pct < 95) return 'watch'
  return 'ok'
}

const th = 'pb-2 text-left text-[12px] font-medium uppercase tracking-wide text-text-faint'
const td = 'py-2 text-[13px] text-text'

export function Health() {
  const { data: homes } = useQuery({
    queryKey: ['homes-list'],
    queryFn: () => apiFetch<HomeOpt[]>('/homes'),
  })

  const [homeId, setHomeId] = useState<number | null>(null)
  const [date, setDate] = useState(todayISO())
  const effectiveHome = homeId ?? homes?.[0]?.home_id ?? null

  const { data: devices } = useQuery({
    queryKey: ['health-devices', effectiveHome],
    queryFn: () => apiFetch<DeviceHealth[]>(`/devices?home_id=${effectiveHome}`),
    enabled: effectiveHome != null,
  })

  const { data: coverage } = useQuery({
    queryKey: ['health-coverage', effectiveHome, date],
    queryFn: () =>
      apiFetch<CoverageReport>(`/health/coverage?home_id=${effectiveHome}&date=${date}`),
    enabled: effectiveHome != null,
  })

  return (
    <div>
      <h1 className="mb-4 text-[22px] font-medium text-text">Device health</h1>

      <div className="mb-4 flex items-center gap-2">
        <label className="text-[13px] text-text-muted">Home</label>
        <select
          value={effectiveHome ?? ''}
          onChange={(e) => setHomeId(Number(e.target.value))}
          className="rounded text-[13px] text-text"
          style={{ padding: '6px 10px', border: '0.5px solid var(--border)', background: 'var(--bg-card)' }}
        >
          {(homes ?? []).map((o) => (
            <option key={o.home_id} value={o.home_id}>
              {o.home_name}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-4">
        <Card title="Devices">
          {devices && devices.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr style={{ borderBottom: '0.5px solid var(--border)' }}>
                    <th className={th}>Device</th>
                    <th className={th}>Type</th>
                    <th className={th}>Status</th>
                    <th className={th}>Firmware</th>
                    <th className={th}>Last seen</th>
                  </tr>
                </thead>
                <tbody>
                  {devices.map((d) => {
                    const o = onlineStatus(d.is_online)
                    return (
                      <tr key={d.device_id} style={{ borderBottom: '0.5px solid var(--border)' }}>
                        <td className={td}>
                          {d.device_name ?? `Device ${d.device_id}`}
                          {d.model && <span className="ml-2 text-text-faint">{d.model}</span>}
                        </td>
                        <td className={`${td} text-text-muted`}>{d.device_type}</td>
                        <td className={td}>
                          <Badge status={o.status}>{o.label}</Badge>
                        </td>
                        <td className={`${td} text-text-muted`}>{d.firmware_version ?? '—'}</td>
                        <td className={`${td} text-text-muted`}>{timeAgo(d.online_updated_at)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-[13px] text-text-muted">No devices.</div>
          )}
        </Card>

        <Card
          title="Data coverage"
          action={
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="rounded text-[13px] text-text"
              style={{ padding: '5px 10px', border: '0.5px solid var(--border)', background: 'var(--bg-card)' }}
            />
          }
        >
          {coverage && coverage.devices.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr style={{ borderBottom: '0.5px solid var(--border)' }}>
                    <th className={th}>Device</th>
                    <th className={th}>Type</th>
                    <th className={th}>Coverage</th>
                    <th className={th}>Readings</th>
                    <th className={th}>Last reading</th>
                  </tr>
                </thead>
                <tbody>
                  {coverage.devices.map((c: CoverageRow) => (
                    <tr key={c.device_id} style={{ borderBottom: '0.5px solid var(--border)' }}>
                      <td className={td}>{c.device_name ?? `Device ${c.device_id}`}</td>
                      <td className={`${td} text-text-muted`}>{c.device_type}</td>
                      <td className={td}>
                        <Badge status={coverageStatus(c.coverage_pct)}>
                          {c.coverage_pct == null ? '—' : `${c.coverage_pct.toFixed(1)}%`}
                        </Badge>
                      </td>
                      <td className={`${td} text-text-muted`}>
                        {c.reading_count} · {c.present_buckets}/{c.expected_buckets} buckets
                      </td>
                      <td className={`${td} text-text-muted`}>{timeAgo(c.last_reading_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-[13px] text-text-muted">No coverage data for this day.</div>
          )}
        </Card>
      </div>
    </div>
  )
}
