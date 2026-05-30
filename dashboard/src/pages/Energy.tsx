import { useMemo, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Card } from '../components/Card'
import { MetricTile, MetricGrid } from '../components/MetricTile'
import { EnergyTrendChart } from '../components/charts/EnergyTrendChart'
import { CircuitRankChart } from '../components/charts/CircuitRankChart'
import { apiFetch } from '../lib/api'
import type { EnergyAnalytics } from '../lib/types'

const RANGES = [
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
]

function fmt(v: number | null, digits = 1): string {
  return v == null ? '—' : v.toFixed(digits)
}

export function Energy() {
  const { id } = useParams()
  const homeId = Number(id)
  const [days, setDays] = useState(30)

  const range = useMemo(() => {
    const to = new Date()
    const from = new Date(to.getTime() - days * 24 * 3600 * 1000)
    return { from: from.toISOString(), to: to.toISOString() }
  }, [days])

  const { data, isLoading, error } = useQuery({
    queryKey: ['home-energy', homeId, range.from],
    queryFn: () =>
      apiFetch<EnergyAnalytics>(
        `/homes/${homeId}/energy?from=${encodeURIComponent(range.from)}&to=${encodeURIComponent(range.to)}`,
      ),
  })

  const t = data?.totals
  const title = data?.home_name ?? `Home ${id}`

  return (
    <div>
      <Link to={`/homes/${homeId}`} className="text-[13px] text-accent">
        ← Home detail
      </Link>
      <div className="mb-4 mt-2 flex items-center justify-between">
        <h1 className="text-[22px] font-medium text-text">{title} · Energy</h1>
        <div className="flex items-center gap-1">
          {RANGES.map((r) => (
            <button
              key={r.days}
              onClick={() => setDays(r.days)}
              className="rounded text-[13px]"
              style={{
                padding: '5px 12px',
                border: '0.5px solid var(--border)',
                background: days === r.days ? 'var(--accent)' : 'var(--bg-card)',
                color: days === r.days ? 'var(--bg-card)' : 'var(--text-muted)',
                fontWeight: days === r.days ? 500 : 400,
              }}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading && <div className="text-text-muted">Loading…</div>}
      {error && <div className="text-act">Failed to load energy analytics.</div>}

      {data && (
        <div className="flex flex-col gap-4">
          <MetricGrid cols={4}>
            <MetricTile label="Home load" value={fmt(t?.home_load_kwh ?? null)} unit="kWh" />
            <MetricTile label="Solar generated" value={fmt(t?.solar_gen_kwh ?? null)} unit="kWh" />
            <MetricTile label="Peak demand" value={fmt(t?.peak_demand_kw ?? null, 2)} unit="kW" />
            <MetricTile
              label="Self-consumption"
              value={t?.self_consumption_pct == null ? '—' : fmt(t.self_consumption_pct)}
              unit={t?.self_consumption_pct == null ? undefined : '%'}
            />
          </MetricGrid>

          <Card title="Daily load & peak demand">
            {data.days.length === 0 ? (
              <div className="text-[13px] text-text-muted">No data in this range.</div>
            ) : (
              <EnergyTrendChart days={data.days} />
            )}
          </Card>

          <Card title="Circuit energy ranking">
            {data.circuits.length === 0 ? (
              <div className="text-[13px] text-text-muted">No circuit data.</div>
            ) : (
              <CircuitRankChart circuits={data.circuits} />
            )}
          </Card>
        </div>
      )}
    </div>
  )
}
