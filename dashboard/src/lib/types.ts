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
