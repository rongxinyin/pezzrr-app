import { NavLink } from 'react-router-dom'
import { useAuthStore } from '../store/auth'

interface NavItem {
  label: string
  to: string
  enabled: boolean
  roles?: string[] // omitted = all roles
  group?: string // section header this item sits under
}

// Fleet-level destinations (§13). Home-scoped screens (Energy, Control) are
// reached from the home detail page, not this global nav. Routes that aren't
// built yet render as disabled placeholders so the layout is final now.
const NAV: NavItem[] = [
  { label: 'Fleet', to: '/', enabled: true },
  { label: 'Scenarios', to: '/scenarios', enabled: true, group: 'Operation' },
  { label: 'Health', to: '/health', enabled: true },
  { label: 'Reports', to: '/reports', enabled: true, roles: ['fleet_analyst', 'admin'] },
  { label: 'Admin', to: '/admin', enabled: true, roles: ['admin'] },
]

function NavRow({ item }: { item: NavItem }) {
  const indent = item.group ? 20 : 12
  if (!item.enabled) {
    return (
      <span
        className="flex items-center justify-between rounded text-[14px] text-text-faint"
        style={{ padding: `8px 12px 8px ${indent}px`, cursor: 'default' }}
        title="Coming soon"
      >
        {item.label}
        <span className="text-[11px] text-text-faint">soon</span>
      </span>
    )
  }
  return (
    <NavLink
      to={item.to}
      end={item.to === '/'}
      className="rounded text-[14px]"
      style={({ isActive }) => ({
        padding: `8px 12px 8px ${indent}px`,
        color: isActive ? 'var(--accent)' : 'var(--text-muted)',
        background: isActive ? 'var(--accent-soft)' : 'transparent',
        fontWeight: isActive ? 500 : 400,
      })}
    >
      {item.label}
    </NavLink>
  )
}

export function Sidebar() {
  const role = useAuthStore((s) => s.role)
  const items = NAV.filter((i) => !i.roles || (role != null && i.roles.includes(role)))

  // Render items in order, emitting a section header the first time a new
  // group is seen so grouped items sit visually beneath their label.
  const seenGroups = new Set<string>()

  return (
    <nav
      className="flex w-52 shrink-0 flex-col gap-1 px-3 py-4"
      style={{ borderRight: '0.5px solid var(--border)', background: 'var(--bg-card)' }}
    >
      {items.map((item) => {
        const header =
          item.group && !seenGroups.has(item.group) ? item.group : null
        if (item.group) seenGroups.add(item.group)
        return (
          <div key={item.to} className="flex flex-col gap-1">
            {header && (
              <div
                className="px-3 pt-2 text-[11px] uppercase tracking-wide text-text-faint"
                style={{ letterSpacing: '0.05em' }}
              >
                {header}
              </div>
            )}
            <NavRow item={item} />
          </div>
        )
      })}
    </nav>
  )
}
