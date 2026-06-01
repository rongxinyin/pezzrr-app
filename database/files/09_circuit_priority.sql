-- =============================================================
-- 09_circuit_priority.sql
-- Three-tier load priority for the ILC scenario controller.
--
-- The original panel_circuits schema carried two booleans (is_critical,
-- is_controllable). The smart-home ILC operation scenarios need a third
-- middle tier so the supervisor can shed loads in priority order:
--
--   critical      "Must have"   - never shed; the only loads kept under
--                                 grid-resilience islanding.
--   essential     "Nice to have"- shed only under grid resilience (after the
--                                 non-essential tier), kept under load mgmt.
--   non_essential "Non-priority"- first to shed under load management /
--                                 capacity / DR events.
--
-- is_critical is retained (other code reads it); critical-tier rows are kept
-- consistent with is_critical via the backfill below and a trigger is NOT
-- added -- circuit_priority is the authoritative field for the ILC going
-- forward. is_controllable still gates whether the ILC may touch a circuit at
-- all (a critical circuit is non-controllable by definition).
-- =============================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'circuit_priority_enum') THEN
        CREATE TYPE circuit_priority_enum AS ENUM ('critical', 'essential', 'non_essential');
    END IF;
END$$;

ALTER TABLE panel_circuits
    ADD COLUMN IF NOT EXISTS circuit_priority circuit_priority_enum
        NOT NULL DEFAULT 'non_essential';

COMMENT ON COLUMN panel_circuits.circuit_priority IS
    'ILC load-shed tier: critical (Must have, never shed / kept under islanding), essential (Nice to have, shed only under grid resilience), non_essential (Non-priority, shed first under load management). Authoritative priority for the scenario controller.';

-- Keep the new tier consistent with the legacy boolean: any circuit already
-- flagged critical becomes the critical tier.
UPDATE panel_circuits SET circuit_priority = 'critical' WHERE is_critical = TRUE;

-- -------------------------------------------------------------
-- control_advisories: allow the full-home ILC scenario controller alongside
-- the existing per-device mpc / rbc controllers.
-- -------------------------------------------------------------
ALTER TABLE control_advisories DROP CONSTRAINT IF EXISTS control_advisories_controller_check;
ALTER TABLE control_advisories
    ADD CONSTRAINT control_advisories_controller_check
        CHECK (controller IN ('mpc', 'rbc', 'ilc'));
