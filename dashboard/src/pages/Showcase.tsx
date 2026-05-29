import { useEffect } from 'react'
import { Card } from '../components/Card'
import { MetricTile, MetricGrid } from '../components/MetricTile'
import { StatusRing } from '../components/StatusRing'
import { Badge } from '../components/Badge'
import type { Status } from '../components/status'
import { useUIStore } from '../store/ui'

const STATUSES: Status[] = ['ok', 'watch', 'act', 'info', 'offline']

export function Showcase() {
  const theme = useUIStore((s) => s.theme)
  const toggleTheme = useUIStore((s) => s.toggleTheme)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  return (
    <div className="min-h-full bg-page p-8">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-[22px] font-medium text-text">Design system</h1>
            <p className="text-text-muted">Card, metric tile, status ring, badge — Airthings style.</p>
          </div>
          <button
            onClick={toggleTheme}
            className="rounded text-[14px] text-accent"
            style={{ border: '0.5px solid var(--border)', padding: '6px 14px', background: 'var(--bg-card)' }}
          >
            {theme === 'light' ? 'Switch to dark' : 'Switch to light'}
          </button>
        </header>

        <div className="grid grid-cols-2" style={{ gap: 16 }}>
          <Card title="Battery" shadow>
            <div className="flex items-center gap-6">
              <StatusRing value={78} status="ok" label="state of charge" />
              <div className="text-text-muted text-[14px]">
                Healthy charge. Usable 9.4 kWh.
              </div>
            </div>
          </Card>

          <Card title="Status rings">
            <div className="flex flex-wrap gap-4">
              <StatusRing value={88} status="ok" label="ok" />
              <StatusRing value={42} status="watch" label="watch" />
              <StatusRing value={15} status="act" label="act" />
              <StatusRing value={60} status="info" label="info" />
              <StatusRing value={0} status="offline" label="offline" />
            </div>
          </Card>

          <Card title="Power flow">
            <MetricGrid cols={4}>
              <MetricTile label="Grid" value="2.1" unit="kW" />
              <MetricTile label="Solar" value="3.4" unit="kW" />
              <MetricTile label="Battery" value="-0.8" unit="kW" />
              <MetricTile label="Home load" value="4.7" unit="kW" />
            </MetricGrid>
          </Card>

          <Card title="Badges">
            <div className="flex flex-wrap items-center gap-2">
              {STATUSES.map((s) => (
                <Badge key={s} status={s}>
                  {s}
                </Badge>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}
