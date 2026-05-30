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


# =====================================================================
# Demand response (§13.4)
# =====================================================================
class DrEventRow(BaseModel):
    event_id: int
    event_reference: Optional[str] = None
    ven_id: Optional[str] = None
    vtn_id: Optional[str] = None
    signal_name: Optional[str] = None
    signal_type: Optional[str] = None
    signal_level: Optional[float] = None
    target_load_kw: Optional[float] = None
    event_start: datetime
    event_end: datetime
    status: str
    priority: Optional[int] = None
    test_event: bool
    active: bool
    participant_count: int


class DrParticipantRow(BaseModel):
    id: int
    event_id: int
    home_id: int
    home_name: Optional[str] = None
    opted_in: bool
    baseline_kw: Optional[float] = None
    actual_reduction_kw: Optional[float] = None
    reduction_target_kw: Optional[float] = None
    settlement_kwh: Optional[float] = None
    performance_score: Optional[float] = None
    notes: Optional[str] = None


class OpenAdrPrice(BaseModel):
    ts: Optional[datetime] = None
    program_name: Optional[str] = None
    period_type: Optional[str] = None
    price_per_kwh: Optional[float] = None
    interval_start: Optional[datetime] = None
    interval_end: Optional[datetime] = None


class OpenAdrPricePoint(BaseModel):
    interval_start: datetime
    interval_end: datetime
    period_type: Optional[str] = None
    price_per_kwh: float


# =====================================================================
# Energy analytics + reports (§13.3, §13.7)
# =====================================================================
class EnergyDay(BaseModel):
    date: date
    home_load_kwh: Optional[float] = None
    solar_gen_kwh: Optional[float] = None
    grid_import_kwh: Optional[float] = None
    grid_export_kwh: Optional[float] = None
    peak_demand_kw: Optional[float] = None
    peak_demand_at: Optional[datetime] = None
    self_consumption_pct: Optional[float] = None
    estimated_cost_usd: Optional[float] = None


class CircuitEnergy(BaseModel):
    circuit_id: int
    channel_num: Optional[int] = None
    circuit_name: Optional[str] = None
    energy_kwh: Optional[float] = None


class EnergyTotals(BaseModel):
    home_load_kwh: Optional[float] = None
    solar_gen_kwh: Optional[float] = None
    grid_import_kwh: Optional[float] = None
    grid_export_kwh: Optional[float] = None
    peak_demand_kw: Optional[float] = None
    self_consumption_pct: Optional[float] = None
    estimated_cost_usd: Optional[float] = None


class EnergyAnalytics(BaseModel):
    home_id: int
    home_name: Optional[str] = None
    start: datetime
    end: datetime
    days: list[EnergyDay]
    circuits: list[CircuitEnergy]
    totals: EnergyTotals


# =====================================================================
# Health (§13.6)
# =====================================================================
class DeviceHealth(BaseModel):
    device_id: int
    home_id: int
    home_name: Optional[str] = None
    device_type: str
    device_name: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    firmware_version: Optional[str] = None
    is_online: Optional[bool] = None
    online_updated_at: Optional[datetime] = None
    is_active: bool = True


class CoverageRow(BaseModel):
    device_id: int
    device_type: str
    device_name: Optional[str] = None
    reading_count: int = 0
    present_buckets: int = 0
    expected_buckets: int = 0
    coverage_pct: Optional[float] = None
    last_reading_at: Optional[datetime] = None


class CoverageReport(BaseModel):
    home_id: int
    home_name: Optional[str] = None
    date: str
    devices: list[CoverageRow]


# =====================================================================
# Admin — user management (§13.8)
# =====================================================================
class AdminUser(BaseModel):
    user_id: int
    username: str
    role: str
    is_active: bool
    created_at: Optional[datetime] = None
    homes: list[int] = []


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str
    is_active: bool = True
    homes: list[int] = []


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None
    homes: Optional[list[int]] = None
