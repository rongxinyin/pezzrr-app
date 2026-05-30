import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

// Reads the live design tokens so charts follow light/dark. Resolve at render
// time and pass the theme as a dep where this is used so options recompute.
export function themeColors() {
  const s = getComputedStyle(document.documentElement)
  const v = (name: string) => s.getPropertyValue(name).trim()
  return {
    text: v('--text'),
    muted: v('--text-muted'),
    border: v('--border'),
    accent: v('--accent'),
    ok: v('--ok'),
    info: v('--info'),
    watch: v('--watch'),
    act: v('--act'),
  }
}

export function EChart({ option, height = 280 }: { option: echarts.EChartsOption; height?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current)
    chartRef.current = chart
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, true)
    // a layout pass may have happened between init and option
    chartRef.current?.resize()
  }, [option])

  return <div ref={ref} style={{ width: '100%', height }} />
}
