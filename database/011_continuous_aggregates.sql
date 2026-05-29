-- =====================================================================
-- 011_continuous_aggregates.sql
-- TimescaleDB continuous aggregates that speed up multi-day telemetry
-- reads for the dashboard API (docs/DASHBOARD_DESIGN.md §19).
--
-- API bucket routing:
--   bucket=1m  + range <= 48h -> raw smart_panel_readings / panel_circuit_readings
--   bucket=5m                 -> panel_5m / circuit_5m
--   bucket=1h  / long ranges  -> panel_1h
--
-- Idempotent: views use IF NOT EXISTS, policies use if_not_exists => TRUE.
-- Continuous-aggregate DDL cannot run inside a transaction block; run this
-- file with psql (statement-at-a-time), not wrapped in BEGIN/COMMIT.
-- =====================================================================

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
  schedule_interval => INTERVAL '5 minutes',
  if_not_exists => TRUE);

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
  schedule_interval => INTERVAL '1 hour',
  if_not_exists => TRUE);

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
  schedule_interval => INTERVAL '5 minutes',
  if_not_exists => TRUE);
