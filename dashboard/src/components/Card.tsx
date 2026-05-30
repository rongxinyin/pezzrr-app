import type { ReactNode } from 'react'

interface CardProps {
  title?: ReactNode
  action?: ReactNode
  shadow?: boolean
  className?: string
  children: ReactNode
}

// §12: --bg-card, 0.5px border, --radius-lg, padding 16px 20px, optional --shadow.
export function Card({ title, action, shadow = false, className = '', children }: CardProps) {
  return (
    <section
      className={`bg-card rounded-lg ${className}`}
      style={{
        border: '0.5px solid var(--border)',
        padding: '16px 20px',
        boxShadow: shadow ? 'var(--shadow)' : undefined,
      }}
    >
      {(title || action) && (
        <header className="flex items-center justify-between mb-3">
          {title && <h2 className="text-[15px] font-medium text-text">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  )
}
