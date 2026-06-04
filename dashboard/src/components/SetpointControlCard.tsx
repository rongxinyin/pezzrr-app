import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card } from './Card'
import { Badge } from './Badge'
import { SetpointPlanChart } from './charts/SetpointPlanChart'
import { apiFetch } from '../lib/api'
import type { Status } from './status'
import type { DispatchRequest, SetpointController, SetpointPlan, ThermostatLive } from '../lib/types'

const cToF = (c: number | null | undefined) => (c == null ? null : Math.round((c * 9) / 5 + 32))

const CONTROLLERS: { key: SetpointController; label: string }[] = [
  { key: 'baseline', label: 'Baseline' },
  { key: 'rbc', label: 'RBC' },
  { key: 'mpc', label: 'MPC' },
]

interface Props {
  homeId: number
  live: ThermostatLive | null
  busy: boolean
  onDispatch: (p: { title: string; body: string; danger: boolean; request: DispatchRequest }) => void
}

// Thermostat Setpoint Control (§10): compare the baseline / RBC / MPC forward
// 24h setpoint plans against the forecast outdoor-air temp, then promote the
// selected controller's immediate setpoint into a live setpoint_adjust dispatch.
export function SetpointControlCard({ homeId, live, busy, onDispatch }: Props) {
  const [controller, setController] = useState<SetpointController>('rbc')

  const { data: plan } = useQuery({
    queryKey: ['setpoint-plan', homeId, controller],
    queryFn: () =>
      apiFetch<SetpointPlan>(`/control/setpoint-plan?home_id=${homeId}&controller=${controller}`),
    refetchInterval: 60_000,
  })

  const { data: home } = useQuery({
    queryKey: ['home-devices', homeId],
    queryFn: () => apiFetch<{ devices: { device_id: number; device_type: string }[] }>(`/homes/${homeId}`),
  })
  const deviceId = home?.devices.find((d) => d.device_type === 'thermostat')?.device_id ?? null

  const coolC = plan?.immediate_cool_setpoint_c ?? null
  const heatC = plan?.immediate_heat_setpoint_c ?? null
  const coolF = cToF(coolC)
  const heatF = cToF(heatC)
  const canApply =
    plan != null && plan.available && (coolC != null || heatC != null) && deviceId != null

  function apply() {
    if (!canApply) return
    const params: Record<string, number> = {}
    if (coolC != null) params.cool_setpoint_c = coolC
    if (heatC != null) params.heat_setpoint_c = heatC
    const label = CONTROLLERS.find((c) => c.key === controller)?.label ?? controller
    const setpointText = [heatF != null && `heat ${heatF}°F`, coolF != null && `cool ${coolF}°F`]
      .filter(Boolean)
      .join(' / ')
    onDispatch({
      title: `Apply ${label} setpoint?`,
      body: `This dispatches the ${label} immediate setpoint: ${setpointText}.`,
      danger: false,
      request: {
        home_id: homeId,
        action_type: 'setpoint_adjust',
        target: { kind: 'thermostat', device_id: deviceId },
        params,
      },
    })
  }

  const stateStatus: Status =
    live?.hvac_state === 'heating' || live?.hvac_state === 'cooling' ? 'info' : 'offline'

  return (
    <Card title="Thermostat Setpoint Control">
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

      <div className="mb-3 flex items-center gap-1">
        {CONTROLLERS.map((c) => {
          const active = c.key === controller
          return (
            <button
              key={c.key}
              onClick={() => setController(c.key)}
              className="rounded text-[13px] font-medium"
              style={{
                padding: '5px 14px',
                color: active ? 'var(--bg-card)' : 'var(--text-muted)',
                background: active ? 'var(--accent)' : 'var(--bg-subtle)',
                border: active ? 'none' : '0.5px solid var(--border)',
              }}
            >
              {c.label}
            </button>
          )
        })}
      </div>

      {plan && !plan.available ? (
        <div className="py-8 text-center text-[13px] text-text-muted">
          {plan.note ?? 'No plan available.'}
        </div>
      ) : plan && plan.points.length > 0 ? (
        <SetpointPlanChart points={plan.points} forecast={plan.forecast} />
      ) : (
        <div className="py-8 text-center text-[13px] text-text-muted">Loading plan…</div>
      )}

      <div className="mt-3 flex items-center gap-4 border-t pt-3" style={{ borderColor: 'var(--border)' }}>
        <div className="text-[13px] text-text-muted">
          Immediate setpoint:{' '}
          <span className="text-text tabular-nums">
            {heatF != null && `heat ${heatF}°F`}
            {heatF != null && coolF != null && ' / '}
            {coolF != null && `cool ${coolF}°F`}
            {heatF == null && coolF == null && '—'}
          </span>
        </div>
        <button
          onClick={apply}
          disabled={!canApply || busy}
          className="ml-auto rounded text-[13px] font-medium"
          style={{
            padding: '6px 14px',
            color: canApply ? 'var(--bg-card)' : 'var(--text-faint)',
            background: canApply ? 'var(--accent)' : 'var(--bg-subtle)',
            border: canApply ? 'none' : '0.5px solid var(--border)',
            cursor: canApply && !busy ? 'pointer' : 'default',
          }}
        >
          Apply
        </button>
      </div>
    </Card>
  )
}
