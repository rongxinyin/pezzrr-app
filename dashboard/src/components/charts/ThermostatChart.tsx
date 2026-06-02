import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import { EChart, themeColors } from './EChart'
import { useUIStore } from '../../store/ui'
import type { ThermostatPoint } from '../../lib/types'

const cToF = (c: number | null) => (c == null ? null : (c * 9) / 5 + 32)

// Indoor temp with the heat/cool setpoint bands, all in °F.
export function ThermostatChart({ points }: { points: ThermostatPoint[] }) {
  const theme = useUIStore((s) => s.theme)

  const option = useMemo<EChartsOption>(() => {
    const c = themeColors()
    const f = (key: keyof ThermostatPoint) =>
      points.map((p) => [p.bucket, cToF(p[key] as number | null)])

    return {
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v) => (v == null ? '—' : `${(+v).toFixed(1)}°F`),
      },
      legend: { data: ['Indoor', 'Heat', 'Cool'], textStyle: { color: c.muted }, top: 0 },
      grid: { left: 44, right: 16, top: 32, bottom: 28 },
      xAxis: {
        type: 'time',
        axisLabel: { color: c.muted },
        axisLine: { lineStyle: { color: c.border } },
      },
      yAxis: {
        type: 'value',
        name: '°F',
        scale: true,
        nameTextStyle: { color: c.muted },
        axisLabel: { color: c.muted },
        splitLine: { lineStyle: { color: c.border } },
      },
      series: [
        { name: 'Indoor', type: 'line', showSymbol: false, smooth: true, color: c.accent, data: f('indoor_temp_c') },
        {
          name: 'Heat',
          type: 'line',
          showSymbol: false,
          step: 'end',
          color: c.watch,
          lineStyle: { type: 'dashed' },
          data: f('heat_setpoint_c'),
        },
        {
          name: 'Cool',
          type: 'line',
          showSymbol: false,
          step: 'end',
          color: c.info,
          lineStyle: { type: 'dashed' },
          data: f('cool_setpoint_c'),
        },
      ],
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, theme])

  return <EChart option={option} height={220} />
}
