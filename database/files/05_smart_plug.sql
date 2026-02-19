-- =============================================================
-- FILE: 05_smart_plug.sql
-- DESC: Kasa KP125M smart plug readings (Matter protocol).
-- DEPENDS ON: 01_core_reference.sql
-- =============================================================

-- -------------------------------------------------------------
-- TABLE: smart_plug_readings
-- Power and energy telemetry from Kasa KP125M smart plugs
-- communicated via Matter protocol to the VOLTTRON Kasa agent.
-- Typical polling: 30 seconds to 1 minute.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS smart_plug_readings (
    id                  BIGSERIAL       NOT NULL,
    device_id           INTEGER         NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    home_id             INTEGER         NOT NULL REFERENCES homes(home_id)     ON DELETE CASCADE,
    ts                  TIMESTAMPTZ     NOT NULL,

    -- Power measurements
    power_w             NUMERIC(10,3),          -- Instantaneous real power (Watts)
    voltage_v           NUMERIC(8,3),           -- RMS voltage (Volts)
    current_a           NUMERIC(8,3),           -- RMS current (Amps)
    power_factor        NUMERIC(5,4),           -- Power factor (0.0–1.0)
    apparent_power_va   NUMERIC(10,3),          -- Apparent power (VA)

    -- Energy
    energy_kwh          NUMERIC(12,6),          -- Cumulative energy since last reset (kWh)

    -- Relay / switch state
    relay_state         BOOLEAN,                -- TRUE = outlet energized (ON)

    -- Protection / alerts
    overload_status     BOOLEAN         DEFAULT FALSE,  -- Overload protection triggered
    overcurrent_status  BOOLEAN         DEFAULT FALSE,  -- Overcurrent alert

    -- ILC context
    ilc_curtail_active  BOOLEAN         DEFAULT FALSE,  -- ILC agent has turned off this plug

    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  smart_plug_readings IS 'Kasa KP125M Matter smart plug telemetry collected via VOLTTRON Kasa agent using Matter/Thread protocol.';
COMMENT ON COLUMN smart_plug_readings.relay_state        IS 'TRUE = outlet is ON and delivering power.';
COMMENT ON COLUMN smart_plug_readings.energy_kwh         IS 'Cumulative kWh counter from plug firmware; reset on plug restart or manual clear.';
COMMENT ON COLUMN smart_plug_readings.ilc_curtail_active IS 'Set to TRUE when ILC agent has commanded this plug OFF for demand response.';

SELECT create_hypertable('smart_plug_readings', 'ts',
    chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_spl_device_ts ON smart_plug_readings(device_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_spl_home_ts   ON smart_plug_readings(home_id,   ts DESC);
CREATE INDEX IF NOT EXISTS idx_spl_relay     ON smart_plug_readings(relay_state, ts DESC);
