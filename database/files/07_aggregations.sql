-- =============================================================
-- FILE: 07_aggregations.sql
-- DESC: Hourly and daily rollup tables for dashboards, billing
--       reports, and fleet-level analytics.
--       Populated by pg_cron scheduled job or nightly batch.
-- DEPENDS ON: 01_core_reference.sql through 06_demand_response_control.sql
-- =============================================================

-- -------------------------------------------------------------
-- TABLE: hourly_energy_summary
-- Per-device, per-hour energy rollup.
-- Populated by scheduled job or materialized view refresh.
-- Powers hourly trend dashboards and heatmaps.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hourly_energy_summary (
    id              BIGSERIAL           NOT NULL,
    home_id         INTEGER             NOT NULL REFERENCES homes(home_id)   ON DELETE CASCADE,
    device_id       INTEGER             NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    device_type     device_type_enum    NOT NULL,
    hour_start      TIMESTAMPTZ         NOT NULL,   -- Truncated to whole hour (UTC)

    -- Power statistics
    avg_power_w     NUMERIC(12,4),
    max_power_w     NUMERIC(12,4),
    min_power_w     NUMERIC(12,4),
    p95_power_w     NUMERIC(12,4),          -- 95th percentile power (peak demand proxy)

    -- Energy
    energy_kwh      NUMERIC(12,6),          -- Total energy for the hour

    -- Data quality
    reading_count   INTEGER,                -- Number of raw readings in this hour
    expected_count  INTEGER,                -- Expected readings (for gap detection)
    coverage_pct    NUMERIC(5,2),           -- reading_count / expected_count * 100

    created_at      TIMESTAMPTZ         NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ         NOT NULL DEFAULT NOW(),

    UNIQUE (device_id, hour_start)
);

COMMENT ON TABLE  hourly_energy_summary IS 'Hourly per-device energy rollup for dashboards and trend analysis. Populated by scheduled batch job.';
COMMENT ON COLUMN hourly_energy_summary.p95_power_w  IS '95th percentile power within the hour — better peak indicator than max for noisy signals.';
COMMENT ON COLUMN hourly_energy_summary.coverage_pct IS 'Data completeness: 100% = no gaps, <80% = flag for data quality review.';

SELECT create_hypertable('hourly_energy_summary', 'hour_start',
    chunk_time_interval => INTERVAL '30 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_hes_home_hour   ON hourly_energy_summary(home_id,   hour_start DESC);
CREATE INDEX IF NOT EXISTS idx_hes_device_hour ON hourly_energy_summary(device_id, hour_start DESC);
CREATE INDEX IF NOT EXISTS idx_hes_type_hour   ON hourly_energy_summary(device_type, hour_start DESC);

-- -------------------------------------------------------------
-- TABLE: daily_home_summary
-- Per-home, per-day rollup of all energy flows, AC runtime,
-- and DR participation.
-- Powers billing dashboards, monthly reports, and fleet comparisons.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_home_summary (
    id                          BIGSERIAL       PRIMARY KEY,
    home_id                     INTEGER         NOT NULL REFERENCES homes(home_id) ON DELETE CASCADE,
    date                        DATE            NOT NULL,

    -- Grid energy flows (kWh)
    grid_import_kwh             NUMERIC(12,4)   DEFAULT 0,   -- Energy drawn from utility grid
    grid_export_kwh             NUMERIC(12,4)   DEFAULT 0,   -- Energy exported to grid (net metering)

    -- Solar generation (kWh)
    solar_gen_kwh               NUMERIC(12,4)   DEFAULT 0,

    -- Battery (kWh)
    battery_charge_kwh          NUMERIC(12,4)   DEFAULT 0,
    battery_discharge_kwh       NUMERIC(12,4)   DEFAULT 0,
    battery_soc_start_pct       NUMERIC(5,2),                -- SoC at midnight start of day
    battery_soc_end_pct         NUMERIC(5,2),                -- SoC at end of day

    -- Home load
    home_load_kwh               NUMERIC(12,4)   DEFAULT 0,   -- Total home consumption
    self_consumption_pct        NUMERIC(5,2),                -- Solar self-consumed / solar generated

    -- Peak demand
    peak_demand_kw              NUMERIC(10,4),               -- Highest 15-min average demand
    peak_demand_at              TIMESTAMPTZ,                 -- Timestamp of peak demand

    -- HVAC (from Ecobee runtime)
    ac_runtime_s                INTEGER         DEFAULT 0,   -- Total compressor runtime (seconds)
    heat_runtime_s              INTEGER         DEFAULT 0,
    fan_runtime_s               INTEGER         DEFAULT 0,
    ac_energy_kwh               NUMERIC(12,4)   DEFAULT 0,   -- Estimated HVAC energy

    -- Smart plug aggregate
    plug_energy_kwh             NUMERIC(12,4)   DEFAULT 0,   -- Total energy through all smart plugs
    plug_curtail_count          INTEGER         DEFAULT 0,   -- Number of ILC plug-off events

    -- Demand response
    dr_events_count             SMALLINT        DEFAULT 0,   -- DR events this day
    dr_reduction_kwh            NUMERIC(12,4)   DEFAULT 0,   -- Total kWh reduced via DR
    dr_performance_score        NUMERIC(5,4),                -- Average performance score for the day

    -- Cost (if utility rate is known)
    estimated_cost_usd          NUMERIC(10,4),               -- Estimated electricity cost
    estimated_savings_usd       NUMERIC(10,4),               -- Estimated savings vs. no-DR baseline

    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    UNIQUE (home_id, date)
);

COMMENT ON TABLE  daily_home_summary IS 'Daily per-home energy, DR, and cost summary. Primary table for billing reports and fleet-level comparisons.';
COMMENT ON COLUMN daily_home_summary.self_consumption_pct IS 'Solar self-consumption ratio = (solar - export) / solar. Higher = less reliance on grid.';
COMMENT ON COLUMN daily_home_summary.peak_demand_kw       IS '15-minute rolling average peak — aligns with typical utility demand charge calculation.';

CREATE TRIGGER trg_dhs_updated_at
    BEFORE UPDATE ON daily_home_summary
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE INDEX IF NOT EXISTS idx_dhs_home_date ON daily_home_summary(home_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_dhs_date      ON daily_home_summary(date DESC);

-- -------------------------------------------------------------
-- MATERIALIZED VIEW: fleet_daily_summary
-- Cross-home fleet-level aggregation.
-- Refresh nightly after daily_home_summary is populated.
-- -------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS fleet_daily_summary AS
SELECT
    date,
    COUNT(DISTINCT home_id)                 AS homes_reporting,
    SUM(grid_import_kwh)                    AS total_grid_import_kwh,
    SUM(grid_export_kwh)                    AS total_grid_export_kwh,
    SUM(solar_gen_kwh)                      AS total_solar_gen_kwh,
    SUM(home_load_kwh)                      AS total_home_load_kwh,
    AVG(peak_demand_kw)                     AS avg_peak_demand_kw,
    MAX(peak_demand_kw)                     AS max_peak_demand_kw,
    SUM(dr_reduction_kwh)                   AS total_dr_reduction_kwh,
    AVG(dr_performance_score)               AS avg_dr_performance,
    SUM(dr_events_count)                    AS total_dr_events,
    SUM(estimated_cost_usd)                 AS total_estimated_cost_usd,
    SUM(estimated_savings_usd)              AS total_estimated_savings_usd,
    AVG(self_consumption_pct)               AS avg_self_consumption_pct,
    AVG(battery_soc_end_pct)               AS avg_battery_soc_eod
FROM  daily_home_summary
GROUP BY date
ORDER BY date DESC
WITH DATA;

COMMENT ON MATERIALIZED VIEW fleet_daily_summary IS 'Fleet-wide daily KPI rollup across all homes. Refresh with: REFRESH MATERIALIZED VIEW fleet_daily_summary;';

CREATE UNIQUE INDEX IF NOT EXISTS idx_fds_date ON fleet_daily_summary(date);

-- -------------------------------------------------------------
-- pg_cron job example (run after installing pg_cron extension)
-- Schedule: nightly at 00:30 UTC to roll up yesterday's data.
-- Uncomment and adapt to your environment.
-- -------------------------------------------------------------
-- SELECT cron.schedule(
--     'nightly-rollup',
--     '30 0 * * *',
--     $$
--       REFRESH MATERIALIZED VIEW CONCURRENTLY fleet_daily_summary;
--     $$
-- );
