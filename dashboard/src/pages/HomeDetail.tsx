import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Card } from '../components/Card'
import { MetricTile, MetricGrid } from '../components/MetricTile'
import { StatusRing } from '../components/StatusRing'
import { Badge } from '../components/Badge'
import { CircuitBars } from '../components/CircuitBars'
import { HistoryDrawer } from '../components/HistoryDrawer'
import { useHomeStream } from '../hooks/useHomeStream'
import { apiFetch } from '../lib/api'
import { useAuthStore } from '../store/auth'
import type { Status } from '../components/status'

function kw(w: number | null | undefined): string {
  return w == null ? '—' : (w / 1000).toFixed(2)
}

function cToF(c: number | null | undefined): string {
  return c == null ? '—' : Math.round((c * 9) / 5 + 32).toString()
}

function socStatus(soc: number | null | undefined): Status {
  if (soc == null) return 'offline'
  if (soc < 20) return 'act'
  if (soc < 35) return 'watch'
  return 'ok'
}

export function HomeDetail() {
  const { id } = useParams()
  const homeId = Number(id)
  const role = useAuthStore((s) => s.role)
  const canControl = role === 'operator' || role === 'admin'
  const [historyOpen, setHistoryOpen] = useState(false)
  const { data, isLoading, error } = useHomeStream(homeId)
  const { data: home } = useQuery({
    queryKey: ['home', homeId],
    queryFn: () => apiFetch<{ home_name: string }>(`/homes/${homeId}`),
  })

  const title = home?.home_name ?? `Home ${id}`

  return (
    <div>
      <Link to="/" className="text-[13px] text-accent">
        ← Fleet overview
      </Link>
      <div className="mb-4 mt-2 flex items-center justify-between">
        <h1 className="text-[22px] font-medium text-text">{title}</h1>
        <div className="flex items-center gap-2">
          {canControl && (
            <Link
              to={`/control/${homeId}`}
              className="rounded text-[13px] font-medium"
              style={{ padding: '6px 14px', color: 'var(--bg-card)', background: 'var(--accent)' }}
            >
              Control
            </Link>
          )}
          <button
            onClick={() => setHistoryOpen(true)}
            className="rounded text-[13px] text-accent"
            style={{ border: '0.5px solid var(--border)', padding: '6px 14px', background: 'var(--bg-card)' }}
          >
            24h history
          </button>
        </div>
      </div>

      <HistoryDrawer
        homeId={homeId}
        panel={data?.panel ?? null}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
      />

      {isLoading && <div className="text-text-muted">Loading…</div>}
      {error && <div className="text-act">Failed to load home telemetry.</div>}

      {data && (
        <div className="flex flex-col gap-4">
          <PriceBanner price={data.price} />

          <Card title="Power flow">
            <MetricGrid cols={4}>
              <MetricTile label="Grid" value={kw(data.panel?.grid_power_w)} unit="kW" />
              <MetricTile label="Solar" value={kw(data.panel?.solar_power_w)} unit="kW" />
              <MetricTile label="Battery" value={kw(data.panel?.battery_power_w)} unit="kW" />
              <MetricTile label="Home load" value={kw(data.panel?.home_load_w)} unit="kW" />
            </MetricGrid>
          </Card>

          <div className="grid grid-cols-1 md:grid-cols-2" style={{ gap: 16 }}>
            <Card title="Battery">
              <div className="flex items-center gap-6">
                <StatusRing
                  value={data.battery?.soc_pct ?? 0}
                  status={socStatus(data.battery?.soc_pct)}
                  label="state of charge"
                />
                <div className="text-[14px] text-text-muted">
                  <div className="capitalize text-text">{data.battery?.status ?? 'unknown'}</div>
                  <div className="mt-1">
                    Usable{' '}
                    <span className="text-text">{data.battery?.usable_kwh ?? '—'}</span> kWh
                  </div>
                  {data.battery?.soh_pct != null && <div className="mt-1">Health {data.battery.soh_pct}%</div>}
                </div>
              </div>
            </Card>

            <ThermostatCard t={data.thermostat} />
          </div>

          <Card title="Circuits by load">
            {data.circuits.length === 0 ? (
              <div className="text-[13px] text-text-muted">No circuit data.</div>
            ) : (
              <CircuitBars circuits={data.circuits} />
            )}
          </Card>
        </div>
      )}
    </div>
  )
}

function PriceBanner({ price }: { price: import('../lib/types').PriceLive | null }) {
  if (!price || price.price_per_kwh == null) return null
  const peak = price.period_type === 'peak'
  return (
    <div
      className="flex items-center justify-between rounded-lg px-5 py-3"
      style={{
        background: peak ? 'var(--watch-bg)' : 'var(--info-bg)',
        border: '0.5px solid var(--border)',
      }}
    >
      <div>
        <span className="text-[13px] text-text-muted">{price.program_name}</span>
        <span className="ml-2 text-[15px] font-medium text-text">
          ${price.price_per_kwh.toFixed(3)}/kWh
        </span>
      </div>
      <Badge status={peak ? 'watch' : 'info'}>{price.period_type ?? 'price'}</Badge>
    </div>
  )
}

function ThermostatCard({ t }: { t: import('../lib/types').ThermostatLive | null }) {
  const stateStatus: Status =
    t?.hvac_state === 'heating' || t?.hvac_state === 'cooling' ? 'info' : 'offline'
  return (
    <Card title="Thermostat">
      {!t ? (
        <div className="text-[13px] text-text-muted">No thermostat data.</div>
      ) : (
        <div className="flex items-center justify-between">
          <div>
            <div className="text-text" style={{ fontSize: 30, fontWeight: 500, lineHeight: 1.1 }}>
              {cToF(t.indoor_temp_c)}°F
            </div>
            {t.indoor_humidity_pct != null && (
              <div className="mt-1 text-[13px] text-text-muted">{t.indoor_humidity_pct}% RH</div>
            )}
          </div>
          <div className="flex flex-col items-end gap-1 text-[13px] text-text-muted">
            <Badge status={stateStatus}>{t.hvac_state ?? 'idle'}</Badge>
            <div className="capitalize">mode: {t.hvac_mode ?? '—'}</div>
            <div>
              set {cToF(t.heat_setpoint_c)}° / {cToF(t.cool_setpoint_c)}°F
            </div>
          </div>
        </div>
      )}
    </Card>
  )
}
