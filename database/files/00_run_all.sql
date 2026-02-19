-- =============================================================
-- FILE: 00_run_all.sql
-- DESC: Master script — runs all schema files in dependency order.
--       Execute this file as a superuser or schema-owner role.
--
-- Usage (psql):
--   psql -U <user> -d <database> -f 00_run_all.sql
--
-- Or from shell:
--   for f in 0{1..7}_*.sql; do psql -U <user> -d <database> -f "$f"; done
-- =============================================================

\echo '=== [1/7] Core reference tables (homes, devices) ==='
\i 01_core_reference.sql

\echo '=== [2/7] Smart Panel tables ==='
\i 02_smart_panel.sql

\echo '=== [3/7] Battery tables ==='
\i 03_battery.sql

\echo '=== [4/7] Thermostat (Ecobee) tables ==='
\i 04_thermostat.sql

\echo '=== [5/7] Smart Plug (Kasa KP125M) tables ==='
\i 05_smart_plug.sql

\echo '=== [6/7] Demand Response & Control Action tables ==='
\i 06_demand_response_control.sql

\echo '=== [7/7] Aggregation & Reporting tables ==='
\i 07_aggregations.sql

\echo '=== Schema creation complete ==='

-- -------------------------------------------------------------
-- Quick verification: list all created tables and views
-- -------------------------------------------------------------
SELECT
    schemaname,
    tablename   AS object_name,
    'table'     AS object_type
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
        'homes', 'devices',
        'smart_panel_readings', 'panel_circuits', 'panel_circuit_readings',
        'battery_readings',
        'thermostat_readings', 'thermostat_runtime',
        'smart_plug_readings',
        'dr_events', 'dr_event_participants', 'control_actions',
        'hourly_energy_summary', 'daily_home_summary'
  )

UNION ALL

SELECT
    schemaname,
    matviewname AS object_name,
    'materialized_view' AS object_type
FROM pg_matviews
WHERE schemaname = 'public'
  AND matviewname = 'fleet_daily_summary'

ORDER BY object_type, object_name;
