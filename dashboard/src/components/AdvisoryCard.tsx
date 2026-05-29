import { Card } from './Card'
import { Badge } from './Badge'
import type { ControlAdvisory } from '../lib/types'

function cToF(c: number | null): string {
  return c == null ? '—' : Math.round((c * 9) / 5 + 32).toString()
}

// A shadow-mode MPC/RBC recommendation (§13.5). "Apply" promotes the advisory
// into a real dispatch; the parent owns the confirm + POST.
export function AdvisoryCard({
  advisory,
  onApply,
  applying = false,
}: {
  advisory: ControlAdvisory
  onApply: (a: ControlAdvisory) => void
  applying?: boolean
}) {
  const a = advisory
  const hasSetpoints =
    a.recommended_cool_setpoint_c != null || a.recommended_heat_setpoint_c != null

  return (
    <Card>
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-[15px] font-medium text-text capitalize">
              {a.action_type.replace(/_/g, ' ')}
            </span>
            <Badge status="info">{a.controller.toUpperCase()}</Badge>
            {a.shadow_mode && <Badge status="watch">shadow</Badge>}
          </div>
          {a.operation_scenario && (
            <div className="mt-1 text-[13px] text-text-muted capitalize">
              {a.operation_scenario.replace(/_/g, ' ')}
            </div>
          )}
        </div>
        <button
          onClick={() => onApply(a)}
          disabled={applying}
          className="rounded text-[13px] font-medium"
          style={{ padding: '6px 16px', color: 'var(--bg-card)', background: 'var(--accent)', opacity: applying ? 0.6 : 1 }}
        >
          {applying ? 'Applying…' : 'Apply'}
        </button>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 text-[13px]" style={{ gap: 12 }}>
        {hasSetpoints && (
          <div>
            <div className="text-text-muted">Setpoint (heat / cool)</div>
            <div className="mt-0.5 text-text">
              <span className="text-text-faint">{cToF(a.baseline_heat_setpoint_c)}° / {cToF(a.baseline_cool_setpoint_c)}°</span>
              {' → '}
              <span className="font-medium">{cToF(a.recommended_heat_setpoint_c)}° / {cToF(a.recommended_cool_setpoint_c)}°F</span>
            </div>
          </div>
        )}
        {a.expected_cost_usd != null && (
          <div>
            <div className="text-text-muted">Expected cost</div>
            <div className="mt-0.5 text-text">${a.expected_cost_usd.toFixed(2)}</div>
          </div>
        )}
        {a.expected_energy_kwh != null && (
          <div>
            <div className="text-text-muted">Expected energy</div>
            <div className="mt-0.5 text-text">{a.expected_energy_kwh.toFixed(2)} kWh</div>
          </div>
        )}
      </div>
    </Card>
  )
}
