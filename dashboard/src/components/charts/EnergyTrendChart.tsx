import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import { EChart, themeColors } from './EChart'
import { useUIStore } from '../../store/ui'
import type { EnergyDay } from '../../lib/types'

// Daily home-load energy (bars, kWh) with the day's peak demand overlaid as a
// line on a second axis (kW).
export function EnergyTrendChart({ days }: { days: EnergyDay[] }) {
  const theme = useUIStore((s) => s.theme)

  const option = useMemo<EChartsOption>(() => {
    const c = themeColors()
    const dates = days.map((d) => d.date)
    return {
      tooltip: { trigger: 'axis' },
      legend: { data: ['Load', 'Solar', 'Peak demand'], textStyle: { color: c.muted }, top: 0 },
      grid: { left: 52, right: 52, top: 32, bottom: 28 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { color: c.muted },
        axisLine: { lineStyle: { color: c.border } },
      },
      yAxis: [
        {
          type: 'value',
          name: 'kWh',
          nameTextStyle: { color: c.muted },
          axisLabel: { color: c.muted },
          splitLine: { lineStyle: { color: c.border } },
        },
        {
          type: 'value',
          name: 'kW',
          nameTextStyle: { color: c.muted },
          axisLabel: { color: c.muted },
          splitLine: { show: false },
        },
      ],
      series: [
        { name: 'Load', type: 'bar', color: c.accent, data: days.map((d) => d.home_load_kwh) },
        { name: 'Solar', type: 'bar', color: c.ok, data: days.map((d) => d.solar_gen_kwh) },
        {
          name: 'Peak demand',
          type: 'line',
          yAxisIndex: 1,
          showSymbol: false,
          smooth: true,
          color: c.watch,
          data: days.map((d) => d.peak_demand_kw),
        },
      ],
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days, theme])

  return <EChart option={option} />
}
