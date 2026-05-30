import type { Status } from './status'
import { STATUS_COLORS } from './status'

interface StatusRingProps {
  value: number // 0–100
  status?: Status
  label?: string
  size?: number
}

const R = 26
const STROKE = 6
const CIRC = 2 * Math.PI * R

// §12: SVG donut, r=26, stroke-width=6, rounded cap,
// dashoffset = (1 − value/100) × 2πr. Center text = value.
export function StatusRing({ value, status = 'ok', label, size = 64 }: StatusRingProps) {
  const clamped = Math.max(0, Math.min(100, value))
  const offset = (1 - clamped / 100) * CIRC
  const color = STATUS_COLORS[status].fg
  const box = 2 * (R + STROKE / 2)

  return (
    <div className="inline-flex flex-col items-center" style={{ width: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${box} ${box}`}>
        <g transform={`rotate(-90 ${box / 2} ${box / 2})`}>
          <circle cx={box / 2} cy={box / 2} r={R} fill="none" stroke="var(--bg-subtle)" strokeWidth={STROKE} />
          <circle
            cx={box / 2}
            cy={box / 2}
            r={R}
            fill="none"
            stroke={color}
            strokeWidth={STROKE}
            strokeLinecap="round"
            strokeDasharray={CIRC}
            strokeDashoffset={offset}
          />
        </g>
        <text
          x="50%"
          y="50%"
          dominantBaseline="central"
          textAnchor="middle"
          style={{ fontSize: 15, fontWeight: 500, fill: 'var(--text)' }}
        >
          {Math.round(clamped)}
        </text>
      </svg>
      {label && <div className="mt-1 text-[12px] text-text-muted">{label}</div>}
    </div>
  )
}
