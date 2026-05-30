import type { ReactNode } from 'react'
import type { Status } from './status'
import { STATUS_COLORS } from './status'

interface BadgeProps {
  status?: Status
  children: ReactNode
}

// §12: 12px, 4px 10px, --radius, status bg + matching dark text from the
// same family (never plain black/gray).
export function Badge({ status = 'info', children }: BadgeProps) {
  const { fg, bg } = STATUS_COLORS[status]
  return (
    <span
      className="inline-flex items-center rounded font-medium"
      style={{ fontSize: 12, padding: '4px 10px', color: fg, backgroundColor: bg }}
    >
      {children}
    </span>
  )
}
