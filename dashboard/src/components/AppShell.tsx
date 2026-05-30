import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useAuthStore } from '../store/auth'
import { useUIStore } from '../store/ui'
import { Sidebar } from './Sidebar'

export function AppShell({ children }: { children: ReactNode }) {
  const role = useAuthStore((s) => s.role)
  const logout = useAuthStore((s) => s.logout)
  const theme = useUIStore((s) => s.theme)
  const toggleTheme = useUIStore((s) => s.toggleTheme)

  return (
    <div className="flex min-h-full flex-col bg-page">
      <header
        className="flex items-center justify-between px-6 py-4"
        style={{ borderBottom: '0.5px solid var(--border)', background: 'var(--bg-card)' }}
      >
        <Link to="/" className="text-[16px] font-semibold text-text">
          Pezzrr <span className="font-normal text-text-muted">Fleet</span>
        </Link>
        <div className="flex items-center gap-3">
          {role && <span className="text-[13px] text-text-muted capitalize">{role}</span>}
          <button
            onClick={toggleTheme}
            className="rounded text-[13px] text-accent"
            style={{ border: '0.5px solid var(--border)', padding: '5px 12px', background: 'var(--bg-card)' }}
          >
            {theme === 'light' ? 'Dark' : 'Light'}
          </button>
          <button
            onClick={logout}
            className="rounded text-[13px] text-text-muted"
            style={{ border: '0.5px solid var(--border)', padding: '5px 12px', background: 'var(--bg-card)' }}
          >
            Sign out
          </button>
        </div>
      </header>
      <div className="flex flex-1">
        <Sidebar />
        <main className="flex-1 p-8">
          <div className="mx-auto max-w-6xl">{children}</div>
        </main>
      </div>
    </div>
  )
}
