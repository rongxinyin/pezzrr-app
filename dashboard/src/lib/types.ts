import type { Status } from '../components/status'

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
