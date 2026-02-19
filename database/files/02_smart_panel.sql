-- =============================================================
-- FILE: 02_smart_panel.sql
-- DESC: EcoFlow Smart Home Panel 2 — whole-panel telemetry
--       and per-circuit / channel readings.
-- DEPENDS ON: 01_core_reference.sql
-- =============================================================

-- -------------------------------------------------------------
-- TABLE: smart_panel_readings
-- Whole-panel telemetry polled every 1 minute (or faster via MQTT).
-- Grid, solar, battery aggregate, and home load measurements.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS smart_panel_readings (
    id                  BIGSERIAL       NOT NULL,
    device_id           INTEGER         NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    home_id             INTEGER         NOT NULL REFERENCES homes(home_id)     ON DELETE CASCADE,
    ts                  TIMESTAMPTZ     NOT NULL,

    -- Grid measurements
    grid_voltage_v1     NUMERIC(8,3),           -- L1 line voltage (Volts)
    grid_voltage_v2     NUMERIC(8,3),           -- L2 line voltage (Volts)
    grid_frequency_hz   NUMERIC(6,3),           -- Grid frequency (Hz)
    grid_power_w        NUMERIC(10,3),          -- Total grid import (+) / export (-) in Watts
    grid_online         BOOLEAN,                -- TRUE = grid connected

    -- Solar / PV input
    solar_power_w       NUMERIC(10,3),          -- PV generation power (Watts)

    -- Battery aggregate (across all attached packs)
    battery_power_w     NUMERIC(10,3),          -- + = charging, - = discharging (Watts)
    battery_soc_pct     NUMERIC(5,2),           -- Aggregate state of charge (%)
    battery_soh_pct     NUMERIC(5,2),           -- Aggregate state of health (%)

    -- Home load
    home_load_w         NUMERIC(10,3),          -- Total home consumption (Watts)

    -- Operating modes
    eps_mode_active     BOOLEAN,                -- Emergency Power Supply mode
    grid_status         SMALLINT,               -- 0 = offline, 1 = online (raw API field)

    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  smart_panel_readings IS 'Whole-panel telemetry from EcoFlow Smart Home Panel 2, collected via EcoFlow MQTT/HTTP API.';
COMMENT ON COLUMN smart_panel_readings.grid_power_w    IS 'Positive = importing from grid; negative = exporting to grid.';
COMMENT ON COLUMN smart_panel_readings.battery_power_w IS 'Positive = charging batteries; negative = discharging.';
COMMENT ON COLUMN smart_panel_readings.eps_mode_active IS 'TRUE when panel is running in Emergency Power Supply mode (grid lost).';

SELECT create_hypertable('smart_panel_readings', 'ts',
    chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_spr_device_ts ON smart_panel_readings(device_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_spr_home_ts   ON smart_panel_readings(home_id,   ts DESC);

-- -------------------------------------------------------------
-- TABLE: panel_circuits
-- Static configuration for each load branch / circuit of a panel.
-- EcoFlow SHP2 supports up to 12 load branches (channels 0–11).
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS panel_circuits (
    circuit_id          SERIAL          PRIMARY KEY,
    device_id           INTEGER         NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    channel_num         SMALLINT        NOT NULL CHECK (channel_num BETWEEN 0 AND 11),
    circuit_name        VARCHAR(100),               -- "HVAC", "Kitchen", "EV Charger", etc.
    rated_amps          NUMERIC(6,2),               -- Breaker amperage rating
    is_critical         BOOLEAN         NOT NULL DEFAULT FALSE,  -- Cannot be curtailed during DR
    is_controllable     BOOLEAN         NOT NULL DEFAULT TRUE,   -- ILC can control this circuit
    load_description    TEXT,                       -- Free-text description of the load
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (device_id, channel_num)
);

COMMENT ON TABLE  panel_circuits IS 'Static circuit/channel config for each EcoFlow SHP2 load branch.';
COMMENT ON COLUMN panel_circuits.is_critical    IS 'Critical circuits (e.g., medical equipment) are excluded from ILC curtailment.';
COMMENT ON COLUMN panel_circuits.is_controllable IS 'ILC agent will only consider controllable circuits for load shaping.';

CREATE INDEX IF NOT EXISTS idx_pc_device ON panel_circuits(device_id);

-- -------------------------------------------------------------
-- TABLE: panel_circuit_readings
-- Per-circuit power/energy measurements at ~1-minute resolution.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS panel_circuit_readings (
    id                  BIGSERIAL       NOT NULL,
    circuit_id          INTEGER         NOT NULL REFERENCES panel_circuits(circuit_id) ON DELETE CASCADE,
    device_id           INTEGER         NOT NULL REFERENCES devices(device_id)         ON DELETE CASCADE,
    home_id             INTEGER         NOT NULL REFERENCES homes(home_id)             ON DELETE CASCADE,
    ts                  TIMESTAMPTZ     NOT NULL,

    power_w             NUMERIC(10,3),          -- Instantaneous power (Watts)
    current_a           NUMERIC(8,3),           -- Current (Amps)
    voltage_v           NUMERIC(8,3),           -- Voltage (Volts)
    energy_kwh          NUMERIC(12,6),          -- Cumulative or interval energy (kWh)
    is_enabled          BOOLEAN,                -- Channel relay state: TRUE = on

    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  panel_circuit_readings IS 'Per-circuit power and energy readings from EcoFlow SHP2, used by ILC for load priority scoring.';
COMMENT ON COLUMN panel_circuit_readings.is_enabled IS 'Reflects EcoFlow channel enable/disable state as of this reading.';

SELECT create_hypertable('panel_circuit_readings', 'ts',
    chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_pcr_circuit_ts ON panel_circuit_readings(circuit_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_pcr_home_ts    ON panel_circuit_readings(home_id,    ts DESC);
CREATE INDEX IF NOT EXISTS idx_pcr_device_ts  ON panel_circuit_readings(device_id,  ts DESC);
