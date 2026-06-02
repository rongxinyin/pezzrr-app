import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card } from '../components/Card'
import { Badge } from '../components/Badge'
import { PRIORITY_LABEL, PRIORITY_STATUS } from '../components/status'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { AdvisoryCard } from '../components/AdvisoryCard'
import { ActionLog } from '../components/ActionLog'
import { PanelModeCard } from '../components/PanelModeCard'
import { SetpointControlCard } from '../components/SetpointControlCard'
import { ThermostatCard } from '../components/ThermostatCard'
import { useHomeStream } from '../hooks/useHomeStream'
import { apiFetch, ApiError } from '../lib/api'
import type { ControlAdvisory, CircuitLive, DispatchRequest, DispatchResponse } from '../lib/types'

interface PendingDispatch {
  title: string
  body: string
  danger: boolean
  request: DispatchRequest
}

export function Control() {
  const { id } = useParams()
  const homeId = Number(id)
  const qc = useQueryClient()
  const { data: live } = useHomeStream(homeId)
  const [pending, setPending] = useState<PendingDispatch | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const { data: advisories } = useQuery({
    queryKey: ['control-advisories', homeId],
    queryFn: () => apiFetch<ControlAdvisory[]>(`/control/advisories?home_id=${homeId}`),
    refetchInterval: 30_000,
  })

  const dispatch = useMutation({
    mutationFn: (req: DispatchRequest) =>
      apiFetch<DispatchResponse>('/control/dispatch', {
        method: 'POST',
        body: JSON.stringify(req),
      }),
    onSuccess: () => {
      setPending(null)
      setErrorMsg(null)
      qc.invalidateQueries({ queryKey: ['control-actions', homeId] })
      qc.invalidateQueries({ queryKey: ['panel-mode', homeId] })
      qc.invalidateQueries({ queryKey: ['setpoint-plan', homeId] })
    },
    onError: (e) => {
      setErrorMsg(e instanceof ApiError ? e.message : 'Dispatch failed')
      setPending(null)
    },
  })

  function toggleCircuit(c: CircuitLive) {
    const turningOff = c.is_enabled !== false
    const action_type = turningOff ? 'curtail' : 'release'
    const name = c.circuit_name ?? `Channel ${c.channel_num}`
    setPending({
      title: `${turningOff ? 'Curtail' : 'Restore'} ${name}?`,
      body: turningOff
        ? `This sends a curtail command to ${name}. The edge controller will switch the load off.`
        : `This restores ${name} to normal operation.`,
      danger: turningOff,
      request: { home_id: homeId, action_type, target: { kind: 'circuit', circuit_id: c.circuit_id } },
    })
  }

  function applyAdvisory(a: ControlAdvisory) {
    if (a.circuit_id == null) return
    setPending({
      title: `Apply ${a.controller.toUpperCase()} recommendation?`,
      body: `Promote this ${a.action_type.replace(/_/g, ' ')} advisory into a live dispatch.`,
      danger: false,
      request: {
        home_id: homeId,
        action_type: a.action_type,
        target: { kind: 'circuit', circuit_id: a.circuit_id },
        event_id: a.event_id,
      },
    })
  }

  const circuits = live?.circuits ?? []
  // Setpoint advisories are now driven by the Thermostat Setpoint Control card;
  // only circuit advisories remain promotable here.
  const circuitAdvisories = (advisories ?? []).filter((a) => a.circuit_id != null)

  return (
    <div>
      <Link to={`/homes/${homeId}`} className="text-[13px] text-accent">
        ← Home detail
      </Link>
      <h1 className="mb-4 mt-2 text-[22px] font-medium text-text">Control &amp; dispatch</h1>

      {errorMsg && (
        <div
          className="mb-4 rounded px-4 py-2 text-[13px] text-act"
          style={{ background: 'var(--act-bg)', border: '0.5px solid var(--border)' }}
        >
          {errorMsg}
        </div>
      )}

      <div className="flex flex-col gap-4">
        {circuitAdvisories.length > 0 && (
          <div>
            <h2 className="mb-2 text-[15px] font-medium text-text">Advisories</h2>
            <div className="flex flex-col gap-3">
              {circuitAdvisories.map((a) => (
                <AdvisoryCard
                  key={a.advisory_id}
                  advisory={a}
                  onApply={applyAdvisory}
                  applying={dispatch.isPending}
                />
              ))}
            </div>
          </div>
        )}

        <Card title="Circuits">
          {circuits.length === 0 ? (
            <div className="text-[13px] text-text-muted">No circuit data.</div>
          ) : (
            <div className="flex flex-col gap-2">
              {circuits.map((c) => (
                <CircuitRow key={c.circuit_id} c={c} onToggle={toggleCircuit} disabled={dispatch.isPending} />
              ))}
            </div>
          )}
        </Card>

        <ThermostatCard
          homeId={homeId}
          live={live?.thermostat ?? null}
          busy={dispatch.isPending}
          onDispatch={setPending}
        />

        <SetpointControlCard
          homeId={homeId}
          live={live?.thermostat ?? null}
          busy={dispatch.isPending}
          onDispatch={setPending}
        />

        <PanelModeCard homeId={homeId} busy={dispatch.isPending} onDispatch={setPending} />

        <ActionLog homeId={homeId} />
      </div>

      <ConfirmDialog
        open={pending != null}
        title={pending?.title ?? ''}
        body={pending?.body ?? ''}
        danger={pending?.danger}
        busy={dispatch.isPending}
        confirmLabel="Dispatch"
        onCancel={() => setPending(null)}
        onConfirm={() => pending && dispatch.mutate(pending.request)}
      />
    </div>
  )
}

function CircuitRow({
  c,
  onToggle,
  disabled,
}: {
  c: CircuitLive
  onToggle: (c: CircuitLive) => void
  disabled: boolean
}) {
  const name = c.circuit_name ?? `Channel ${c.channel_num}`
  const off = c.is_enabled === false
  const locked = c.is_critical || !c.is_controllable
  return (
    <div className="flex items-center justify-between rounded px-3 py-2" style={{ background: 'var(--bg-subtle)' }}>
      <div className="min-w-0">
        <div className="truncate text-[14px] text-text">
          {name}
          {c.circuit_priority && (
            <span className="ml-2 align-middle">
              <Badge status={PRIORITY_STATUS[c.circuit_priority]}>{PRIORITY_LABEL[c.circuit_priority]}</Badge>
            </span>
          )}
        </div>
        <div className="text-[12px] text-text-faint tabular-nums">{c.power_w ?? 0} W{off && ' · off'}</div>
      </div>
      {locked ? (
        <span className="text-[12px] text-text-faint" title={c.is_critical ? 'Critical circuit — locked' : 'Not controllable'}>
          🔒 locked
        </span>
      ) : (
        <button
          onClick={() => onToggle(c)}
          disabled={disabled}
          className="rounded text-[13px] font-medium"
          style={{
            padding: '6px 14px',
            color: off ? 'var(--bg-card)' : 'var(--act)',
            background: off ? 'var(--accent)' : 'var(--act-bg)',
            border: off ? 'none' : '0.5px solid var(--border)',
          }}
        >
          {off ? 'Restore' : 'Curtail'}
        </button>
      )}
    </div>
  )
}
