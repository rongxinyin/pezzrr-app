import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card } from './Card'
import { apiFetch } from '../lib/api'
import type { DispatchRequest, PanelMode } from '../lib/types'

const SAVINGS_MODES: { value: number; label: string }[] = [
  { value: 0, label: 'Off' },
  { value: 1, label: 'Time-of-use' },
  { value: 2, label: 'Self-powered' },
  { value: 3, label: 'Timed' },
]

const modeLabel = (v: number | null) =>
  SAVINGS_MODES.find((m) => m.value === v)?.label ?? '—'

interface Draft {
  smartBackupMode: number | null
  epsModeInfo: boolean | null
  backupReserveSoc: number | null
  chargeWattPower: number | null
  foceChargeHight: number | null
}

function toDraft(m: PanelMode): Draft {
  return {
    smartBackupMode: m.smartBackupMode,
    epsModeInfo: m.epsModeInfo,
    backupReserveSoc: m.backupReserveSoc,
    chargeWattPower: m.chargeWattPower,
    foceChargeHight: m.foceChargeHight,
  }
}

// Only the keys that differ from the panel's current values; this is the
// smallest live write, so an Apply never re-pushes settings the user didn't touch.
function changedParams(current: PanelMode, draft: Draft): Record<string, number | boolean> {
  const out: Record<string, number | boolean> = {}
  for (const key of Object.keys(draft) as (keyof Draft)[]) {
    const next = draft[key]
    if (next != null && next !== current[key]) out[key] = next
  }
  return out
}

interface Props {
  homeId: number
  busy: boolean
  onDispatch: (p: { title: string; body: string; danger: boolean; request: DispatchRequest }) => void
}

export function PanelModeCard({ homeId, busy, onDispatch }: Props) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['panel-mode', homeId],
    queryFn: () => apiFetch<PanelMode>(`/control/panel-mode?home_id=${homeId}`),
    refetchInterval: 30_000,
  })
  const [draft, setDraft] = useState<Draft | null>(null)

  // Re-seed the form whenever fresh panel state arrives (initial load + refetch
  // after a dispatch), so the inputs track the device, not a stale edit.
  useEffect(() => {
    if (data) setDraft(toDraft(data))
  }, [data])

  if (isError) {
    return (
      <Card title="Panel operating mode">
        <div className="text-[13px] text-text-muted">No smart panel for this home.</div>
      </Card>
    )
  }
  if (isLoading || !data || !draft) {
    return (
      <Card title="Panel operating mode">
        <div className="text-[13px] text-text-muted">Loading panel state…</div>
      </Card>
    )
  }

  const changed = changedParams(data, draft)
  const dirty = Object.keys(changed).length > 0

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((d) => (d ? { ...d, [key]: value } : d))
  }

  function apply() {
    const summary = Object.entries(changed)
      .map(([k, v]) => (k === 'smartBackupMode' ? `mode → ${modeLabel(draft!.smartBackupMode)}` : `${k} → ${v}`))
      .join(', ')
    onDispatch({
      title: 'Update panel operating mode?',
      body: `This writes to the live Smart Home Panel: ${summary}.`,
      danger: false,
      request: {
        home_id: homeId,
        action_type: 'set_operating_mode',
        target: { kind: 'battery_mode', device_id: data!.device_id },
        params: changed,
      },
    })
  }

  return (
    <Card
      title="Panel operating mode"
      action={
        <button
          onClick={apply}
          disabled={!dirty || busy}
          className="rounded text-[13px] font-medium"
          style={{
            padding: '6px 14px',
            color: dirty ? 'var(--bg-card)' : 'var(--text-faint)',
            background: dirty ? 'var(--accent)' : 'var(--bg-subtle)',
            border: dirty ? 'none' : '0.5px solid var(--border)',
            cursor: dirty && !busy ? 'pointer' : 'default',
          }}
        >
          Apply
        </button>
      }
    >
      <div
        className="mb-3 flex items-center justify-between rounded px-3 py-2 text-[13px]"
        style={{ background: 'var(--bg-subtle)' }}
      >
        <span className="text-text-muted">Current mode</span>
        <span className="text-text">
          <span className="font-medium">{modeLabel(data.smartBackupMode)}</span>
          <span className="ml-2 text-text-faint">
            EPS {data.epsModeInfo ? 'on' : 'off'}
            {data.backupReserveSoc != null && ` · reserve ${data.backupReserveSoc}%`}
          </span>
        </span>
      </div>

      <div className="flex flex-col gap-3">
        <Field label="Savings mode">
          <select
            value={draft.smartBackupMode ?? ''}
            onChange={(e) => set('smartBackupMode', Number(e.target.value))}
            className="rounded bg-card text-[13px] text-text"
            style={{ padding: '5px 8px', border: '0.5px solid var(--border)' }}
          >
            {SAVINGS_MODES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </Field>

        <Field label="EPS backup">
          <button
            onClick={() => set('epsModeInfo', !draft.epsModeInfo)}
            className="rounded text-[13px] font-medium"
            style={{
              padding: '5px 12px',
              color: draft.epsModeInfo ? 'var(--bg-card)' : 'var(--text)',
              background: draft.epsModeInfo ? 'var(--accent)' : 'var(--bg-subtle)',
              border: '0.5px solid var(--border)',
            }}
          >
            {draft.epsModeInfo ? 'On' : 'Off'}
          </button>
        </Field>

        <Field label="Backup reserve (%)">
          <NumberInput
            value={draft.backupReserveSoc}
            min={0}
            max={100}
            onChange={(v) => set('backupReserveSoc', v)}
          />
        </Field>

        <Field label="Charge power (W)">
          <NumberInput
            value={draft.chargeWattPower}
            min={500}
            max={7200}
            step={100}
            onChange={(v) => set('chargeWattPower', v)}
          />
        </Field>

        <Field label="Charge limit (%)">
          <NumberInput
            value={draft.foceChargeHight}
            min={80}
            max={100}
            onChange={(v) => set('foceChargeHight', v)}
          />
        </Field>
      </div>
    </Card>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-[13px] text-text-muted">{label}</span>
      {children}
    </div>
  )
}

function NumberInput({
  value,
  min,
  max,
  step,
  onChange,
}: {
  value: number | null
  min: number
  max: number
  step?: number
  onChange: (v: number | null) => void
}) {
  return (
    <input
      type="number"
      value={value ?? ''}
      min={min}
      max={max}
      step={step}
      onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
      className="rounded bg-card text-[13px] text-text tabular-nums"
      style={{ padding: '5px 8px', width: 96, border: '0.5px solid var(--border)' }}
    />
  )
}
