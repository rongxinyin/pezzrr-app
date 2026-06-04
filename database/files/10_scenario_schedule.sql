-- =============================================================
-- FILE: 10_scenario_schedule.sql
-- DESC: Operator-set operation-scenario calendar for the smart-home ILC.
--       One scenario per home per day; the Scenarios dashboard page writes
--       these from a calendar UI and dispatches the resolved scenario
--       (panel battery mode + thermostat band-widen) via control_actions.
--
--       This is the *intended* schedule (what the operator wants on a given
--       day). The controller's auto-detection (mpc_config scenarios.auto)
--       still resolves the live scenario each cycle; this table lets an
--       operator pin a day to an explicit scenario instead of 'auto'.
-- =============================================================

CREATE TABLE IF NOT EXISTS scenario_schedule (
    schedule_id        BIGSERIAL    PRIMARY KEY,
    home_id            INTEGER      NOT NULL REFERENCES homes(home_id) ON DELETE CASCADE,
    scenario_date      DATE         NOT NULL,
    operation_scenario VARCHAR(40)  NOT NULL
        CHECK (operation_scenario IN
               ('normal','load_peak_management','capacity_management','resiliency')),
    note               TEXT,
    created_by         VARCHAR(80),                       -- dashboard username that set it
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (home_id, scenario_date)
);

COMMENT ON TABLE  scenario_schedule IS 'Operator-set per-home, per-day operation scenario for the smart-home ILC. Drives the Scenarios dashboard calendar; an explicit override of the controller auto-detection for that day.';
COMMENT ON COLUMN scenario_schedule.operation_scenario IS 'normal | load_peak_management | capacity_management | resiliency';
COMMENT ON COLUMN scenario_schedule.created_by IS 'Dashboard username that set / last updated the entry.';

CREATE INDEX IF NOT EXISTS idx_scenario_sched_home_date
    ON scenario_schedule(home_id, scenario_date);
