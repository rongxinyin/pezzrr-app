import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import { EChart, themeColors } from './EChart'
import { useUIStore } from '../../store/ui'
import type { PanelLive } from '../../lib/types'

// Instantaneous power-flow Sankey from the latest panel snapshot.
// Grid/Solar/Battery feed Home; surplus flows Home -> Grid (export) or
// Home -> Battery (charging). Only one direction per source occurs at a time,
// so the graph stays acyclic.
export function PowerFlowSankey({ panel }: { panel: PanelLive | null }) {
  const theme = useUIStore((s) => s.theme)

  const option = useMemo<EChartsOption | null>(() => {
    if (!panel) return null
    const c = themeColors()
    const grid = panel.grid_power_w ?? 0
    const solar = panel.solar_power_w ?? 0
    const batt = panel.battery_power_w ?? 0 // + charging, - discharging

    const links: { source: string; target: string; value: number }[] = []
    if (grid > 0) links.push({ source: 'Grid', target: 'Home', value: grid })
    if (solar > 0) links.push({ source: 'Solar', target: 'Home', value: solar })
    if (batt < 0) links.push({ source: 'Battery', target: 'Home', value: -batt })
    if (grid < 0) links.push({ source: 'Home', target: 'Grid', value: -grid })
    if (batt > 0) links.push({ source: 'Home', target: 'Battery', value: batt })

    if (links.length === 0) return null

    return {
      tooltip: {
        trigger: 'item',
        formatter: (p) => {
          const params = p as { value?: number; name?: string }
          return params.value != null ? `${Math.round(params.value)} W` : (params.name ?? '')
        },
      },
      series: [
        {
          type: 'sankey',
          left: 8,
          right: 80,
          top: 8,
          bottom: 8,
          data: [
            { name: 'Grid', itemStyle: { color: c.info } },
            { name: 'Solar', itemStyle: { color: c.ok } },
            { name: 'Battery', itemStyle: { color: c.watch } },
            { name: 'Home', itemStyle: { color: c.accent } },
          ],
          links,
          label: { color: c.text },
          lineStyle: { color: 'gradient', opacity: 0.4 },
        },
      ],
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panel, theme])

  if (!option) return <div className="text-[13px] text-text-muted">No power flow to display.</div>
  return <EChart option={option} height={220} />
}
