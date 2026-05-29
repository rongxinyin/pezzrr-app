import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import { EChart, themeColors } from './EChart'
import { useUIStore } from '../../store/ui'
import type { PanelPoint } from '../../lib/types'

// 24h panel series: home load (filled), grid, solar — in kW.
export function PowerAreaChart({ points }: { points: PanelPoint[] }) {
  const theme = useUIStore((s) => s.theme)

  const option = useMemo<EChartsOption>(() => {
    const c = themeColors()
    const kw = (key: keyof PanelPoint) =>
      points.map((p) => [p.bucket, p[key] == null ? null : (p[key] as number) / 1000])

    return {
      tooltip: { trigger: 'axis', valueFormatter: (v) => (v == null ? '—' : `${(+v).toFixed(2)} kW`) },
      legend: { data: ['Home load', 'Grid', 'Solar'], textStyle: { color: c.muted }, top: 0 },
      grid: { left: 48, right: 16, top: 32, bottom: 28 },
      xAxis: {
        type: 'time',
        axisLabel: { color: c.muted },
        axisLine: { lineStyle: { color: c.border } },
      },
      yAxis: {
        type: 'value',
        name: 'kW',
        nameTextStyle: { color: c.muted },
        axisLabel: { color: c.muted },
        splitLine: { lineStyle: { color: c.border } },
      },
      series: [
        {
          name: 'Home load',
          type: 'line',
          showSymbol: false,
          smooth: true,
          areaStyle: { opacity: 0.15 },
          color: c.accent,
          data: kw('home_load_w'),
        },
        { name: 'Grid', type: 'line', showSymbol: false, smooth: true, color: c.info, data: kw('grid_power_w') },
        { name: 'Solar', type: 'line', showSymbol: false, smooth: true, color: c.ok, data: kw('solar_power_w') },
      ],
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, theme])

  return <EChart option={option} />
}
