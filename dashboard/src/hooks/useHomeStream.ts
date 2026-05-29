import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { API_BASE } from '../lib/config'
import { useAuthStore } from '../store/auth'
import type { LiveSnapshot } from '../lib/types'

// Seeds from GET /homes/{id}/live, then subscribes to the SSE stream and
// writes each pushed snapshot into the same TanStack Query cache key, so the
// page re-renders live without refetching. EventSource auto-reconnects on drop.
export function useHomeStream(homeId: number) {
  const qc = useQueryClient()
  const token = useAuthStore((s) => s.token)
  const key = ['home-live', homeId] as const

  const query = useQuery({
    queryKey: key,
    queryFn: () => apiFetch<LiveSnapshot>(`/homes/${homeId}/live`),
  })

  useEffect(() => {
    if (!token) return
    const url = `${API_BASE}/stream/homes/${homeId}?token=${encodeURIComponent(token)}`
    const es = new EventSource(url)
    es.onmessage = (e) => {
      try {
        qc.setQueryData(key, JSON.parse(e.data) as LiveSnapshot)
      } catch {
        /* ignore malformed frame */
      }
    }
    return () => es.close()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [homeId, token])

  return query
}
