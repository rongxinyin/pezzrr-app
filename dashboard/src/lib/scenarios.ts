import type { Status } from '../components/status'
import type { OperationScenario } from './types'

// The four operation scenarios the smart-home ILC resolves, in escalation
// order. Labels + status colors are shared by the Scenarios page, the calendar
// and the current-scenario card so a scenario always reads the same everywhere.
export const SCENARIOS: OperationScenario[] = [
  'normal',
  'load_peak_management',
  'capacity_management',
  'resiliency',
]

export const SCENARIO_LABEL: Record<OperationScenario, string> = {
  normal: 'Normal',
  load_peak_management: 'Load peak',
  capacity_management: 'Capacity',
  resiliency: 'Resiliency',
}

// Distinct colors across the status palette: normal=green, load peak=amber,
// capacity=red, resiliency=blue.
export const SCENARIO_STATUS: Record<OperationScenario, Status> = {
  normal: 'ok',
  load_peak_management: 'watch',
  capacity_management: 'act',
  resiliency: 'info',
}

export function scenarioLabel(s: string | null | undefined): string {
  if (!s) return '—'
  return SCENARIO_LABEL[s as OperationScenario] ?? s
}

export function scenarioStatus(s: string | null | undefined): Status {
  if (!s) return 'offline'
  return SCENARIO_STATUS[s as OperationScenario] ?? 'info'
}
