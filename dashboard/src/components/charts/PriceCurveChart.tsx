import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import { EChart, themeColors } from './EChart'
import { useUIStore } from '../../store/ui'
import type { OpenAdrPricePoint } from '../../lib/types'

// OpenADR price curve ($/kWh) stepped over interval windows, peak intervals
// tinted to stand out from off-peak.
export function PriceCurveChart({ points }: { points: OpenAdrPricePoint[] }) {
  const theme = useUIStore((s) => s.theme)

  const option = useMemo<EChartsOption>(() => {
    const c = themeColors()
    const data = points.map((p) => ({
      value: [p.interval_start, p.price_per_kwh],
      itemStyle: { color: p.period_type === 'peak' ? c.watch : c.accent },
    }))

    return {
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v) => (v == null ? '—' : `$${(+v).toFixed(3)}/kWh`),
      },
      grid: { left: 56, right: 16, top: 16, bottom: 28 },
      xAxis: {
        type: 'time',
        axisLabel: { color: c.muted },
        axisLine: { lineStyle: { color: c.border } },
      },
      yAxis: {
        type: 'value',
        name: '$/kWh',
        nameTextStyle: { color: c.muted },
        axisLabel: { color: c.muted, formatter: (v: number) => `$${v.toFixed(2)}` },
        splitLine: { lineStyle: { color: c.border } },
      },
      series: [
        {
          name: 'Price',
          type: 'line',
          step: 'end',
          showSymbol: false,
          color: c.accent,
          areaStyle: { opacity: 0.12 },
          data,
        },
      ],
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, theme])

  return <EChart option={option} />
}
