import type { Status } from '../components/status'
import type { OperationScenario } from './types'

// The six operation scenarios the smart-home ILC resolves, in escalation
// order. Labels + status colors are shared by the Scenarios page, the calendar
// and the current-scenario card so a scenario always reads the same everywhere.
export const SCENARIOS: OperationScenario[] = [
  'normal',
  'load_management_tou',
  'load_management_dr',
  'load_management_capacity',
  'capacity_management',
  'resiliency',
]

export const SCENARIO_LABEL: Record<OperationScenario, string> = {
  normal: 'Normal',
  load_management_tou: 'TOU peak',
  load_management_dr: 'DR event',
  load_management_capacity: 'Capacity',
  capacity_management: 'Islanded',
  resiliency: 'Resiliency',
}

// Status palette (only four colors): normal=green; the two load-shift
// scenarios (TOU, DR)=amber; the two capacity scenarios=red; resiliency=blue.
export const SCENARIO_STATUS: Record<OperationScenario, Status> = {
  normal: 'ok',
  load_management_tou: 'watch',
  load_management_dr: 'watch',
  load_management_capacity: 'act',
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
