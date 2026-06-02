import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card } from './Card'
import { Badge } from './Badge'
import { ThermostatChart } from './charts/ThermostatChart'
import { apiFetch } from '../lib/api'
import type { Status } from './status'
import type { DispatchRequest, ThermostatEnvelope, ThermostatLive } from '../lib/types'

const cToF = (c: number | null | undefined) => (c == null ? null : Math.round((c * 9) / 5 + 32))
const fToC = (f: number) => Math.round(((f - 32) * 5) / 9 * 10) / 10

interface Props {
  homeId: number
  live: ThermostatLive | null
  busy: boolean
  onDispatch: (p: { title: string; body: string; danger: boolean; request: DispatchRequest }) => void
}

export function ThermostatCard({ homeId, live, busy, onDispatch }: Props) {
  const range = useMemo(() => {
    const to = new Date()
    const from = new Date(to.getTime() - 24 * 3600 * 1000)
    return { from: from.toISOString(), to: to.toISOString() }
  }, [])

  const { data: history } = useQuery({
    queryKey: ['thermostat-24h', homeId, range.from],
    queryFn: () =>
      apiFetch<ThermostatEnvelope>(
        `/homes/${homeId}/thermostat?from=${encodeURIComponent(range.from)}&to=${encodeURIComponent(range.to)}&bucket=5m`,
      ),
  })

  const { data: home } = useQuery({
    queryKey: ['home-devices', homeId],
    queryFn: () => apiFetch<{ devices: { device_id: number; device_type: string }[] }>(`/homes/${homeId}`),
  })
  const deviceId = home?.devices.find((d) => d.device_type === 'thermostat')?.device_id ?? null

  const liveHeatF = cToF(live?.heat_setpoint_c)
  const liveCoolF = cToF(live?.cool_setpoint_c)
  const [heatF, setHeatF] = useState<number | null>(null)
  const [coolF, setCoolF] = useState<number | null>(null)

  // Seed the inputs from the live setpoints once they arrive; don't re-seed on
  // every stream tick or it would clobber a half-typed edit.
  useEffect(() => {
    if (heatF == null && liveHeatF != null) setHeatF(liveHeatF)
    if (coolF == null && liveCoolF != null) setCoolF(liveCoolF)
  }, [liveHeatF, liveCoolF, heatF, coolF])

  const dirty = (heatF != null && heatF !== liveHeatF) || (coolF != null && coolF !== liveCoolF)
  const valid = heatF != null && coolF != null && heatF < coolF

  function apply() {
    if (!valid) return
    onDispatch({
      title: 'Adjust thermostat setpoints?',
      body: `This dispatches a setpoint change: heat ${heatF}°F / cool ${coolF}°F.`,
      danger: false,
      request: {
        home_id: homeId,
        action_type: 'setpoint_adjust',
        target: { kind: 'thermostat', device_id: deviceId },
        params: { heat_setpoint_c: fToC(heatF!), cool_setpoint_c: fToC(coolF!) },
      },
    })
  }

  const stateStatus: Status =
    live?.hvac_state === 'heating' || live?.hvac_state === 'cooling' ? 'info' : 'offline'

  return (
    <Card title="Thermostat">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <span className="text-text" style={{ fontSize: 26, fontWeight: 500 }}>
            {cToF(live?.indoor_temp_c) ?? '—'}°F
          </span>
          {live?.indoor_humidity_pct != null && (
            <span className="ml-2 text-[13px] text-text-muted">{live.indoor_humidity_pct}% RH</span>
          )}
        </div>
        <div className="flex items-center gap-2 text-[13px] text-text-muted">
          <span className="capitalize">mode: {live?.hvac_mode ?? '—'}</span>
          <Badge status={stateStatus}>{live?.hvac_state ?? 'idle'}</Badge>
        </div>
      </div>

      {history && history.points.length > 0 ? (
        <ThermostatChart points={history.points} />
      ) : (
        <div className="py-8 text-center text-[13px] text-text-muted">No thermostat history.</div>
      )}

      <div className="mt-3 flex items-center gap-4 border-t pt-3" style={{ borderColor: 'var(--border)' }}>
        <SetpointInput label="Heat" value={heatF} onChange={setHeatF} />
        <SetpointInput label="Cool" value={coolF} onChange={setCoolF} />
        <div className="ml-auto flex items-center gap-2">
          {dirty && !valid && (
            <span className="text-[12px] text-act">Heat must be below cool</span>
          )}
          <button
            onClick={apply}
            disabled={!dirty || !valid || busy}
            className="rounded text-[13px] font-medium"
            style={{
              padding: '6px 14px',
              color: dirty && valid ? 'var(--bg-card)' : 'var(--text-faint)',
              background: dirty && valid ? 'var(--accent)' : 'var(--bg-subtle)',
              border: dirty && valid ? 'none' : '0.5px solid var(--border)',
              cursor: dirty && valid && !busy ? 'pointer' : 'default',
            }}
          >
            Apply
          </button>
        </div>
      </div>
    </Card>
  )
}

function SetpointInput({
  label,
  value,
  onChange,
}: {
  label: string
  value: number | null
  onChange: (v: number | null) => void
}) {
  return (
    <label className="flex items-center gap-2 text-[13px] text-text-muted">
      {label}
      <input
        type="number"
        value={value ?? ''}
        min={45}
        max={95}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
        className="rounded bg-card text-[13px] text-text tabular-nums"
        style={{ padding: '5px 8px', width: 64, border: '0.5px solid var(--border)' }}
      />
      °F
    </label>
  )
}
