import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import { EChart, themeColors } from './EChart'
import { useUIStore } from '../../store/ui'
import type { ForecastPoint, SetpointPlanPoint } from '../../lib/types'

const cToF = (c: number | null) => (c == null ? null : (c * 9) / 5 + 32)

// Setpoint plan for the selected controller: cooling/heating setpoints and
// predicted indoor temp on the left °F axis, outdoor-air temp on the right °F
// axis. baseline/rbc span the current day; mpc spans its forward horizon.
export function SetpointPlanChart({
  points,
  forecast,
}: {
  points: SetpointPlanPoint[]
  forecast: ForecastPoint[]
}) {
  const theme = useUIStore((s) => s.theme)

  const option = useMemo<EChartsOption>(() => {
    const c = themeColors()
    const sp = (key: keyof SetpointPlanPoint) =>
      points.map((p) => [p.ts, cToF(p[key] as number | null)])
    const oat = forecast.map((p) => [p.ts, cToF(p.outdoor_temp_c)])

    const hasHeat = points.some((p) => p.heat_setpoint_c != null)
    const hasPred = points.some((p) => p.predicted_indoor_temp_c != null)
    const hasIndoor = points.some((p) => p.indoor_temp_c != null)

    const legend = ['Cool setpoint']
    if (hasHeat) legend.push('Heat setpoint')
    if (hasIndoor) legend.push('Indoor (actual)')
    if (hasPred) legend.push('Predicted indoor')
    legend.push('Outdoor (forecast)')

    const series: EChartsOption['series'] = [
      {
        name: 'Cool setpoint',
        type: 'line',
        showSymbol: false,
        step: 'end',
        color: c.info,
        lineStyle: { type: 'dashed' },
        data: sp('cool_setpoint_c'),
      },
    ]
    if (hasHeat)
      series.push({
        name: 'Heat setpoint',
        type: 'line',
        showSymbol: false,
        step: 'end',
        color: c.watch,
        lineStyle: { type: 'dashed' },
        data: sp('heat_setpoint_c'),
      })
    if (hasIndoor)
      series.push({
        name: 'Indoor (actual)',
        type: 'line',
        showSymbol: false,
        smooth: true,
        color: c.accent,
        lineStyle: { width: 2.5 },
        data: sp('indoor_temp_c'),
      })
    if (hasPred)
      series.push({
        name: 'Predicted indoor',
        type: 'line',
        showSymbol: false,
        smooth: true,
        color: c.accent,
        lineStyle: { type: 'dashed', opacity: 0.8 },
        data: sp('predicted_indoor_temp_c'),
      })
    series.push({
      name: 'Outdoor (forecast)',
      type: 'line',
      yAxisIndex: 1,
      showSymbol: false,
      smooth: true,
      color: c.act,
      lineStyle: { opacity: 0.7 },
      data: oat,
    })

    return {
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v) => (v == null ? '—' : `${(+v).toFixed(1)}°F`),
      },
      legend: { data: legend, textStyle: { color: c.muted }, top: 0 },
      grid: { left: 44, right: 48, top: 32, bottom: 28 },
      xAxis: {
        type: 'time',
        axisLabel: { color: c.muted },
        axisLine: { lineStyle: { color: c.border } },
      },
      yAxis: [
        {
          type: 'value',
          name: 'Setpoint °F',
          scale: true,
          nameTextStyle: { color: c.muted },
          axisLabel: { color: c.muted },
          splitLine: { lineStyle: { color: c.border } },
        },
        {
          type: 'value',
          name: 'OAT °F',
          scale: true,
          position: 'right',
          nameTextStyle: { color: c.muted },
          axisLabel: { color: c.muted },
          splitLine: { show: false },
        },
      ],
      series,
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, forecast, theme])

  return <EChart option={option} height={240} />
}
