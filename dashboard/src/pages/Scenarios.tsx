import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card } from '../components/Card'
import { Badge } from '../components/Badge'
import { MetricTile, MetricGrid } from '../components/MetricTile'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ScenarioCalendar } from '../components/ScenarioCalendar'
import { PriceCurveChart } from '../components/charts/PriceCurveChart'
import { apiFetch, ApiError } from '../lib/api'
import { useAuthStore } from '../store/auth'
import { SCENARIOS, SCENARIO_LABEL, scenarioLabel, scenarioStatus } from '../lib/scenarios'
import type {
  BatteryCapacity,
  DrEvent,
  DrParticipant,
  OpenAdrPrice,
  OpenAdrPricePoint,
  OperationScenario,
  PanelCapacity,
  ScenarioCurrent,
  ScenarioDispatchResult,
} from '../lib/types'
import type { Status } from '../components/status'

const DISPATCH_ROLES = ['operator', 'admin']

export function Scenarios() {
  const role = useAuthStore((s) => s.role)
  const canEdit = role != null && DISPATCH_ROLES.includes(role)

  const { data: current } = useQuery({
    queryKey: ['scenarios-current'],
    queryFn: () => apiFetch<ScenarioCurrent[]>('/scenarios/current'),
    refetchInterval: 60_000,
  })

  const [homeId, setHomeId] = useState<number | null>(null)
  useEffect(() => {
    if (homeId == null && current && current.length > 0) setHomeId(current[0].home_id)
  }, [current, homeId])

  const selectedHome = current?.find((h) => h.home_id === homeId) ?? null

  return (
    <div>
      <h1 className="mb-4 text-[22px] font-medium text-text">Scenarios</h1>

      {current && current.length > 1 && (
        <div className="mb-4 flex items-center gap-2">
          <span className="text-[13px] text-text-muted">Home</span>
          <select
            value={homeId ?? ''}
            onChange={(e) => setHomeId(Number(e.target.value))}
            className="rounded bg-card text-[13px] text-text"
            style={{ padding: '5px 8px', border: '0.5px solid var(--border)' }}
          >
            {current.map((h) => (
              <option key={h.home_id} value={h.home_id}>
                {h.home_name}
              </option>
            ))}
          </select>
        </div>
      )}

      {selectedHome && (
        <div className="grid grid-cols-1 lg:grid-cols-2" style={{ gap: 16 }}>
          <CurrentScenarioCard home={selectedHome} canEdit={canEdit} />
          {homeId != null && <CapacityCard homeId={homeId} />}
          {homeId != null && <ScenarioCalendar homeId={homeId} canEdit={canEdit} />}
        </div>
      )}

      {/* Demand response context (price + events) */}
      <DrSection />
    </div>
  )
}

function CurrentScenarioCard({ home, canEdit }: { home: ScenarioCurrent; canEdit: boolean }) {
  const qc = useQueryClient()
  const initial = (home.scheduled_scenario ?? home.current_scenario ?? 'normal') as OperationScenario
  const [choice, setChoice] = useState<OperationScenario>(initial)
  const [confirm, setConfirm] = useState(false)
  const [result, setResult] = useState<ScenarioDispatchResult | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  // Track the selected home: re-seed the dispatch choice when it changes.
  useEffect(() => {
    setChoice((home.scheduled_scenario ?? home.current_scenario ?? 'normal') as OperationScenario)
    setResult(null)
    setErrorMsg(null)
  }, [home.home_id, home.scheduled_scenario, home.current_scenario])

  const dispatch = useMutation({
    mutationFn: () =>
      apiFetch<ScenarioDispatchResult>('/scenarios/dispatch', {
        method: 'POST',
        body: JSON.stringify({ home_id: home.home_id, operation_scenario: choice }),
      }),
    onSuccess: (r) => {
      setConfirm(false)
      setErrorMsg(null)
      setResult(r)
      qc.invalidateQueries({ queryKey: ['scenarios-current'] })
      qc.invalidateQueries({ queryKey: ['panel-mode', home.home_id] })
    },
    onError: (e) => {
      setConfirm(false)
      setErrorMsg(e instanceof ApiError ? e.message : 'Dispatch failed')
    },
  })

  return (
    <Card title="Current operation scenario">
      {errorMsg && <div className="mb-3 text-[13px] text-act">{errorMsg}</div>}

      <div className="mb-3 flex items-center gap-2">
        <Badge status={scenarioStatus(home.current_scenario)}>
          {scenarioLabel(home.current_scenario)}
        </Badge>
        {home.ts && (
          <span className="text-[12px] text-text-faint">
            {new Date(home.ts).toLocaleString([], {
              month: 'short',
              day: 'numeric',
              hour: '2-digit',
              minute: '2-digit',
            })}
          </span>
        )}
      </div>

      <MetricTile label="Scheduled today" value={scenarioLabel(home.scheduled_scenario)} />

      {canEdit && (
        <div className="mt-4" style={{ borderTop: '0.5px solid var(--border)', paddingTop: 12 }}>
          <div className="mb-2 text-[13px] text-text-muted">Dispatch scenario operation</div>
          <div className="flex items-center gap-2">
            <select
              value={choice}
              onChange={(e) => setChoice(e.target.value as OperationScenario)}
              className="rounded bg-card text-[13px] text-text"
              style={{ padding: '5px 8px', border: '0.5px solid var(--border)' }}
            >
              {SCENARIOS.map((s) => (
                <option key={s} value={s}>
                  {SCENARIO_LABEL[s]}
                </option>
              ))}
            </select>
            <button
              onClick={() => setConfirm(true)}
              disabled={dispatch.isPending}
              className="rounded text-[13px] font-medium"
              style={{ padding: '6px 14px', color: 'var(--bg-card)', background: 'var(--accent)' }}
            >
              Dispatch
            </button>
          </div>

          {result && (
            <div className="mt-3 flex flex-col gap-1">
              {result.dr_event_active && (
                <div className="text-[12px] text-text-faint">DR event active — used DR battery mode.</div>
              )}
              {result.steps.map((s, i) => (
                <div key={i} className="text-[12px]">
                  <span className="text-text-muted">{s.kind}</span>{' '}
                  <Badge status={STEP_STATUS[s.status] ?? 'info'}>{s.status}</Badge>
                  {s.detail && <span className="ml-2 text-text-faint">{s.detail}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <ConfirmDialog
        open={confirm}
        title={`Dispatch ${SCENARIO_LABEL[choice]} to ${home.home_name}?`}
        body={`This sends live commands: the panel battery mode and the thermostat band-widen setpoints for the ${SCENARIO_LABEL[choice]} scenario.`}
        danger={choice === 'resiliency' || choice === 'capacity_management'}
        busy={dispatch.isPending}
        confirmLabel="Dispatch"
        onCancel={() => setConfirm(false)}
        onConfirm={() => dispatch.mutate()}
      />
    </Card>
  )
}

const STEP_STATUS: Record<string, Status> = {
  success: 'ok',
  pending: 'watch',
  acknowledged: 'info',
  failed: 'act',
  skipped: 'offline',
}

function capacityStatus(over: boolean, near: boolean): { status: Status; color: string } {
  if (over) return { status: 'act', color: 'var(--act)' }
  if (near) return { status: 'watch', color: 'var(--watch)' }
  return { status: 'ok', color: 'var(--ok)' }
}

// Shared load bar: fill scaled to loadPct, a tick at the warn threshold, and
// three edge labels (start / threshold / full scale).
function CapacityBar({
  loadPct,
  triggerPct,
  color,
  left,
  mid,
  right,
}: {
  loadPct: number
  triggerPct: number
  color: string
  left: string
  mid: string
  right: string
}) {
  const barPct = Math.min(loadPct * 100, 100)
  const tickPct = Math.min(triggerPct * 100, 100)
  return (
    <div className="mt-3">
      <div className="relative h-2 rounded-full" style={{ background: 'var(--bg-subtle)' }}>
        <div className="absolute left-0 top-0 h-2 rounded-full" style={{ width: `${barPct}%`, background: color }} />
        <div className="absolute" style={{ left: `${tickPct}%`, top: -3, height: 14, width: 2, background: 'var(--text-faint)' }} />
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-text-faint">
        <span>{left}</span>
        <span>{mid}</span>
        <span>{right}</span>
      </div>
    </div>
  )
}

function PanelCapacitySection({ cap }: { cap: PanelCapacity }) {
  const loadPct = cap.load_pct ?? 0
  const { status, color } = capacityStatus(cap.over_capacity, cap.near_threshold)
  const statusLabel = cap.over_capacity ? 'over capacity' : cap.near_threshold ? 'near limit' : 'within limits'

  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[13px] font-medium text-text">Panel</span>
        <Badge status={status}>{statusLabel}</Badge>
        <span className="text-[12px] text-text-faint">
          {cap.current_a == null ? 'no reading' : `${Math.round(loadPct * 100)}% of ${cap.breaker_a} A breaker`}
        </span>
      </div>

      <MetricGrid cols={2}>
        <MetricTile
          label="Operating load"
          value={cap.current_kw == null ? '—' : cap.current_kw.toFixed(2)}
          unit={cap.current_kw == null ? undefined : `kW · ${cap.current_a} A`}
        />
        <MetricTile
          label="Breaker capacity"
          value={cap.capacity_kw.toFixed(1)}
          unit={`kW · ${cap.breaker_a} A`}
        />
      </MetricGrid>

      <CapacityBar
        loadPct={loadPct}
        triggerPct={cap.trigger_pct}
        color={color}
        left="0"
        mid={`trip ${cap.threshold_a} A · ${cap.threshold_kw} kW (${Math.round(cap.trigger_pct * 100)}%)`}
        right={`${cap.breaker_a} A`}
      />
    </div>
  )
}

function BatteryCapacitySection({ cap }: { cap: BatteryCapacity }) {
  const loadPct = cap.load_pct ?? 0
  const { status, color } = capacityStatus(cap.over_capacity, cap.near_threshold)
  const statusLabel = cap.over_capacity ? 'over inverter capacity' : cap.near_threshold ? 'near inverter limit' : 'within inverter limits'

  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[13px] font-medium text-text">Battery inverter</span>
        <Badge status={status}>{statusLabel}</Badge>
        <span className="text-[12px] text-text-faint">
          {cap.inverter_count === 0
            ? 'no battery inverter'
            : cap.current_load_kw == null
              ? 'no reading'
              : `${Math.round(loadPct * 100)}% of ${cap.inverter_count}×${cap.inverter_capacity_kw} kW`}
        </span>
      </div>

      <MetricGrid cols={2}>
        <MetricTile
          label="Operating load"
          value={cap.current_load_kw == null ? '—' : cap.current_load_kw.toFixed(2)}
          unit={cap.current_load_kw == null ? undefined : 'kW'}
        />
        <MetricTile
          label="Inverter capacity"
          value={cap.total_capacity_kw.toFixed(1)}
          unit={`kW · ${cap.inverter_count}×${cap.inverter_capacity_kw} kW`}
        />
        <MetricTile
          label="Battery output"
          value={cap.battery_power_kw == null ? '—' : cap.battery_power_kw.toFixed(2)}
          unit={cap.battery_power_kw == null ? undefined : 'kW'}
        />
        <MetricTile
          label="Headroom"
          value={cap.current_load_kw == null ? '—' : (cap.total_capacity_kw - cap.current_load_kw).toFixed(2)}
          unit={cap.current_load_kw == null ? undefined : 'kW'}
        />
      </MetricGrid>

      <CapacityBar
        loadPct={loadPct}
        triggerPct={cap.trigger_pct}
        color={color}
        left="0"
        mid={`limit ${cap.threshold_kw} kW (${Math.round(cap.trigger_pct * 100)}%)`}
        right={`${cap.total_capacity_kw} kW`}
      />
    </div>
  )
}

function CapacityCard({ homeId }: { homeId: number }) {
  const panelQ = useQuery({
    queryKey: ['scenario-capacity', homeId],
    queryFn: () => apiFetch<PanelCapacity>(`/scenarios/${homeId}/capacity`),
    refetchInterval: 30_000,
  })
  const batteryQ = useQuery({
    queryKey: ['battery-capacity', homeId],
    queryFn: () => apiFetch<BatteryCapacity>(`/scenarios/${homeId}/battery-capacity`),
    refetchInterval: 30_000,
  })

  return (
    <Card title="Capacity">
      {panelQ.isLoading ? (
        <div className="text-[13px] text-text-muted">Loading panel…</div>
      ) : panelQ.error || !panelQ.data ? (
        <div className="text-[13px] text-act">Failed to load panel capacity.</div>
      ) : (
        <PanelCapacitySection cap={panelQ.data} />
      )}

      <div className="my-4" style={{ borderTop: '0.5px solid var(--border)' }} />

      {batteryQ.isLoading ? (
        <div className="text-[13px] text-text-muted">Loading battery…</div>
      ) : batteryQ.error || !batteryQ.data ? (
        <div className="text-[13px] text-act">Failed to load battery capacity.</div>
      ) : (
        <BatteryCapacitySection cap={batteryQ.data} />
      )}
    </Card>
  )
}

// =====================================================================
// Demand response context (moved from the old DR page)
// =====================================================================
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

function DrSection() {
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

  // Pin the price curve to the current local day (midnight to midnight).
  const dayStart = new Date()
  dayStart.setHours(0, 0, 0, 0)
  const dayEnd = new Date(dayStart)
  dayEnd.setDate(dayEnd.getDate() + 1)

  const { data: curve } = useQuery({
    queryKey: ['openadr-price-history', dayStart.toISOString()],
    queryFn: () =>
      apiFetch<OpenAdrPricePoint[]>(
        `/openadr/price/history?from=${encodeURIComponent(dayStart.toISOString())}` +
          `&to=${encodeURIComponent(dayEnd.toISOString())}`,
      ),
    refetchInterval: 300_000,
  })

  const active = events?.find((e) => e.active) ?? null

  useEffect(() => {
    if (selected == null && events && events.length > 0) {
      setSelected((active ?? events[0]).event_id)
    }
  }, [events, active, selected])

  return (
    <div className="mt-6">
      <h2 className="mb-3 text-[16px] font-medium text-text">Demand response</h2>

      {price && price.price_per_kwh != null && <PriceBanner price={price} />}

      {active && (
        <div className="mt-4">
          <ActiveEventCard event={active} />
        </div>
      )}

      <div className="mt-4 grid grid-cols-1 lg:grid-cols-2" style={{ gap: 16 }}>
        <Card title="Price curve (today)">
          {!curve || curve.length === 0 ? (
            <div className="text-[13px] text-text-muted">No price data.</div>
          ) : (
            <PriceCurveChart
              points={curve}
              min={dayStart.toISOString()}
              max={dayEnd.toISOString()}
            />
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
