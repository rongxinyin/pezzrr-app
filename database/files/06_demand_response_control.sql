-- =============================================================
-- FILE: 06_demand_response_control.sql
-- DESC: OpenADR demand response events and ILC control action log.
-- DEPENDS ON: 01_core_reference.sql, 02_smart_panel.sql
-- =============================================================

-- -------------------------------------------------------------
-- ENUM TYPES
-- -------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE dr_status_enum AS ENUM (
        'pending',
        'active',
        'completed',
        'cancelled',
        'failed'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE action_type_enum AS ENUM (
        'curtail',
        'release',
        'augment',
        'setpoint_adjust',
        'relay_toggle',
        'battery_charge_mode',
        'eps_toggle',
        'channel_enable',
        'channel_disable',
        'precool',
        'preheat',
        'set_operating_mode'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE trigger_source_enum AS ENUM (
        'ILC_agent',
        'DR_event',
        'schedule',
        'manual',
        'override',
        'safety'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- -------------------------------------------------------------
-- TABLE: dr_events
-- One row per OpenADR 2.0 event received from the VTN (utility).
-- The VOLTTRON OpenADR VEN client agent writes to this table.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dr_events (
    event_id            SERIAL              PRIMARY KEY,
    ven_id              VARCHAR(100),                   -- VOLTTRON VEN identity
    vtn_id              VARCHAR(100),                   -- Utility VTN (server) identity
    event_reference     VARCHAR(200)        UNIQUE,     -- OpenADR eiEventID (globally unique)
    signal_name         VARCHAR(100),                   -- e.g. 'SIMPLE', 'ELECTRICITY_PRICE', 'LOAD_DISPATCH'
    signal_type         VARCHAR(50),                    -- 'LEVEL' | 'PRICE' | 'PRICE_RELATIVE' | 'X_LOAD_CONTROL_CAPACITY'
    signal_level        NUMERIC(8,3),                   -- Payload value (e.g. curtailment level 0/1/2/3)
    target_load_kw      NUMERIC(10,3),                  -- Explicit kW reduction target if specified
    event_start         TIMESTAMPTZ         NOT NULL,
    event_end           TIMESTAMPTZ         NOT NULL,
    status              dr_status_enum      NOT NULL DEFAULT 'pending',
    priority            SMALLINT            DEFAULT 0,  -- Event priority (higher = more urgent)
    test_event          BOOLEAN             DEFAULT FALSE,  -- TRUE = drill / test only
    raw_payload         JSONB,                          -- Full OpenADR XML/JSON payload
    created_at          TIMESTAMPTZ         NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ         NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  dr_events IS 'OpenADR 2.0 demand response events received by the VOLTTRON VEN client agent from the utility VTN.';
COMMENT ON COLUMN dr_events.signal_level    IS 'SIMPLE signal: 0=normal, 1=moderate, 2=high, 3=special. PRICE: $/kWh. LOAD_DISPATCH: kW target.';
COMMENT ON COLUMN dr_events.event_reference IS 'OpenADR eiEventID — globally unique event identifier from the VTN.';
COMMENT ON COLUMN dr_events.test_event      IS 'TRUE for utility test/drill events that should not trigger real curtailment.';

CREATE TRIGGER trg_dr_events_updated_at
    BEFORE UPDATE ON dr_events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE INDEX IF NOT EXISTS idx_dre_status     ON dr_events(status);
CREATE INDEX IF NOT EXISTS idx_dre_start_end  ON dr_events(event_start, event_end);

-- -------------------------------------------------------------
-- TABLE: dr_event_participants
-- Links each DR event to the specific homes that participate,
-- and records their baseline and actual reduction for settlement.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dr_event_participants (
    id                      SERIAL          PRIMARY KEY,
    event_id                INTEGER         NOT NULL REFERENCES dr_events(event_id)  ON DELETE CASCADE,
    home_id                 INTEGER         NOT NULL REFERENCES homes(home_id)        ON DELETE CASCADE,
    opted_in                BOOLEAN         NOT NULL DEFAULT TRUE,       -- Home opted in to this event
    baseline_kw             NUMERIC(10,3),                               -- Pre-event baseline power (kW)
    actual_reduction_kw     NUMERIC(10,3),                               -- Measured demand reduction achieved
    reduction_target_kw     NUMERIC(10,3),                               -- Home-level reduction target
    settlement_kwh          NUMERIC(12,6),                               -- kWh credited for settlement
    performance_score       NUMERIC(5,4),                                -- 0.0–1.0 curtailment performance
    notes                   TEXT,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (event_id, home_id)
);

COMMENT ON TABLE  dr_event_participants IS 'Per-home participation record for each DR event, used for utility settlement and performance reporting.';

CREATE INDEX IF NOT EXISTS idx_drep_event   ON dr_event_participants(event_id);
CREATE INDEX IF NOT EXISTS idx_drep_home    ON dr_event_participants(home_id);

-- -------------------------------------------------------------
-- TABLE: control_actions
-- Audit log of every command issued by the ILC agent or other
-- agents (DR, schedule, manual override).
-- This is the definitive record of what was commanded and when.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS control_actions (
    action_id               BIGSERIAL               NOT NULL,
    home_id                 INTEGER                 NOT NULL REFERENCES homes(home_id)               ON DELETE CASCADE,
    device_id               INTEGER                 REFERENCES devices(device_id),
    circuit_id              INTEGER                 REFERENCES panel_circuits(circuit_id),
    event_id                INTEGER                 REFERENCES dr_events(event_id),    -- NULL if not DR-driven
    ts                      TIMESTAMPTZ             NOT NULL DEFAULT NOW(),

    action_type             action_type_enum        NOT NULL,
    triggered_by            trigger_source_enum     NOT NULL,

    -- ILC scoring context
    ilc_priority_score      NUMERIC(8,4),           -- AHP composite score at time of action
    ilc_demand_target_kw    NUMERIC(10,3),           -- ILC demand target at time of action
    ilc_current_demand_kw   NUMERIC(10,3),           -- Measured building demand at time of action

    -- Command/response detail
    command_payload         JSONB,                  -- Exact API command sent to device
    response_payload        JSONB,                  -- Device acknowledgement / response

    -- Outcome
    success                 BOOLEAN,
    error_msg               TEXT,
    acknowledged_at         TIMESTAMPTZ,            -- When device ACK was received

    created_at              TIMESTAMPTZ             NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  control_actions IS 'Full audit trail of all commands issued by VOLTTRON ILC agent, DR agent, scheduler, or manual operator.';
COMMENT ON COLUMN control_actions.ilc_priority_score  IS 'ILC AHP composite priority score that determined this device was selected for curtailment.';
COMMENT ON COLUMN control_actions.command_payload      IS 'Exact JSON/MQTT command payload sent to the device API.';
COMMENT ON COLUMN control_actions.triggered_by         IS 'Source agent that initiated this control action.';

SELECT create_hypertable('control_actions', 'ts',
    chunk_time_interval => INTERVAL '30 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_ca_home_ts    ON control_actions(home_id,   ts DESC);
CREATE INDEX IF NOT EXISTS idx_ca_device_ts  ON control_actions(device_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ca_event      ON control_actions(event_id);
CREATE INDEX IF NOT EXISTS idx_ca_type       ON control_actions(action_type);
CREATE INDEX IF NOT EXISTS idx_ca_trigger    ON control_actions(triggered_by);

-- -------------------------------------------------------------
-- TABLE: control_advisories
-- Shadow-mode recommendation log for the HVAC supervisor (MPC / RBC).
-- These are setpoint recommendations that were COMPUTED but NOT sent to any
-- device. Kept separate from control_actions (the audit trail of commands
-- actually ISSUED) so that: (a) the shadow log does not pollute the real
-- command history, (b) control_actions.success keeps its meaning -- the
-- device accepted the command -- and (c) a future real-actuation path can
-- write control_actions unchanged. The fields the supervisor reports on
-- (setpoints, scenario, expected cost/energy) are promoted to columns; the
-- full payload is retained in `detail`.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS control_advisories (
    advisory_id             BIGSERIAL           NOT NULL,
    home_id                 INTEGER             NOT NULL REFERENCES homes(home_id)          ON DELETE CASCADE,
    device_id               INTEGER             REFERENCES devices(device_id),
    circuit_id              INTEGER             REFERENCES panel_circuits(circuit_id),  -- NULL for thermostat/whole-home advisories
    event_id                INTEGER             REFERENCES dr_events(event_id),         -- set for DR-driven RBC advisories
    ts                      TIMESTAMPTZ         NOT NULL DEFAULT NOW(),                 -- when the advisory was generated

    -- What produced it
    controller              VARCHAR(8)          NOT NULL CHECK (controller IN ('mpc','rbc')),
    action_type             action_type_enum    NOT NULL,
    triggered_by            trigger_source_enum NOT NULL,
    operation_scenario      VARCHAR(40),                -- normal | load_management_tou | load_management_dr | load_management_capacity | capacity_management | resiliency
    scenario_source         VARCHAR(16),                -- 'explicit' | 'auto'
    shadow_mode             BOOLEAN             NOT NULL DEFAULT TRUE,

    -- The recommendation: baseline vs. recommended thermostat setpoints (deg C)
    baseline_cool_setpoint_c    NUMERIC(5,2),
    baseline_heat_setpoint_c    NUMERIC(5,2),
    recommended_cool_setpoint_c NUMERIC(5,2),
    recommended_heat_setpoint_c NUMERIC(5,2),

    -- MPC expected-outcome estimates (NULL for RBC advisories)
    expected_cost_usd               NUMERIC(10,4),
    expected_energy_kwh             NUMERIC(10,4),
    comfort_violation_degc_steps    NUMERIC(10,4),
    solver                          VARCHAR(16),

    -- Optimization horizon context
    horizon_steps           SMALLINT,
    dt_s                    INTEGER,

    detail                  JSONB,                      -- full advisory payload (prices, forecast linkage, RBC band, etc.)
    created_at              TIMESTAMPTZ         NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  control_advisories IS 'Shadow-mode HVAC setpoint recommendations (MPC/RBC) computed but not sent to any device. Separate from control_actions, the issued-command audit trail.';
COMMENT ON COLUMN control_advisories.shadow_mode IS 'TRUE = recommendation only, no device command issued. Always TRUE while the supervisor runs in advisory mode.';
COMMENT ON COLUMN control_advisories.detail      IS 'Full advisory payload; the first-class columns above are promoted from this for querying.';

SELECT create_hypertable('control_advisories', 'ts',
    chunk_time_interval => INTERVAL '30 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_cadv_home_ts    ON control_advisories(home_id,   ts DESC);
CREATE INDEX IF NOT EXISTS idx_cadv_device_ts  ON control_advisories(device_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_cadv_event      ON control_advisories(event_id);
CREATE INDEX IF NOT EXISTS idx_cadv_controller ON control_advisories(controller);
CREATE INDEX IF NOT EXISTS idx_cadv_scenario   ON control_advisories(operation_scenario);

SELECT add_retention_policy('control_advisories', INTERVAL '90 days', if_not_exists => TRUE);
