import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import type { FleetStatusItem } from '../lib/types'
import { HomeCard } from '../components/HomeCard'
import { MetricTile } from '../components/MetricTile'

export function Fleet() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['fleet-status'],
    queryFn: () => apiFetch<FleetStatusItem[]>('/fleet/status'),
    refetchInterval: 30_000,
  })

  if (isLoading) return <div className="text-text-muted">Loading fleet…</div>
  if (error) return <div className="text-act">Failed to load fleet status.</div>

  const homes = data ?? []
  const online = homes.filter((h) => h.gateway_online).length
  const load = homes.reduce((sum, h) => sum + (h.home_load_w ?? 0), 0) / 1000
  const drActive = homes.filter((h) => h.dr_active).length
  const socs = homes.map((h) => h.battery_soc_pct).filter((v): v is number => v != null)
  const avgSoc = socs.length ? socs.reduce((a, b) => a + b, 0) / socs.length : null

  return (
    <div>
      <h1 className="mb-4 text-[22px] font-medium text-text">Fleet overview</h1>

      <div className="mb-6 grid grid-cols-2 sm:grid-cols-4" style={{ gap: 12 }}>
        <MetricTile label="Homes online" value={`${online}/${homes.length}`} />
        <MetricTile label="Fleet load" value={load.toFixed(1)} unit="kW" />
        <MetricTile label="Active DR" value={drActive} />
        <MetricTile label="Avg SoC" value={avgSoc == null ? '—' : avgSoc.toFixed(0)} unit={avgSoc == null ? undefined : '%'} />
      </div>

      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
        {homes.map((h) => (
          <HomeCard key={h.home_id} home={h} />
        ))}
      </div>
    </div>
  )
}
