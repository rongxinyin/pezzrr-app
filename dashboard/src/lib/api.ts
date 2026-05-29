import { API_BASE } from './config'
import { useAuthStore } from '../store/auth'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

// Authenticated JSON fetch. Attaches the bearer token, clears it on 401,
// and surfaces the API's {detail} message on errors.
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = useAuthStore.getState().token
  const headers = new Headers(init?.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init?.body) headers.set('Content-Type', 'application/json')

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })

  if (res.status === 401) {
    useAuthStore.getState().logout()
    throw new ApiError(401, 'Unauthorized')
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}
