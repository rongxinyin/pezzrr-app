import type { Status } from '../components/status'

export interface PanelLive {
  ts: string | null
  home_load_w: number | null
  grid_power_w: number | null
  solar_power_w: number | null
  battery_power_w: number | null
  battery_soc_pct: number | null
  grid_status: number | null
  eps_mode_active: boolean | null
}

export interface BatteryLive {
  ts: string | null
  soc_pct: number | null
  soh_pct: number | null
  status: string | null
  power_w: number | null
  capacity_wh: number | null
  usable_kwh: number | null
}

export interface ThermostatLive {
  ts: string | null
  indoor_temp_c: number | null
  indoor_humidity_pct: number | null
  hvac_mode: string | null
  hvac_state: string | null
  heat_setpoint_c: number | null
  cool_setpoint_c: number | null
}

export interface PriceLive {
  price_per_kwh: number | null
  period_type: string | null
  program_name: string | null
  interval_start: string | null
  interval_end: string | null
}

export interface CircuitLive {
  circuit_id: number
  channel_num: number
  circuit_name: string | null
  is_critical: boolean
  is_controllable: boolean
  circuit_priority: 'critical' | 'essential' | 'non_essential' | null
  power_w: number | null
  is_enabled: boolean | null
  ts: string | null
}

export interface ActionLive {
  action_id: number
  ts: string | null
  action_type: string
  success: boolean | null
  acknowledged_at: string | null
}

export interface LiveSnapshot {
  home_id: number
  panel: PanelLive | null
  battery: BatteryLive | null
  thermostat: ThermostatLive | null
  price: PriceLive | null
  circuits: CircuitLive[]
  latest_action: ActionLive | null
}

export interface PanelPoint {
  bucket: string
  home_load_w: number | null
  grid_power_w: number | null
  solar_power_w: number | null
  battery_soc_pct: number | null
  battery_power_w?: number | null
  peak_load_w?: number | null
}

export interface PanelEnvelope {
  home_id: number
  bucket: string
  from: string
  to: string
  count: number
  points: PanelPoint[]
}

export interface ThermostatPoint {
  bucket: string
  indoor_temp_c: number | null
  outdoor_temp_c: number | null
  indoor_humidity_pct: number | null
  heat_setpoint_c: number | null
  cool_setpoint_c: number | null
}

export interface ThermostatEnvelope {
  home_id: number
  bucket: string
  from: string
  to: string
  count: number
  points: ThermostatPoint[]
}

// =====================================================================
// Control (dispatch + audit) — matches api/models.py
// =====================================================================
export type ActionStatus = 'pending' | 'acknowledged' | 'success' | 'failed'

export interface ControlAction {
  action_id: number
  home_id: number
  device_id: number | null
  circuit_id: number | null
  event_id: number | null
  ts: string
  action_type: string
  triggered_by: string
  status: ActionStatus
  success: boolean | null
  acknowledged_at: string | null
  error_msg: string | null
}

export interface ControlAdvisory {
  advisory_id: number
  home_id: number
  device_id: number | null
  circuit_id: number | null
  event_id: number | null
  ts: string
  controller: string
  action_type: string
  triggered_by: string
  operation_scenario: string | null
  shadow_mode: boolean
  baseline_cool_setpoint_c: number | null
  baseline_heat_setpoint_c: number | null
  recommended_cool_setpoint_c: number | null
  recommended_heat_setpoint_c: number | null
  expected_cost_usd: number | null
  expected_energy_kwh: number | null
}

export interface PanelMode {
  home_id: number
  device_id: number
  smartBackupMode: number | null
  epsModeInfo: boolean | null
  backupReserveSoc: number | null
  chargeWattPower: number | null
  foceChargeHight: number | null
}

export type DispatchKind = 'circuit' | 'thermostat' | 'plug' | 'demand_limit' | 'battery_mode'

export interface DispatchRequest {
  home_id: number
  action_type: string
  target: { kind: DispatchKind; circuit_id?: number | null; device_id?: number | null }
  params?: Record<string, unknown>
  event_id?: number | null
}

export interface DispatchResponse {
  action_id: number
  status: string
}

export type SetpointController = 'baseline' | 'rbc' | 'mpc'

export interface SetpointPlanPoint {
  ts: string
  cool_setpoint_c: number | null
  heat_setpoint_c: number | null
  predicted_indoor_temp_c: number | null
  indoor_temp_c: number | null
}

export interface ForecastPoint {
  ts: string
  outdoor_temp_c: number | null
}

export interface SetpointPlan {
  home_id: number
  controller: SetpointController
  mode: string
  start: string
  dt_s: number
  available: boolean
  note: string | null
  immediate_cool_setpoint_c: number | null
  immediate_heat_setpoint_c: number | null
  points: SetpointPlanPoint[]
  forecast: ForecastPoint[]
}

// =====================================================================
// Demand response (§13.4) — matches api/models.py
// =====================================================================
export interface DrEvent {
  event_id: number
  event_reference: string | null
  ven_id: string | null
  vtn_id: string | null
  signal_name: string | null
  signal_type: string | null
  signal_level: number | null
  target_load_kw: number | null
  event_start: string
  event_end: string
  status: string
  priority: number | null
  test_event: boolean
  active: boolean
  participant_count: number
}

export interface DrParticipant {
  id: number
  event_id: number
  home_id: number
  home_name: string | null
  opted_in: boolean
  baseline_kw: number | null
  actual_reduction_kw: number | null
  reduction_target_kw: number | null
  settlement_kwh: number | null
  performance_score: number | null
  notes: string | null
}

export interface OpenAdrPrice {
  ts: string | null
  program_name: string | null
  period_type: string | null
  price_per_kwh: number | null
  interval_start: string | null
  interval_end: string | null
}

export interface OpenAdrPricePoint {
  interval_start: string
  interval_end: string
  period_type: string | null
  price_per_kwh: number
}

// =====================================================================
// Energy analytics (§13.3) — matches api/models.py
// =====================================================================
export interface EnergyDay {
  date: string
  home_load_kwh: number | null
  solar_gen_kwh: number | null
  grid_import_kwh: number | null
  grid_export_kwh: number | null
  peak_demand_kw: number | null
  peak_demand_at: string | null
  self_consumption_pct: number | null
  estimated_cost_usd: number | null
}

export interface CircuitEnergy {
  circuit_id: number
  channel_num: number | null
  circuit_name: string | null
  energy_kwh: number | null
}

export interface EnergyTotals {
  home_load_kwh: number | null
  solar_gen_kwh: number | null
  grid_import_kwh: number | null
  grid_export_kwh: number | null
  peak_demand_kw: number | null
  self_consumption_pct: number | null
  estimated_cost_usd: number | null
}

export interface EnergyAnalytics {
  home_id: number
  home_name: string | null
  start: string
  end: string
  days: EnergyDay[]
  circuits: CircuitEnergy[]
  totals: EnergyTotals
}

// =====================================================================
// Device health (§13.6) — matches api/models.py
// =====================================================================
export interface DeviceHealth {
  device_id: number
  home_id: number
  home_name: string | null
  device_type: string
  device_name: string | null
  manufacturer: string | null
  model: string | null
  firmware_version: string | null
  is_online: boolean | null
  online_updated_at: string | null
  is_active: boolean
}

export interface CoverageRow {
  device_id: number
  device_type: string
  device_name: string | null
  reading_count: number
  present_buckets: number
  expected_buckets: number
  coverage_pct: number | null
  last_reading_at: string | null
}

export interface CoverageReport {
  home_id: number
  home_name: string | null
  date: string
  devices: CoverageRow[]
}

// =====================================================================
// Admin — user management (§13.8) — matches api/models.py
// =====================================================================
export interface AdminUser {
  user_id: number
  username: string
  role: string
  is_active: boolean
  created_at: string | null
  homes: number[]
}

export interface FleetStatusItem {
  home_id: number
  home_name: string
  city: string | null
  status: Status
  gateway_online: boolean
  enrolled_dr: boolean
  dr_active: boolean
  home_load_w: number | null
  grid_power_w: number | null
  solar_power_w: number | null
  battery_soc_pct: number | null
  panel_ts: string | null
}
