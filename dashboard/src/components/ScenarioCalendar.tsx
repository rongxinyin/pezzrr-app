import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card } from './Card'
import { STATUS_COLORS } from './status'
import { apiFetch, ApiError } from '../lib/api'
import { SCENARIOS, SCENARIO_LABEL, scenarioStatus } from '../lib/scenarios'
import type { OperationScenario, ScenarioScheduleEntry } from '../lib/types'

const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa']

function pad(n: number): string {
  return String(n).padStart(2, '0')
}
function monthKey(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`
}
function dayKey(year: number, month: number, day: number): string {
  return `${year}-${pad(month + 1)}-${pad(day)}`
}
function monthTitle(d: Date): string {
  return d.toLocaleDateString([], { month: 'long', year: 'numeric' })
}

interface Props {
  homeId: number
  canEdit: boolean
}

// Month grid for an operator to pin a per-day operation scenario. Clicking a
// day selects it; the scenario chips below write/clear scenario_schedule for
// the selected day. Each scheduled day is tinted with its scenario color.
export function ScenarioCalendar({ homeId, canEdit }: Props) {
  const qc = useQueryClient()
  const [cursor, setCursor] = useState(() => {
    const now = new Date()
    return new Date(now.getFullYear(), now.getMonth(), 1)
  })
  const [selected, setSelected] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const month = monthKey(cursor)
  const { data: entries } = useQuery({
    queryKey: ['scenario-schedule', homeId, month],
    queryFn: () =>
      apiFetch<ScenarioScheduleEntry[]>(
        `/scenarios/schedule?home_id=${homeId}&month=${month}`,
      ),
  })

  const byDate = new Map<string, OperationScenario>()
  for (const e of entries ?? []) byDate.set(e.scenario_date, e.operation_scenario)

  function invalidate() {
    qc.invalidateQueries({ queryKey: ['scenario-schedule', homeId, month] })
    qc.invalidateQueries({ queryKey: ['scenarios-current'] })
  }

  const setMut = useMutation({
    mutationFn: (v: { date: string; scenario: OperationScenario }) =>
      apiFetch<ScenarioScheduleEntry>('/scenarios/schedule', {
        method: 'PUT',
        body: JSON.stringify({
          home_id: homeId,
          scenario_date: v.date,
          operation_scenario: v.scenario,
        }),
      }),
    onSuccess: () => {
      setErrorMsg(null)
      invalidate()
    },
    onError: (e) => setErrorMsg(e instanceof ApiError ? e.message : 'Failed to save'),
  })

  const clearMut = useMutation({
    mutationFn: (date: string) =>
      apiFetch<void>(`/scenarios/schedule?home_id=${homeId}&scenario_date=${date}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      setErrorMsg(null)
      invalidate()
    },
    onError: (e) => setErrorMsg(e instanceof ApiError ? e.message : 'Failed to clear'),
  })

  const busy = setMut.isPending || clearMut.isPending

  const year = cursor.getFullYear()
  const m = cursor.getMonth()
  const leading = new Date(year, m, 1).getDay()
  const daysInMonth = new Date(year, m + 1, 0).getDate()
  const today = new Date()
  const todayKey = dayKey(today.getFullYear(), today.getMonth(), today.getDate())
  const todayMidnight = new Date(today.getFullYear(), today.getMonth(), today.getDate())

  const cells: (number | null)[] = []
  for (let i = 0; i < leading; i++) cells.push(null)
  for (let d = 1; d <= daysInMonth; d++) cells.push(d)

  // Month summary: how many days are pinned to each scenario.
  const counts = new Map<OperationScenario, number>()
  for (const s of byDate.values()) counts.set(s, (counts.get(s) ?? 0) + 1)

  return (
    <Card
      title="Scenario calendar"
      action={
        <div className="flex items-center gap-2">
          <NavBtn label="‹" onClick={() => setCursor(new Date(year, m - 1, 1))} />
          <span className="text-[13px] text-text-muted" style={{ minWidth: 120, textAlign: 'center' }}>
            {monthTitle(cursor)}
          </span>
          <NavBtn label="›" onClick={() => setCursor(new Date(year, m + 1, 1))} />
        </div>
      }
    >
      {errorMsg && (
        <div className="mb-3 text-[13px] text-act">{errorMsg}</div>
      )}

      <div className="grid grid-cols-7 gap-1">
        {WEEKDAYS.map((w) => (
          <div key={w} className="py-1 text-center text-[11px] text-text-faint">
            {w}
          </div>
        ))}
        {cells.map((d, i) => {
          if (d == null) return <div key={`b${i}`} />
          const key = dayKey(year, m, d)
          const scn = byDate.get(key)
          const isToday = key === todayKey
          const isSelected = key === selected
          const isPast = new Date(year, m, d) < todayMidnight
          const editable = canEdit && !isPast
          const colors = scn ? STATUS_COLORS[scenarioStatus(scn)] : null
          return (
            <button
              key={key}
              onClick={() => editable && setSelected(key)}
              disabled={!editable}
              title={isPast ? 'Past date — not editable' : scn ? SCENARIO_LABEL[scn] : undefined}
              className="flex flex-col items-center rounded py-1 text-[12px]"
              style={{
                background: colors ? colors.bg : 'var(--bg-subtle)',
                color: colors ? colors.fg : 'var(--text-muted)',
                border: isSelected
                  ? '1px solid var(--accent)'
                  : isToday
                    ? '1px solid var(--border)'
                    : '0.5px solid transparent',
                cursor: editable ? 'pointer' : 'default',
                opacity: isPast ? 0.4 : 1,
                minHeight: 40,
              }}
            >
              <span className={isToday ? 'font-semibold' : ''}>{d}</span>
              {scn && (
                <span className="mt-0.5 text-[9px] leading-tight">
                  {SCENARIO_LABEL[scn]}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Day setter */}
      {canEdit && (
        <div className="mt-4" style={{ borderTop: '0.5px solid var(--border)', paddingTop: 12 }}>
          {selected == null ? (
            <div className="text-[13px] text-text-faint">
              Click a day to set its scenario.
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              <div className="text-[13px] text-text-muted">
                Set <span className="font-medium text-text">{selected}</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {SCENARIOS.map((s) => {
                  const colors = STATUS_COLORS[scenarioStatus(s)]
                  const active = byDate.get(selected) === s
                  return (
                    <button
                      key={s}
                      onClick={() => setMut.mutate({ date: selected, scenario: s })}
                      disabled={busy}
                      className="rounded text-[13px] font-medium"
                      style={{
                        padding: '5px 12px',
                        color: colors.fg,
                        background: colors.bg,
                        border: active ? '1px solid var(--accent)' : '0.5px solid var(--border)',
                      }}
                    >
                      {SCENARIO_LABEL[s]}
                    </button>
                  )
                })}
                <button
                  onClick={() => clearMut.mutate(selected)}
                  disabled={busy || !byDate.has(selected)}
                  className="rounded text-[13px] text-text-muted"
                  style={{ padding: '5px 12px', border: '0.5px solid var(--border)' }}
                >
                  Clear
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Month summary */}
      <div className="mt-4 flex flex-wrap gap-3">
        {SCENARIOS.map((s) => {
          const colors = STATUS_COLORS[scenarioStatus(s)]
          return (
            <span key={s} className="flex items-center gap-1.5 text-[12px] text-text-muted">
              <span
                className="inline-block rounded"
                style={{ width: 10, height: 10, background: colors.fg }}
              />
              {SCENARIO_LABEL[s]}
              <span className="text-text-faint">· {counts.get(s) ?? 0}d</span>
            </span>
          )
        })}
      </div>
    </Card>
  )
}

function NavBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="rounded text-[14px] text-text-muted"
      style={{ padding: '2px 10px', border: '0.5px solid var(--border)', background: 'var(--bg-card)' }}
    >
      {label}
    </button>
  )
}
