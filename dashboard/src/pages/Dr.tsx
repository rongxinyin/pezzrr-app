import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card } from '../components/Card'
import { Badge } from '../components/Badge'
import { MetricTile, MetricGrid } from '../components/MetricTile'
import { PriceCurveChart } from '../components/charts/PriceCurveChart'
import { apiFetch } from '../lib/api'
import type { DrEvent, DrParticipant, OpenAdrPrice, OpenAdrPricePoint } from '../lib/types'
import type { Status } from '../components/status'

function fmtRange(start: string, end: string): string {
  const s = new Date(start)
  const e = new Date(end)
  const day = s.toLocaleDateString([], { month: 'short', day: 'numeric' })
  const t = (d: Date) => d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return `${day} ${t(s)}–${t(e)}`
}

const EVENT_BADGE: Record<string, Status> = {
  active: 'act',
  pending: 'watch',
  completed: 'ok',
  cancelled: 'offline',
  failed: 'act',
}

function pct(v: number | null): string {
  return v == null ? '—' : `${Math.round(v * 100)}%`
}

export function Dr() {
  const [selected, setSelected] = useState<number | null>(null)

  const { data: events, isLoading, error } = useQuery({
    queryKey: ['dr-events'],
    queryFn: () => apiFetch<DrEvent[]>('/dr/events'),
    refetchInterval: 60_000,
  })

  const { data: price } = useQuery({
    queryKey: ['openadr-price'],
    queryFn: () => apiFetch<OpenAdrPrice>('/openadr/price'),
    refetchInterval: 60_000,
  })

  const { data: curve } = useQuery({
    queryKey: ['openadr-price-history'],
    queryFn: () => apiFetch<OpenAdrPricePoint[]>('/openadr/price/history'),
    refetchInterval: 300_000,
  })

  const active = events?.find((e) => e.active) ?? null

  // Default the participation table to the active event, else the latest.
  useEffect(() => {
    if (selected == null && events && events.length > 0) {
      setSelected((active ?? events[0]).event_id)
    }
  }, [events, active, selected])

  return (
    <div>
      <h1 className="mb-4 text-[22px] font-medium text-text">Demand response</h1>

      {price && price.price_per_kwh != null && <PriceBanner price={price} />}

      {active && (
        <div className="mt-4">
          <ActiveEventCard event={active} />
        </div>
      )}

      <div className="mt-4 grid grid-cols-1 lg:grid-cols-2" style={{ gap: 16 }}>
        <Card title="Price curve (24h)">
          {!curve || curve.length === 0 ? (
            <div className="text-[13px] text-text-muted">No price data.</div>
          ) : (
            <PriceCurveChart points={curve} />
          )}
        </Card>

        <Card title="Events">
          {isLoading && <div className="text-[13px] text-text-muted">Loading…</div>}
          {error && <div className="text-[13px] text-act">Failed to load events.</div>}
          {events && events.length === 0 && (
            <div className="text-[13px] text-text-muted">No demand response events.</div>
          )}
          {events && events.length > 0 && (
            <div className="flex flex-col gap-2">
              {events.map((e) => (
                <EventRow
                  key={e.event_id}
                  event={e}
                  selected={e.event_id === selected}
                  onSelect={() => setSelected(e.event_id)}
                />
              ))}
            </div>
          )}
        </Card>
      </div>

      {selected != null && (
        <div className="mt-4">
          <ParticipantsTable eventId={selected} />
        </div>
      )}
    </div>
  )
}

function PriceBanner({ price }: { price: OpenAdrPrice }) {
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
        <span className="text-[13px] text-text-muted">{price.program_name ?? 'OpenADR'}</span>
        <span className="ml-2 text-[15px] font-medium text-text">
          ${price.price_per_kwh!.toFixed(3)}/kWh
        </span>
        {price.interval_end && (
          <span className="ml-2 text-[12px] text-text-faint">
            until {new Date(price.interval_end).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
      </div>
      <Badge status={peak ? 'watch' : 'info'}>{price.period_type ?? 'price'}</Badge>
    </div>
  )
}

function ActiveEventCard({ event }: { event: DrEvent }) {
  return (
    <Card title="Active event">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-[15px] font-medium text-text">
          {event.signal_name ?? event.event_reference ?? `Event ${event.event_id}`}
        </span>
        <Badge status="act">{event.status}</Badge>
        {event.test_event && <Badge status="info">test</Badge>}
      </div>
      <MetricGrid cols={4}>
        <MetricTile label="Window" value={fmtRange(event.event_start, event.event_end)} />
        <MetricTile
          label="Target reduction"
          value={event.target_load_kw == null ? '—' : event.target_load_kw.toFixed(1)}
          unit={event.target_load_kw == null ? undefined : 'kW'}
        />
        <MetricTile label="Signal" value={event.signal_type ?? '—'} />
        <MetricTile label="Participants" value={event.participant_count} />
      </MetricGrid>
    </Card>
  )
}

function EventRow({
  event,
  selected,
  onSelect,
}: {
  event: DrEvent
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      onClick={onSelect}
      className="flex items-center justify-between rounded px-3 py-2 text-left"
      style={{
        background: selected ? 'var(--accent-soft)' : 'var(--bg-subtle)',
        border: selected ? '0.5px solid var(--accent)' : '0.5px solid transparent',
      }}
    >
      <div className="min-w-0">
        <div className="truncate text-[14px] text-text">
          {event.signal_name ?? event.event_reference ?? `Event ${event.event_id}`}
        </div>
        <div className="text-[12px] text-text-faint">
          {fmtRange(event.event_start, event.event_end)} · {event.participant_count} homes
        </div>
      </div>
      <Badge status={EVENT_BADGE[event.status] ?? 'offline'}>{event.status}</Badge>
    </button>
  )
}

function ParticipantsTable({ eventId }: { eventId: number }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['dr-participants', eventId],
    queryFn: () => apiFetch<DrParticipant[]>(`/dr/events/${eventId}/participants`),
  })

  return (
    <Card title="Participation">
      {isLoading && <div className="text-[13px] text-text-muted">Loading…</div>}
      {error && <div className="text-[13px] text-act">Failed to load participants.</div>}
      {data && data.length === 0 && (
        <div className="text-[13px] text-text-muted">No participants recorded.</div>
      )}
      {data && data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-text-muted" style={{ borderBottom: '0.5px solid var(--border)' }}>
                <th className="py-2 pr-4 text-left font-normal">Home</th>
                <th className="py-2 pr-4 text-right font-normal">Baseline kW</th>
                <th className="py-2 pr-4 text-right font-normal">Target kW</th>
                <th className="py-2 pr-4 text-right font-normal">Actual kW</th>
                <th className="py-2 pr-4 text-right font-normal">Settlement kWh</th>
                <th className="py-2 text-right font-normal">Performance</th>
              </tr>
            </thead>
            <tbody>
              {data.map((p) => (
                <tr key={p.id} style={{ borderBottom: '0.5px solid var(--border)' }}>
                  <td className="py-2 pr-4 text-text">
                    {p.home_name ?? `Home ${p.home_id}`}
                    {!p.opted_in && <span className="ml-2 text-text-faint">(opted out)</span>}
                  </td>
                  <td className="py-2 pr-4 text-right tabular-nums text-text-muted">{p.baseline_kw?.toFixed(2) ?? '—'}</td>
                  <td className="py-2 pr-4 text-right tabular-nums text-text-muted">{p.reduction_target_kw?.toFixed(2) ?? '—'}</td>
                  <td className="py-2 pr-4 text-right tabular-nums text-text">{p.actual_reduction_kw?.toFixed(2) ?? '—'}</td>
                  <td className="py-2 pr-4 text-right tabular-nums text-text-muted">{p.settlement_kwh?.toFixed(2) ?? '—'}</td>
                  <td className="py-2 text-right tabular-nums text-text">{pct(p.performance_score)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
