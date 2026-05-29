import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card } from '../components/Card'
import { apiFetch, apiDownload, ApiError } from '../lib/api'

interface HomeOpt {
  home_id: number
  home_name: string
}

function todayISO(): string {
  return new Date().toISOString().slice(0, 10)
}

function thisMonth(): string {
  return new Date().toISOString().slice(0, 7)
}

function DownloadButton({
  label,
  onClick,
  busy,
}: {
  label: string
  onClick: () => void
  busy: boolean
}) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className="rounded text-[13px] font-medium"
      style={{
        padding: '6px 14px',
        color: 'var(--bg-card)',
        background: 'var(--accent)',
        opacity: busy ? 0.6 : 1,
      }}
    >
      {label}
    </button>
  )
}

export function Reports() {
  const { data: homes } = useQuery({
    queryKey: ['homes-list'],
    queryFn: () => apiFetch<HomeOpt[]>('/homes'),
  })

  const [homeId, setHomeId] = useState<number | null>(null)
  const [day, setDay] = useState(todayISO())
  const [month, setMonth] = useState(thisMonth())
  const [from, setFrom] = useState(todayISO())
  const [to, setTo] = useState(todayISO())
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  // Default the home selector once the list loads.
  const effectiveHome = homeId ?? homes?.[0]?.home_id ?? null

  async function download(path: string, fallback: string) {
    if (effectiveHome == null) return
    setBusy(true)
    setErr(null)
    try {
      await apiDownload(path, fallback)
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Download failed')
    } finally {
      setBusy(false)
    }
  }

  const h = effectiveHome

  return (
    <div>
      <h1 className="mb-4 text-[22px] font-medium text-text">Reports</h1>

      {err && (
        <div
          className="mb-4 rounded px-4 py-2 text-[13px] text-act"
          style={{ background: 'var(--act-bg)', border: '0.5px solid var(--border)' }}
        >
          {err}
        </div>
      )}

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

      <div className="grid grid-cols-1 md:grid-cols-3" style={{ gap: 16 }}>
        <Card title="Daily report">
          <div className="flex flex-col gap-3">
            <input
              type="date"
              value={day}
              onChange={(e) => setDay(e.target.value)}
              className="rounded text-[13px] text-text"
              style={{ padding: '6px 10px', border: '0.5px solid var(--border)', background: 'var(--bg-card)' }}
            />
            <div className="flex gap-2">
              <DownloadButton label="PDF" busy={busy} onClick={() => download(`/reports/daily?home_id=${h}&day=${day}&format=pdf`, `energy_${h}_${day}.pdf`)} />
              <DownloadButton label="CSV" busy={busy} onClick={() => download(`/reports/daily?home_id=${h}&day=${day}&format=csv`, `energy_${h}_${day}.csv`)} />
            </div>
          </div>
        </Card>

        <Card title="Monthly report">
          <div className="flex flex-col gap-3">
            <input
              type="month"
              value={month}
              onChange={(e) => setMonth(e.target.value)}
              className="rounded text-[13px] text-text"
              style={{ padding: '6px 10px', border: '0.5px solid var(--border)', background: 'var(--bg-card)' }}
            />
            <div className="flex gap-2">
              <DownloadButton label="PDF" busy={busy} onClick={() => download(`/reports/monthly?home_id=${h}&month=${month}&format=pdf`, `energy_${h}_${month}.pdf`)} />
              <DownloadButton label="CSV" busy={busy} onClick={() => download(`/reports/monthly?home_id=${h}&month=${month}&format=csv`, `energy_${h}_${month}.csv`)} />
            </div>
          </div>
        </Card>

        <Card title="Range export">
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <input
                type="date"
                value={from}
                onChange={(e) => setFrom(e.target.value)}
                className="rounded text-[13px] text-text"
                style={{ padding: '6px 10px', border: '0.5px solid var(--border)', background: 'var(--bg-card)' }}
              />
              <span className="text-text-faint">→</span>
              <input
                type="date"
                value={to}
                onChange={(e) => setTo(e.target.value)}
                className="rounded text-[13px] text-text"
                style={{ padding: '6px 10px', border: '0.5px solid var(--border)', background: 'var(--bg-card)' }}
              />
            </div>
            <div className="flex gap-2">
              <DownloadButton label="CSV" busy={busy} onClick={() => download(`/reports/export?home_id=${h}&from=${from}&to=${to}&format=csv`, `energy_${h}.csv`)} />
              <DownloadButton label="PDF" busy={busy} onClick={() => download(`/reports/export?home_id=${h}&from=${from}&to=${to}&format=pdf`, `energy_${h}.pdf`)} />
            </div>
          </div>
        </Card>
      </div>
    </div>
  )
}
