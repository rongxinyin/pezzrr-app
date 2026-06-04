import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import { EChart, themeColors } from './EChart'
import { useUIStore } from '../../store/ui'
import type { OpenAdrPricePoint } from '../../lib/types'

// OpenADR price curve ($/kWh) stepped over interval windows, peak intervals
// tinted to stand out from off-peak. When `min`/`max` are given the time axis
// is pinned to that window (e.g. the current local day) so the full span shows
// even when price data only covers part of it.
export function PriceCurveChart({
  points,
  min,
  max,
}: {
  points: OpenAdrPricePoint[]
  min?: string
  max?: string
}) {
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
        min,
        max,
        axisLabel: {
          color: c.muted,
          formatter: { hour: '{HH}:{mm}', minute: '{HH}:{mm}' },
        },
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
  }, [points, theme, min, max])

  return <EChart option={option} />
}
