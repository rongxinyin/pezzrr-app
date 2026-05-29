"""Pydantic response schemas for the dashboard API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    homes: list[int]


class MeResponse(BaseModel):
    user_id: int
    role: str
    homes: list[int]


class HomeSummaryItem(BaseModel):
    home_id: int
    home_name: str
    city: Optional[str] = None
    state: Optional[str] = None
    timezone: str
    enrolled_dr: bool
    gateway_id: Optional[str] = None
    gateway_online: bool


class Device(BaseModel):
    device_id: int
    device_type: str
    device_name: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    is_online: Optional[bool] = None
    online_updated_at: Optional[datetime] = None


class PanelSnapshot(BaseModel):
    ts: datetime
    home_load_w: Optional[float] = None
    grid_power_w: Optional[float] = None
    solar_power_w: Optional[float] = None
    battery_power_w: Optional[float] = None
    battery_soc_pct: Optional[float] = None
    grid_status: Optional[str] = None
    eps_mode_active: Optional[bool] = None


class StatusSnapshot(BaseModel):
    panel: Optional[PanelSnapshot] = None
    battery_soc_pct: Optional[float] = None


class HomeDetail(BaseModel):
    home_id: int
    home_name: str
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    utility_id: Optional[str] = None
    timezone: str
    gateway_id: Optional[str] = None
    enrolled_dr: bool
    devices: list[Device]
    status: StatusSnapshot


class FleetStatusItem(BaseModel):
    home_id: int
    home_name: str
    city: Optional[str] = None
    status: str  # ok | watch | act | offline
    gateway_online: bool
    enrolled_dr: bool
    dr_active: bool
    home_load_w: Optional[float] = None
    grid_power_w: Optional[float] = None
    solar_power_w: Optional[float] = None
    battery_soc_pct: Optional[float] = None
    panel_ts: Optional[datetime] = None


class FleetDailyRow(BaseModel):
    date: date
    homes_reporting: Optional[int] = None
    total_grid_import_kwh: Optional[float] = None
    total_grid_export_kwh: Optional[float] = None
    total_solar_gen_kwh: Optional[float] = None
    total_home_load_kwh: Optional[float] = None
    avg_peak_demand_kw: Optional[float] = None
    max_peak_demand_kw: Optional[float] = None
    total_dr_reduction_kwh: Optional[float] = None
    avg_dr_performance: Optional[float] = None
    total_dr_events: Optional[int] = None
    total_estimated_cost_usd: Optional[float] = None
    total_estimated_savings_usd: Optional[float] = None
    avg_self_consumption_pct: Optional[float] = None
    avg_battery_soc_eod: Optional[float] = None


# =====================================================================
# Control (dispatch + audit)
# =====================================================================
ACTION_TYPES = (
    "curtail", "release", "augment", "setpoint_adjust", "relay_toggle",
    "battery_charge_mode", "eps_toggle", "channel_enable", "channel_disable",
    "precool", "preheat",
)


class DispatchTarget(BaseModel):
    kind: Literal["circuit", "thermostat", "plug", "demand_limit", "battery_mode"]
    circuit_id: Optional[int] = None
    device_id: Optional[int] = None


class DispatchRequest(BaseModel):
    home_id: int
    action_type: str
    target: DispatchTarget
    params: dict = {}
    event_id: Optional[int] = None


class DispatchResponse(BaseModel):
    action_id: int
    status: str  # always "pending" on accept


class ControlActionRow(BaseModel):
    action_id: int
    home_id: int
    device_id: Optional[int] = None
    circuit_id: Optional[int] = None
    event_id: Optional[int] = None
    ts: datetime
    action_type: str
    triggered_by: str
    status: str  # pending | acknowledged | success | failed
    success: Optional[bool] = None
    acknowledged_at: Optional[datetime] = None
    error_msg: Optional[str] = None


class ControlAdvisoryRow(BaseModel):
    advisory_id: int
    home_id: int
    device_id: Optional[int] = None
    circuit_id: Optional[int] = None
    event_id: Optional[int] = None
    ts: datetime
    controller: str
    action_type: str
    triggered_by: str
    operation_scenario: Optional[str] = None
    shadow_mode: bool
    baseline_cool_setpoint_c: Optional[float] = None
    baseline_heat_setpoint_c: Optional[float] = None
    recommended_cool_setpoint_c: Optional[float] = None
    recommended_heat_setpoint_c: Optional[float] = None
    expected_cost_usd: Optional[float] = None
    expected_energy_kwh: Optional[float] = None
