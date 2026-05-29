import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import { EChart, themeColors } from './EChart'
import { useUIStore } from '../../store/ui'
import type { CircuitEnergy } from '../../lib/types'

// Horizontal bar ranking of circuit energy (kWh) over the selected range.
export function CircuitRankChart({ circuits }: { circuits: CircuitEnergy[] }) {
  const theme = useUIStore((s) => s.theme)
  const top = circuits.slice(0, 10)

  const option = useMemo<EChartsOption>(() => {
    const c = themeColors()
    const labels = top.map((x) => x.circuit_name ?? `Channel ${x.channel_num}`)
    return {
      tooltip: { trigger: 'axis', valueFormatter: (v) => (v == null ? '—' : `${(+v).toFixed(1)} kWh`) },
      grid: { left: 8, right: 24, top: 8, bottom: 24, containLabel: true },
      xAxis: {
        type: 'value',
        name: 'kWh',
        nameTextStyle: { color: c.muted },
        axisLabel: { color: c.muted },
        splitLine: { lineStyle: { color: c.border } },
      },
      yAxis: {
        type: 'category',
        inverse: true,
        data: labels,
        axisLabel: { color: c.muted },
        axisLine: { lineStyle: { color: c.border } },
      },
      series: [
        {
          type: 'bar',
          color: c.accent,
          data: top.map((x) => x.energy_kwh),
          barMaxWidth: 18,
        },
      ],
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [circuits, theme])

  return <EChart option={option} height={Math.max(160, top.length * 30 + 40)} />
}
