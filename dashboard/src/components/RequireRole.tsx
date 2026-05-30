import type { ReactNode } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import { useAuthStore } from '../store/auth'

// Route guard for role-restricted screens (§13.5 control = operator/admin).
// The API enforces RBAC regardless; this just keeps non-operators out of the
// UI. A blocked user is bounced back to the home detail they came from.
export function RequireRole({ allow, children }: { allow: string[]; children: ReactNode }) {
  const role = useAuthStore((s) => s.role)
  const { id } = useParams()
  if (role == null || !allow.includes(role)) {
    return <Navigate to={id ? `/homes/${id}` : '/'} replace />
  }
  return <>{children}</>
}
