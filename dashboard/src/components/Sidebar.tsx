import { NavLink } from 'react-router-dom'
import { useAuthStore } from '../store/auth'

interface NavItem {
  label: string
  to: string
  enabled: boolean
  roles?: string[] // omitted = all roles
}

// Fleet-level destinations (§13). Home-scoped screens (Energy, Control) are
// reached from the home detail page, not this global nav. Routes that aren't
// built yet render as disabled placeholders so the layout is final now.
const NAV: NavItem[] = [
  { label: 'Fleet', to: '/', enabled: true },
  { label: 'Demand response', to: '/dr', enabled: true },
  { label: 'Health', to: '/health', enabled: false },
  { label: 'Reports', to: '/reports', enabled: false, roles: ['fleet_analyst', 'admin'] },
  { label: 'Admin', to: '/admin', enabled: false, roles: ['admin'] },
]

export function Sidebar() {
  const role = useAuthStore((s) => s.role)
  const items = NAV.filter((i) => !i.roles || (role != null && i.roles.includes(role)))

  return (
    <nav
      className="flex w-52 shrink-0 flex-col gap-1 px-3 py-4"
      style={{ borderRight: '0.5px solid var(--border)', background: 'var(--bg-card)' }}
    >
      {items.map((item) =>
        item.enabled ? (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className="rounded text-[14px]"
            style={({ isActive }) => ({
              padding: '8px 12px',
              color: isActive ? 'var(--accent)' : 'var(--text-muted)',
              background: isActive ? 'var(--accent-soft)' : 'transparent',
              fontWeight: isActive ? 500 : 400,
            })}
          >
            {item.label}
          </NavLink>
        ) : (
          <span
            key={item.to}
            className="flex items-center justify-between rounded text-[14px] text-text-faint"
            style={{ padding: '8px 12px', cursor: 'default' }}
            title="Coming soon"
          >
            {item.label}
            <span className="text-[11px] text-text-faint">soon</span>
          </span>
        ),
      )}
    </nav>
  )
}
