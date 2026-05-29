# Smart Home Energy Dashboard — Design & Implementation Spec

> **Purpose.** A front-end web dashboard to **visualize, monitor, report, and dispatch control** for the VOLTTRON-based smart-home energy fleet. Styled after Airthings (calm, card-based, one big number + status ring per tile).
>
> **Audience.** This document is written to be implemented with the **Claude Code CLI**. Save it as `docs/DASHBOARD_DESIGN.md`, reference it from `CLAUDE.md`, and feed the build tasks in [§14](#14-build-plan-claude-code-tasks) to Claude Code one at a time.

---

## Table of contents

1. [How to use this with Claude Code](#1-how-to-use-this-with-claude-code)
2. [System context](#2-system-context)
3. [Tech stack](#3-tech-stack)
4. [Repository layout](#4-repository-layout)
5. [Existing assets to reuse](#5-existing-assets-to-reuse)
6. [Data model reference](#6-data-model-reference)
7. [Backend API specification](#7-backend-api-specification)
8. [Real-time (SSE)](#8-real-time-sse)
9. [Auth & RBAC](#9-auth--rbac)
10. [Control dispatch — the VOLTTRON bridge](#10-control-dispatch--the-volttron-bridge)
11. [Frontend architecture](#11-frontend-architecture)
12. [Design system (Airthings)](#12-design-system-airthings)
13. [Screen specifications](#13-screen-specifications)
14. [Build plan (Claude Code tasks)](#14-build-plan-claude-code-tasks)
15. [Deployment](#15-deployment)
16. [Configuration & secrets](#16-configuration--secrets)
17. [Testing](#17-testing)
18. [Open questions to confirm](#18-open-questions-to-confirm)
19. [Appendix: SQL for continuous aggregates](#19-appendix-sql-for-continuous-aggregates)

---

## 1. How to use this with Claude Code

1. Copy this file to `docs/DASHBOARD_DESIGN.md`.
2. Add a pointer in your repo root `CLAUDE.md`:
   ```md
   ## Dashboard
   The web dashboard spec lives in `docs/DASHBOARD_DESIGN.md`.
   Build it in the order given in §14. Never command devices directly
   from the browser — dispatch always goes API → VOLTTRON → control_actions.
   ```
3. Run Claude Code from the repo root and work through [§14](#14-build-plan-claude-code-tasks) **one task per session**, e.g.:
   ```
   claude
   > Read docs/DASHBOARD_DESIGN.md. Implement Task 1 only. Stop and let me review.
   ```
4. Each task lists **acceptance criteria** — ask Claude Code to verify against them before moving on.

**Non-negotiable rules** (repeat these to Claude Code as needed):
- The browser never talks to a device. All control flows API → VOLTTRON bus → device agent → `control_actions`.
- Every state-changing endpoint enforces role + home-scope **server-side**.
- Critical circuits (`panel_circuits.is_critical = TRUE`) can never be curtailed.
- All SQL is parameterized. No string interpolation into queries.

---

## 2. System context

The existing system is a 4-tier pipeline. The dashboard adds **tier 4** and one new server component (the **API**):

```
Devices (EcoFlow SHP2 + DELTA Pro Ultra, Ecobee, Kasa KP125M)
   │  vendor APIs / Matter
   ▼
Edge gateway  — Raspberry Pi 5, per home
   • VOLTTRON platform
   • ILC agent (curtail/augment state machine, AHP scoring)  [ilc.agent]
   • Device agents (ecoflow_agent, ecobee_agent, kasa agent)
   • OpenADR 3.1 VEN client
   │  telemetry up (data_collectors → DB)   |   commands down (MQTT/relay)
   ▼
Central server
   • TimescaleDB  (pezerr_db)            ← already exists
   • FastAPI service                     ← NEW (this project)
   • Analytics / rollups (pg_cron)       ← partially exists
   │  REST + SSE
   ▼
Web dashboard (browser)                  ← NEW (this project)
   Visualize · Monitor/Report · Dispatch
```

**Key fact:** the fleet is multi-gateway. The central server cannot directly RPC into each home's VOLTTRON instance, so dispatch is asynchronous (see [§10](#10-control-dispatch--the-volttron-bridge)).

---

## 3. Tech stack

### Frontend
| Concern | Choice | Notes |
|---|---|---|
| Framework | React 18 + TypeScript + Vite | Fast HMR, simple build |
| Data fetching | TanStack Query v5 | Caching, polling, SSE integration |
| State | Zustand | Auth/session + UI prefs only |
| Styling | Tailwind CSS + CSS variables | Token layer in [§12](#12-design-system-airthings) |
| Charts | **ECharts** (or uPlot) | Handles 100k+ points; Sankey for power flow. **Avoid Recharts** at this scale |
| Routing | React Router v6 | |
| Icons | Tabler / Lucide (outline) | |

### Backend
| Concern | Choice | Notes |
|---|---|---|
| API | **FastAPI** | Reuses existing Python stack (`data_collectors/`) |
| DB driver | asyncpg (+ SQLAlchemy Core optional) | Async; pool over `pezerr_db` |
| Auth | python-jose (JWT) + passlib | RBAC in [§9](#9-auth--rbac) |
| Realtime | SSE via `sse-starlette` | Postgres `LISTEN/NOTIFY` or short poll |
| Command transport | MQTT (paho/aiomqtt) | Central API → per-gateway topic |
| Reports | WeasyPrint (PDF) + pandas (CSV) | |

---

## 4. Repository layout

Add to the existing repo (do not disturb `data_collectors/`, `database/`, `GridServices/`):

```
repo/
├── data_collectors/        # existing
├── database/               # existing schema (001..009 + files/)
├── GridServices/Control/ILCAgent/   # existing ILC
├── config/                 # existing JSON configs (reused by the API)
├── api/                    # NEW — FastAPI service
│   ├── main.py
│   ├── db.py               # asyncpg pool; reuse config/data_analytics_config.json
│   ├── auth.py             # JWT, RBAC dependencies
│   ├── deps.py
│   ├── routers/
│   │   ├── homes.py
│   │   ├── telemetry.py
│   │   ├── dr.py
│   │   ├── control.py
│   │   ├── reports.py
│   │   └── stream.py       # SSE
│   ├── control_bus.py      # MQTT publisher → gateways
│   ├── models.py           # pydantic schemas
│   └── requirements.txt
├── agents/
│   └── dashboard_command/  # NEW — VOLTTRON agent on each gateway
│       └── dashboard_command/agent.py
├── database/
│   └── 010_dashboard_auth.sql   # NEW — users + access
└── dashboard/              # NEW — React app
    ├── src/
    │   ├── api/            # typed client + query hooks
    │   ├── components/     # Card, MetricTile, StatusRing, Badge, ...
    │   ├── pages/          # FleetOverview, HomeDetail, ...
    │   ├── theme/          # tokens.css, tailwind preset
    │   ├── auth/
    │   └── App.tsx
    ├── tailwind.config.ts
    └── package.json
```

---

## 5. Existing assets to reuse

- **`data_collectors/config.py`** — `get_db_dsn()`, `get_db_config()`, `CONFIG_DIR`, `PROJECT_ROOT`. The API's `db.py` should build its DSN the same way (`config/data_analytics_config.json → database`).
- **`data_collectors/db.py`** — `DatabaseManager` shows the exact insert/lookup patterns and column names. Mirror its parameterization style.
- **Database schema** — `database/files/01..08_*.sql` + `database/009_openadr_events.sql`. The API is a read layer over these tables plus a guarded writer to `control_actions`.
- **ILC agent** — `GridServices/Control/ILCAgent/ilc/ilc_agent.py`. Exposes `@RPC.export update_configurations(data)` and reserves devices via the actuator (`request_new_schedule`). VIP identity: `ilc.agent`. Config key `demand_limit` is the curtailment target.
- **Device agents** — `agents/ecoflow_agent`, `agents/ecobee_agent`, and the Kasa agent are the only components allowed to actuate hardware.

---

## 6. Data model reference

The dashboard **reads** the time-series + summary tables and **writes** only `control_actions`. Column names below are authoritative (from the schema files).

### Reference
- `homes(home_id, home_name, address, city, state, zip_code, utility_id, timezone, gateway_id, volttron_instance_id, enrolled_dr, ...)`
- `devices(device_id, home_id, device_type[smart_panel|battery|thermostat|smart_plug], device_name, manufacturer, model, serial_number, api_identifier, is_online, online_updated_at, metadata, ...)`
- `panel_circuits(circuit_id, device_id, channel_num[1..12], circuit_name, rated_amps, rated_voltage, is_critical, is_controllable, load_description)`

### Telemetry (TimescaleDB hypertables, partitioned on `ts`)
- `smart_panel_readings(device_id, home_id, ts, grid_power_w, grid_frequency_hz, solar_power_w, battery_power_w, battery_soc_pct, home_load_w, grid_status, eps_mode_active, ...)` — `grid_power_w` +import/−export; `battery_power_w` +charge/−discharge.
- `panel_circuit_readings(circuit_id, device_id, home_id, ts, power_w, current_a, voltage_v, energy_kwh, is_enabled)`
- `battery_readings(device_id, home_id, ts, soc_pct, soh_pct, status, power_w, capacity_wh, ac_in_power_w, ac_out_power_w, ...)`
- `thermostat_readings(device_id, home_id, ts, indoor_temp_c, outdoor_temp_c, indoor_humidity_pct, heat_setpoint_c, cool_setpoint_c, hvac_mode, hvac_state, fan_mode, occupancy_status, hold_type, hold_until, ilc_override_active, ilc_cool_setpoint_c, ilc_heat_setpoint_c)`
- `smart_plug_readings(device_id, home_id, ts, power_w, voltage_v, current_a, energy_kwh, relay_state, ilc_curtail_active, ...)`
- `weather_observations` / `weather_forecast` (per `weather_locations`).

### Forecast
- `home_load_forecast(home_id, generated_at, forecast_ts, load_w, temp_source)`
- `circuit_load_forecast(circuit_id, home_id, generated_at, forecast_ts, load_w)`

### Demand response
- `openadr_events(ts, program_name, program_id, event_name, event_id, priority, period_type[peak|off_peak], price_per_kwh, interval_start, interval_end, ven_id, ven_name)` — the live price feed.
- `dr_events(event_id, ven_id, vtn_id, event_reference, signal_name, signal_type, signal_level, target_load_kw, event_start, event_end, status[pending|active|completed|cancelled|failed], priority, test_event, raw_payload)`
- `dr_event_participants(event_id, home_id, opted_in, baseline_kw, actual_reduction_kw, reduction_target_kw, settlement_kwh, performance_score)`

### Control
- `control_advisories(advisory_id, home_id, device_id, circuit_id, event_id, ts, controller[mpc|rbc], action_type, triggered_by, operation_scenario, shadow_mode, baseline_cool_setpoint_c, recommended_cool_setpoint_c, baseline_heat_setpoint_c, recommended_heat_setpoint_c, expected_cost_usd, expected_energy_kwh, detail)` — **read-only** shadow recommendations.
- `control_actions(action_id, home_id, device_id, circuit_id, event_id, ts, action_type[curtail|release|setpoint_adjust|relay_toggle|channel_enable|channel_disable|...], triggered_by[ILC_agent|DR_event|schedule|manual|override|safety], ilc_priority_score, ilc_demand_target_kw, command_payload(JSONB), response_payload(JSONB), success, error_msg, acknowledged_at)` — **the audit trail the dashboard writes to**.

### Aggregates
- `hourly_energy_summary(home_id, device_id, device_type, hour_start, avg/max/min/p95_power_w, energy_kwh, reading_count, expected_count, coverage_pct)`
- `daily_home_summary(home_id, date, grid_import_kwh, grid_export_kwh, solar_gen_kwh, battery_charge_kwh, battery_discharge_kwh, home_load_kwh, peak_demand_kw, ac_runtime_s, dr_reduction_kwh, dr_performance_score, estimated_cost_usd, estimated_savings_usd, ...)`
- `fleet_daily_summary` (materialized view) — fleet KPIs by date.

---

## 7. Backend API specification

Base path `/api/v1`. All responses JSON. All endpoints require a valid JWT except `/auth/*`. Home-scoped endpoints are filtered to the caller's accessible homes.

### Auth
```
POST /auth/login        {username, password} -> {access_token, role, homes:[...]}
GET  /auth/me           -> {user_id, role, homes:[...]}
```

### Homes & fleet
```
GET  /homes                         -> [{home_id, home_name, city, enrolled_dr, gateway_online, ...}]
GET  /homes/{id}                    -> home + devices + latest status snapshot
GET  /homes/{id}/summary?date=      -> row from daily_home_summary
GET  /fleet/summary?from=&to=       -> rows from fleet_daily_summary
GET  /fleet/status                  -> live per-home rollup for the overview grid
```

`GET /fleet/status` is the overview screen's primary call: for each accessible home, return the latest `smart_panel_readings` (home_load_w, grid_power_w, solar_power_w), latest `battery_readings.soc_pct`, `devices.is_online`, and whether a `dr_events` row is currently `active`. Derive a `status ∈ {ok, watch, act, offline}`:
- `offline` if gateway/panel offline,
- `act` if EPS mode / grid loss / active DR event,
- `watch` if `home_load_w` within 15% of target or `soc_pct < 35`,
- else `ok`.

### Telemetry (bucketed)
```
GET /homes/{id}/panel?from=&to=&bucket=1m|5m|1h
GET /homes/{id}/circuits?from=&to=&bucket=...    -> per-circuit series + circuit metadata
GET /homes/{id}/battery?from=&to=&bucket=...
GET /homes/{id}/thermostat?from=&to=&bucket=...
GET /homes/{id}/plugs?from=&to=&bucket=...
GET /homes/{id}/weather?from=&to=
GET /homes/{id}/load-forecast?generated_at=latest  -> home + per-circuit forecast
```
Use `time_bucket` and the continuous aggregates from [§19](#19-appendix-sql-for-continuous-aggregates). Reject ranges that would scan raw hypertables beyond ~48h at 1m resolution; force a coarser bucket.

Example query for `GET /homes/{id}/panel` at 5m:
```sql
SELECT time_bucket('5 minutes', ts) AS bucket,
       avg(home_load_w)   AS home_load_w,
       avg(grid_power_w)  AS grid_power_w,
       avg(solar_power_w) AS solar_power_w,
       avg(battery_soc_pct) AS soc_pct
FROM   smart_panel_readings
WHERE  home_id = $1 AND ts >= $2 AND ts < $3
GROUP  BY bucket ORDER BY bucket;
```

### Demand response
```
GET /dr/events?status=&from=&to=         -> dr_events
GET /dr/events/{id}/participants         -> dr_event_participants joined to homes
GET /openadr/price?home_id=              -> latest active openadr_events row (price + window)
GET /openadr/price/history?from=&to=     -> price curve
```

### Control
```
GET  /control/advisories?home_id=&active=true   -> control_advisories (shadow)
GET  /control/actions?home_id=&from=&to=        -> control_actions audit log
POST /control/dispatch                          -> see §10 (operator/admin only)
GET  /control/actions/{action_id}               -> poll success/acknowledged_at
```

### Health & reports
```
GET  /devices?home_id=&online=        -> devices + is_online/online_updated_at
GET  /health/coverage?home_id=&date=  -> coverage_pct gaps from hourly_energy_summary
GET  /reports/daily?home_id=&date=    -> PDF
GET  /reports/monthly?home_id=&month= -> PDF
GET  /reports/export?...&format=csv   -> CSV
```

### Response conventions
- Timestamps ISO-8601 UTC; the frontend converts to `homes.timezone` (America/Los_Angeles).
- Round powers to whole watts, energy to 3 decimals, money to cents, percentages to 1 decimal.
- Errors: `{detail, code}` with appropriate HTTP status (401/403/404/422).

---

## 8. Real-time (SSE)

```
GET /api/v1/stream/homes/{id}   (text/event-stream)
```
Server pushes a JSON event every 5–10 s (or on `NOTIFY`) containing the latest panel reading, battery SoC, active price, and any new `control_actions` status change for that home. Implementation options, simplest first:
1. **Poll-and-push** — the SSE generator selects the newest rows each interval and emits deltas.
2. **LISTEN/NOTIFY** — add an `AFTER INSERT` trigger on `smart_panel_readings` that `pg_notify('panel_<home_id>', payload)`; the SSE handler subscribes via a dedicated asyncpg connection.

Frontend: a `useHomeStream(homeId)` hook wraps `EventSource`, reconnects on drop, and writes into the TanStack Query cache so live tiles update without refetch. Non-live pages just poll with `refetchInterval`.

---

## 9. Auth & RBAC

### Roles
| Role | Scope | Capabilities |
|---|---|---|
| `viewer` | own home | read monitoring + reports for their home only |
| `operator` | assigned homes | read + **dispatch** (curtail/release/setpoint) |
| `fleet_analyst` | all homes | read + reports/export; **no dispatch** |
| `admin` | all | everything + user/device/home config + DR enrollment |

### Tables (`database/010_dashboard_auth.sql`)
```sql
CREATE TABLE app_users (
  user_id     SERIAL PRIMARY KEY,
  username    VARCHAR(100) NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role        VARCHAR(20) NOT NULL CHECK (role IN ('viewer','operator','fleet_analyst','admin')),
  is_active   BOOLEAN NOT NULL DEFAULT TRUE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE user_home_access (
  user_id INTEGER NOT NULL REFERENCES app_users(user_id) ON DELETE CASCADE,
  home_id INTEGER NOT NULL REFERENCES homes(home_id)     ON DELETE CASCADE,
  PRIMARY KEY (user_id, home_id)
);
```
`fleet_analyst`/`admin` see all homes regardless of `user_home_access`.

### Enforcement
- JWT claims: `{user_id, role, homes:[...]}` (homes empty for fleet roles → "all").
- A FastAPI dependency `require(role_min, home_param="id")` checks role rank and that the requested `home_id` is in scope. Apply to **every** router.
- `POST /control/dispatch` requires `operator` or `admin`. Reject others with 403 even if the UI hid the control.

---

## 10. Control dispatch — the VOLTTRON bridge

This is the trickiest piece. Because each home has its own gateway/VOLTTRON instance, the central API **cannot** call `ilc.agent` RPC directly. Dispatch is asynchronous via a message broker.

### Flow
```
Dashboard (operator confirms)
  → POST /api/v1/control/dispatch
      1. RBAC check (operator/admin, home in scope)
      2. Safety check: load panel_circuits; reject if is_critical or not is_controllable
      3. INSERT control_actions (status: success=NULL, command_payload, triggered_by='manual')
      4. Publish command to MQTT topic  cmd/home/<gateway_id>/control   {action_id, ...}
      5. Return {action_id, status:'pending'}
  → (edge) agents/dashboard_command (VOLTTRON) subscribed to that topic:
      6. Translate to action:
         - demand-limit change   → vip.rpc.call('ilc.agent','update_configurations',{...})
         - circuit on/off         → schedule via actuator (request_new_schedule) → ecoflow_agent (load channel control)
         - thermostat setpoint    → ecobee_agent RPC
         - plug relay             → kasa agent RPC
      7. Publish result to  cmd/home/<gateway_id>/result  {action_id, success, response, ack_ts}
  → (central) result listener (in the API process or a small worker):
      8. UPDATE control_actions SET success, response_payload, acknowledged_at WHERE action_id=...
  → Dashboard polls GET /control/actions/{action_id} (or receives it over SSE) → flips pending→confirmed
```

### Dispatch request schema
```json
{
  "home_id": 5,
  "action_type": "curtail",
  "target": { "kind": "circuit", "circuit_id": 42 },
  "params": { "duration_min": 120 },
  "event_id": 17
}
```
`target.kind ∈ {circuit, thermostat, plug, demand_limit, battery_mode}`.

### FastAPI sketch (`api/routers/control.py`)
```python
@router.post("/control/dispatch")
async def dispatch(req: DispatchRequest, user=Depends(require("operator"))):
    if req.home_id not in user.homes_or_all():
        raise HTTPException(403, "home not in scope")

    # Safety: never curtail critical / non-controllable circuits
    if req.target.kind == "circuit":
        c = await db.fetchrow(
            "SELECT is_critical, is_controllable FROM panel_circuits WHERE circuit_id=$1",
            req.target.circuit_id)
        if c is None: raise HTTPException(404, "circuit")
        if c["is_critical"] or not c["is_controllable"]:
            raise HTTPException(422, "circuit is critical/non-controllable")

    gateway_id = await db.fetchval(
        "SELECT gateway_id FROM homes WHERE home_id=$1", req.home_id)

    action_id = await db.fetchval(
        """INSERT INTO control_actions
             (home_id, device_id, circuit_id, event_id, ts,
              action_type, triggered_by, command_payload)
           VALUES ($1,$2,$3,$4,NOW(),$5,'manual',$6)
           RETURNING action_id""",
        req.home_id, req.device_id, req.circuit_id, req.event_id,
        req.action_type, json.dumps(req.params))

    await control_bus.publish(
        f"cmd/home/{gateway_id}/control",
        {"action_id": action_id, **req.dict()})

    return {"action_id": action_id, "status": "pending"}
```

### VOLTTRON command agent sketch (`agents/dashboard_command/dashboard_command/agent.py`)
```python
class DashboardCommandAgent(Agent):
    @Core.receiver("onstart")
    def _start(self, sender, **kw):
        self.mqtt.subscribe(f"cmd/home/{self.gateway_id}/control", self._on_cmd)

    def _on_cmd(self, payload):
        try:
            if payload["target"]["kind"] == "demand_limit":
                self.vip.rpc.call("ilc.agent", "update_configurations",
                                  {"config": {"demand_limit": payload["params"]["kw"]}}).get(timeout=30)
            elif payload["target"]["kind"] == "circuit":
                # schedule + actuate via the EcoFlow load-channel control path
                self._actuate_circuit(payload)
            elif payload["target"]["kind"] == "thermostat":
                self.vip.rpc.call("ecobee.agent", "set_setpoint", payload["params"]).get(timeout=30)
            # ... plug / battery_mode
            ok, resp = True, {"detail": "applied"}
        except Exception as e:
            ok, resp = False, {"error": str(e)}
        self.mqtt.publish(f"cmd/home/{self.gateway_id}/result",
                          {"action_id": payload["action_id"], "success": ok,
                           "response": resp, "ack_ts": utils.format_timestamp(get_aware_utc_now())})
```

> **Confirm before coding:** the exact EcoFlow SHP2 load-channel control command (`PUT /iot-open/sign/device/quota`, `operateType`, `cmdSet`/`id`) — see [§18](#18-open-questions-to-confirm). The `data_collectors/ecoflow_client.py` currently only does GET quota; the write command must be added to the **device agent**, not the dashboard.

---

## 11. Frontend architecture

- **Data layer** (`src/api/`): a typed fetch client + one TanStack Query hook per endpoint (`useFleetStatus`, `useHomePanel`, `useAdvisories`, `useDispatch`, …). All time conversion to the home's tz happens here.
- **Auth** (`src/auth/`): login, token storage (httpOnly cookie preferred; else memory + refresh), a `<RequireRole>` route guard, and a `useAuth()` hook exposing `role`/`homes`.
- **Routing**: `/` fleet, `/homes/:id`, `/homes/:id/energy`, `/dr`, `/control/:id`, `/health`, `/reports`, `/admin`. Control + admin routes wrapped in role guards (and the API still enforces).
- **Live data**: `useHomeStream(id)` (SSE) feeds live tiles; everything else polls.
- **Components**: `Card`, `MetricTile`, `StatusRing`, `Badge`, `PowerFlowSankey`, `TimeSeriesChart`, `CircuitBars`, `AdvisoryCard`, `ConfirmDialog`, `ActionLog`.

---

## 12. Design system (Airthings)

Calm, flat, card-based. One accent (teal), semantic status colors, big numbers, status rings. No gradients/heavy shadows. 14px card radius.

### Tokens (`src/theme/tokens.css`)
```css
:root {
  /* surfaces */
  --bg-page:    #F6F7F8;
  --bg-card:    #FFFFFF;
  --bg-subtle:  #F0F2F3;
  --border:     rgba(20,30,40,0.10);
  /* text */
  --text:       #1B2733;
  --text-muted: #5B6B7A;
  --text-faint: #8A98A6;
  /* accent (teal) */
  --accent:     #0F6E56;
  --accent-soft:#E1F5EE;
  /* status */
  --ok:   #2E9E6B;  --ok-bg:   #E7F5EE;
  --watch:#C9821B;  --watch-bg:#FBF0DC;
  --act:  #C4453B;  --act-bg:  #FBEAE9;
  --info: #1E6FA8;  --info-bg: #E6F1FB;
  /* shape */
  --radius:   12px;
  --radius-lg:16px;
  --shadow:   0 1px 2px rgba(20,30,40,0.06);
}
@media (prefers-color-scheme: dark) {
  :root { --bg-page:#0F1417; --bg-card:#161C21; --bg-subtle:#1C242A;
          --text:#E7ECEF; --text-muted:#9DAAB4; --border:rgba(255,255,255,0.10); }
}
```

### Component rules
- **Card**: `--bg-card`, `0.5px` border, `--radius-lg`, padding `16px 20px`, optional `--shadow`.
- **MetricTile**: `--bg-subtle`, no border, `--radius`. 13px muted label on top, 24px/500 number below. Grids of 2–4 with `gap: 12px`.
- **StatusRing**: SVG donut, `r=26`, `stroke-width=6`, rounded cap, `stroke-dashoffset` = `(1 − value/100) × 2πr`. Color by status. Center text = value.
- **Badge**: 12px, `4px 10px`, `--radius`, status bg + matching dark text from same family (never plain black/gray).
- **Status logic** (energy analog of Airthings air-quality): `ok` (green) below target & healthy SoC; `watch` (amber) approaching peak / SoC < 35; `act` (red) active DR / EPS / grid loss; `offline` (gray).
- Typography: one sans family, two weights (400/500). Sentence case everywhere. Body 15–16px, line-height 1.6.

### Tailwind preset
Map the tokens into `tailwind.config.ts` (`theme.extend.colors`) so utilities like `bg-card`, `text-muted`, `text-accent`, `bg-ok-bg` exist.

---

## 13. Screen specifications

### 13.1 Fleet overview `/`  (operator, analyst, admin)
- KPI strip: homes online, fleet load (kW), active DR events, avg SoC — from `GET /fleet/status`.
- Responsive grid of home cards: name, status dot, SoC ring, current load, grid/DR badge. Card → `/homes/:id`.

### 13.2 Home detail (live) `/homes/:id`  (all roles, own home for viewer)
- OpenADR price banner (latest `openadr_events`).
- Four power-flow tiles: grid / solar / battery / home load (signed).
- Battery SoC ring + status + usable kWh.
- Thermostat tile (indoor temp, mode, setpoint).
- Circuit-by-load bars (`panel_circuit_readings`, ranked).
- 24h history drawer: bucketed series; **PowerFlowSankey** + area chart.
- Live updates via SSE.

### 13.3 Energy & analytics `/homes/:id/energy`
- From `hourly_energy_summary` + `daily_home_summary`: load trend, peak-demand markers, self-consumption %, cost, circuit energy ranking. Date/range picker.

### 13.4 Demand response `/dr`
- `dr_events` timeline; active event card (target + window); OpenADR price curve; participation table (`dr_event_participants`) baseline vs actual + performance score.

### 13.5 Control / dispatch `/control/:id`  (operator, admin only)
- Advisory cards from `control_advisories` (MPC/RBC shadow) with Apply.
- Circuit toggles → ConfirmDialog → `POST /control/dispatch`. Critical circuits locked.
- Thermostat setpoint nudge; plug relay; battery-mode (if supported).
- Recent actions log polling `success`/`acknowledged_at`.

### 13.6 Device health `/health`
- `devices.is_online`/`online_updated_at`; data coverage from `hourly_energy_summary.coverage_pct` (flag < 80%); firmware.

### 13.7 Reports `/reports`  (analyst, admin)
- Generate daily/monthly per-home PDF; CSV export.

### 13.8 Admin `/admin`  (admin)
- Users & roles (`app_users`, `user_home_access`); home/device config; DR enrollment (`homes.enrolled_dr`).

---

## 14. Build plan (Claude Code tasks)

Work top to bottom, one task per session. Each ends with acceptance criteria.

**Task 1 — Frontend scaffold + design system.**
Create `dashboard/` (Vite + React + TS + Tailwind + TanStack Query + React Router + Zustand). Add `src/theme/tokens.css` and Tailwind preset from §12. Build `Card`, `MetricTile`, `StatusRing`, `Badge` with Storybook-style fixtures. ✔ App runs; components match §12; light/dark both work.

**Task 2 — API skeleton + DB pool.**
Create `api/` (FastAPI + asyncpg). `db.py` builds the DSN from `config/data_analytics_config.json` (reuse `data_collectors/config.py` logic). Implement `/homes`, `/homes/{id}`, `/fleet/summary`. Add OpenAPI + CORS. ✔ `/docs` lists endpoints; `/homes` returns seeded homes.

**Task 3 — Continuous aggregates + telemetry endpoints.**
Add the SQL from §19 as `database/011_continuous_aggregates.sql`. Implement bucketed `/homes/{id}/panel|circuits|battery|thermostat|plugs` with `?bucket`. Enforce bucket vs range guard. ✔ Multi-day query returns in <300ms using the agg.

**Task 4 — Auth & RBAC.**
Add `database/010_dashboard_auth.sql`, `auth.py` (JWT, bcrypt), and the `require(role)` dependency with home-scope. Wire `/auth/login`, `/auth/me`. Apply guards to all routers. ✔ viewer blocked from other homes; analyst blocked from `/control/dispatch` (403).

**Task 5 — Fleet status + overview screen.**
Implement `GET /fleet/status` with the status derivation in §7. Build the Fleet overview page (KPI strip + home card grid). ✔ matches §13.1; clicking a card routes to home detail.

**Task 6 — Home detail + SSE.**
Implement `/stream/homes/{id}` (poll-and-push first). Build the Home detail page (price banner, power tiles, SoC ring, thermostat, circuit bars) + 24h drawer with ECharts Sankey + area chart. ✔ live tiles update without refresh; history renders.

**Task 7 — Control bridge (server).**
Implement `POST /control/dispatch` per §10 (RBAC + critical-circuit guard + insert + MQTT publish), `control_bus.py`, the result listener (UPDATE on result), and `GET /control/advisories|actions`. ✔ dispatch inserts a pending `control_actions` row and publishes to MQTT; critical circuit returns 422.

**Task 8 — VOLTTRON command agent (edge).**
Create `agents/dashboard_command` subscribing to `cmd/home/<gateway_id>/control`, translating to ILC `update_configurations` / actuator / device-agent RPC, publishing results. ✔ a test command flips a circuit and writes back `success`/`acknowledged_at`.

**Task 9 — Control panel (UI).**
Build `/control/:id`: advisory cards (Apply), circuit toggles + ConfirmDialog, action log polling status. Hide for non-operators. ✔ matches §13.5; locked critical circuits; pending→confirmed visible.

**Task 10 — Demand response screen.**
Implement `/dr/events`, `/dr/events/{id}/participants`, `/openadr/price[/history]`. Build the DR page. ✔ active event, price curve, participation render.

**Task 11 — Analytics + reports.**
Build `/homes/:id/energy` over the aggregates; implement `/reports/daily|monthly|export` (WeasyPrint/pandas). ✔ PDF/CSV download; analytics charts render.

**Task 12 — Health, admin, deploy.**
Build `/health` and `/admin`. Dockerize API + dashboard; reverse proxy + TLS; `docker-compose.yml`. ✔ both behind HTTPS; admin can manage users.

---

## 15. Deployment

- API: `uvicorn` behind `gunicorn` workers (or `uvicorn --workers`), containerized.
- Dashboard: build static, serve via the reverse proxy (Caddy/Nginx) with TLS; proxy `/api` and `/api/v1/stream` (disable buffering for SSE).
- MQTT broker (Mosquitto) reachable by central API and each gateway; per-home topic ACLs.
- `docker-compose.yml` for server side (api, proxy, broker). TimescaleDB stays as the existing service.
- SSE note: set proxy `proxy_buffering off;` and a long read timeout on the stream location.

---

## 16. Configuration & secrets

- API reads DB creds from the existing `config/data_analytics_config.json` (`database` block) via `get_db_dsn()`.
- New `config/api_config.json`: `{jwt_secret, jwt_ttl_min, mqtt:{host,port,user,pass}, cors_origins:[...]}`. Keep out of git.
- Never put secrets in URLs or the frontend bundle. JWT secret server-side only.

---

## 17. Testing

- API: pytest + httpx against a disposable TimescaleDB (docker) seeded via `data_collectors seed`. Test RBAC (403 matrix), bucket guard, critical-circuit rejection.
- Dispatch: mock the MQTT bus; assert a pending `control_actions` row is written and the result listener updates it.
- Frontend: Vitest + Testing Library for components; Playwright smoke for login → fleet → home → (operator) dispatch confirm.
- Visual: snapshot the three reference screens against the mockups.

---

## 18. Open questions to confirm

1. **EcoFlow SHP2 load-channel control command** — exact `PUT /iot-open/sign/device/quota` body (`operateType`, `cmdSet`, `id`, channel mask). Confirm against the EcoFlow Smart Home Panel developer doc before Task 8. Add the write to the **ecoflow device agent**, not the dashboard.
2. **Ecobee setpoint RPC** — confirm the `ecobee_agent` exposes a `set_setpoint`/hold method; otherwise add one (uses `smartWrite` scope).
3. **Kasa KP125M relay** — confirm the Matter/Kasa agent's relay-toggle RPC name.
4. **Gateway addressing** — confirm `homes.gateway_id` is populated for every home and matches the MQTT topic each gateway subscribes to.
5. **DR target → demand_limit mapping** — define how `dr_events.target_load_kw` / `signal_level` maps to ILC `demand_limit` for auto-dispatch vs operator-confirmed.
6. **`battery_readings` column swap** — `tests/plot_example_unit_battery.py` notes `ac_in_power_w`/`ac_out_power_w` are swapped on this deployment. Decide whether to fix at the collector/transformer or compensate in the API; document the chosen source of truth.

---

## 19. Appendix: SQL for continuous aggregates

`database/011_continuous_aggregates.sql` — speeds up multi-day telemetry reads.

```sql
-- 5-minute panel rollup
CREATE MATERIALIZED VIEW IF NOT EXISTS panel_5m
WITH (timescaledb.continuous) AS
SELECT home_id, device_id,
       time_bucket('5 minutes', ts) AS bucket,
       avg(home_load_w)     AS home_load_w,
       avg(grid_power_w)    AS grid_power_w,
       avg(solar_power_w)   AS solar_power_w,
       avg(battery_power_w) AS battery_power_w,
       avg(battery_soc_pct) AS battery_soc_pct
FROM smart_panel_readings
GROUP BY home_id, device_id, bucket;

SELECT add_continuous_aggregate_policy('panel_5m',
  start_offset => INTERVAL '3 days',
  end_offset   => INTERVAL '5 minutes',
  schedule_interval => INTERVAL '5 minutes');

-- 1-hour panel rollup (long ranges)
CREATE MATERIALIZED VIEW IF NOT EXISTS panel_1h
WITH (timescaledb.continuous) AS
SELECT home_id, device_id,
       time_bucket('1 hour', ts) AS bucket,
       avg(home_load_w)  AS home_load_w,
       max(home_load_w)  AS peak_load_w,
       avg(grid_power_w) AS grid_power_w,
       avg(solar_power_w) AS solar_power_w,
       avg(battery_soc_pct) AS battery_soc_pct
FROM smart_panel_readings
GROUP BY home_id, device_id, bucket;

SELECT add_continuous_aggregate_policy('panel_1h',
  start_offset => INTERVAL '30 days',
  end_offset   => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour');

-- 5-minute circuit rollup
CREATE MATERIALIZED VIEW IF NOT EXISTS circuit_5m
WITH (timescaledb.continuous) AS
SELECT circuit_id, home_id,
       time_bucket('5 minutes', ts) AS bucket,
       avg(power_w) AS power_w,
       max(power_w) AS peak_w
FROM panel_circuit_readings
GROUP BY circuit_id, home_id, bucket;

SELECT add_continuous_aggregate_policy('circuit_5m',
  start_offset => INTERVAL '3 days',
  end_offset   => INTERVAL '5 minutes',
  schedule_interval => INTERVAL '5 minutes');
```

API selects from `smart_panel_readings` for `bucket=1m` and ranges ≤48h, from `panel_5m` for `bucket=5m`, and from `panel_1h` for `bucket=1h` / long ranges.
