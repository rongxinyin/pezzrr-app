import type { ReactNode } from 'react'

interface ConfirmDialogProps {
  open: boolean
  title: string
  body: ReactNode
  confirmLabel?: string
  busyLabel?: string
  danger?: boolean
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}

// Modal confirmation before a dispatch (§13.5). Backdrop click / Cancel
// dismisses; Confirm is disabled while the request is in flight.
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = 'Confirm',
  busyLabel = 'Dispatching…',
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.4)' }}
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md rounded-lg"
        style={{ background: 'var(--bg-card)', border: '0.5px solid var(--border)', padding: '20px 24px' }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-[16px] font-medium text-text">{title}</h2>
        <div className="mt-2 text-[14px] text-text-muted">{body}</div>
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={busy}
            className="rounded text-[13px] text-text-muted"
            style={{ border: '0.5px solid var(--border)', padding: '7px 16px', background: 'var(--bg-card)' }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            className="rounded text-[13px] font-medium"
            style={{
              padding: '7px 16px',
              color: 'var(--bg-card)',
              background: danger ? 'var(--act)' : 'var(--accent)',
              opacity: busy ? 0.6 : 1,
            }}
          >
            {busy ? busyLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
