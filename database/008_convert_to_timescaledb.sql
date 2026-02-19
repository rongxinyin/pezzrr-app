-- ============================================================================
-- 008_convert_to_timescaledb.sql
-- Convert time-series reading tables to TimescaleDB hypertables.
-- TimescaleDB requires all unique constraints to include the partition column.
-- We drop the BIGSERIAL PKs and re-create composite PKs with (id, ts).
-- Run as: psql -U ryin -d pezerr_db -f 008_convert_to_timescaledb.sql
-- ============================================================================

\echo '=== Enabling TimescaleDB ==='
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================================================
-- 1. smart_panel_readings  (partition on ts)
-- ============================================================================
\echo '--- Converting smart_panel_readings ---'

ALTER TABLE smart_panel_readings DROP CONSTRAINT smart_panel_readings_pkey;
ALTER TABLE smart_panel_readings ADD PRIMARY KEY (id, ts);

SELECT create_hypertable(
    'smart_panel_readings', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists       => TRUE,
    migrate_data        => TRUE
);

-- ============================================================================
-- 2. panel_circuit_readings  (partition on ts)
-- ============================================================================
\echo '--- Converting panel_circuit_readings ---'

ALTER TABLE panel_circuit_readings DROP CONSTRAINT panel_circuit_readings_pkey;
ALTER TABLE panel_circuit_readings ADD PRIMARY KEY (id, ts);

SELECT create_hypertable(
    'panel_circuit_readings', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists       => TRUE,
    migrate_data        => TRUE
);

-- ============================================================================
-- 3. battery_readings  (partition on ts)
-- ============================================================================
\echo '--- Converting battery_readings ---'

ALTER TABLE battery_readings DROP CONSTRAINT battery_readings_pkey;
ALTER TABLE battery_readings ADD PRIMARY KEY (id, ts);

SELECT create_hypertable(
    'battery_readings', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists       => TRUE,
    migrate_data        => TRUE
);

-- ============================================================================
-- 4. thermostat_readings  (partition on ts)
-- ============================================================================
\echo '--- Converting thermostat_readings ---'

ALTER TABLE thermostat_readings DROP CONSTRAINT thermostat_readings_pkey;
ALTER TABLE thermostat_readings ADD PRIMARY KEY (id, ts);

SELECT create_hypertable(
    'thermostat_readings', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists       => TRUE,
    migrate_data        => TRUE
);

-- ============================================================================
-- 5. thermostat_runtime  (partition on interval_start)
-- ============================================================================
\echo '--- Converting thermostat_runtime ---'

ALTER TABLE thermostat_runtime DROP CONSTRAINT thermostat_runtime_pkey;
ALTER TABLE thermostat_runtime ADD PRIMARY KEY (id, interval_start);

-- Unique constraint already includes interval_start via (device_id, interval_start)
-- but we need to make it hypertable-compatible
ALTER TABLE thermostat_runtime DROP CONSTRAINT thermostat_runtime_device_id_interval_start_key;
ALTER TABLE thermostat_runtime ADD CONSTRAINT thermostat_runtime_device_interval
    UNIQUE (device_id, interval_start);

SELECT create_hypertable(
    'thermostat_runtime', 'interval_start',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists       => TRUE,
    migrate_data        => TRUE
);

-- ============================================================================
-- 6. smart_plug_readings  (partition on ts)
-- ============================================================================
\echo '--- Converting smart_plug_readings ---'

ALTER TABLE smart_plug_readings DROP CONSTRAINT smart_plug_readings_pkey;
ALTER TABLE smart_plug_readings ADD PRIMARY KEY (id, ts);

SELECT create_hypertable(
    'smart_plug_readings', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists       => TRUE,
    migrate_data        => TRUE
);

-- ============================================================================
-- 7. control_actions  (partition on ts)
-- ============================================================================
\echo '--- Converting control_actions ---'

ALTER TABLE control_actions DROP CONSTRAINT control_actions_pkey;
ALTER TABLE control_actions ADD PRIMARY KEY (action_id, ts);

SELECT create_hypertable(
    'control_actions', 'ts',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists       => TRUE,
    migrate_data        => TRUE
);

-- ============================================================================
-- 8. hourly_energy_summary  (partition on hour_start)
-- ============================================================================
\echo '--- Converting hourly_energy_summary ---'

ALTER TABLE hourly_energy_summary DROP CONSTRAINT hourly_energy_summary_pkey;
ALTER TABLE hourly_energy_summary ADD PRIMARY KEY (id, hour_start);

-- Unique constraint must include partition column — already does
ALTER TABLE hourly_energy_summary DROP CONSTRAINT hourly_energy_summary_device_id_hour_start_key;
ALTER TABLE hourly_energy_summary ADD CONSTRAINT hourly_energy_summary_device_hour
    UNIQUE (device_id, hour_start);

SELECT create_hypertable(
    'hourly_energy_summary', 'hour_start',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists       => TRUE,
    migrate_data        => TRUE
);

-- ============================================================================
-- Verification
-- ============================================================================
\echo ''
\echo '=== TimescaleDB Hypertables ==='
SELECT
    hypertable_name,
    num_dimensions,
    num_chunks
FROM timescaledb_information.hypertables
ORDER BY hypertable_name;

\echo ''
\echo '=== Chunk Time Intervals ==='
SELECT
    hypertable_name,
    column_name,
    time_interval
FROM timescaledb_information.dimensions
ORDER BY hypertable_name;
