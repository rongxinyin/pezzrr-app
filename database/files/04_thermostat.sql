-- =============================================================
-- FILE: 04_thermostat.sql
-- DESC: Ecobee thermostat readings and HVAC runtime intervals.
-- DEPENDS ON: 01_core_reference.sql
-- =============================================================

-- -------------------------------------------------------------
-- TABLE: thermostat_readings
-- Real-time thermostat state polled via Ecobee API.
-- Typical resolution: 5-minute intervals (Ecobee thermostat
-- reports data in 5-min intervals; ILC may query more often).
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS thermostat_readings (
    id                      BIGSERIAL       NOT NULL,
    device_id               INTEGER         NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    home_id                 INTEGER         NOT NULL REFERENCES homes(home_id)     ON DELETE CASCADE,
    ts                      TIMESTAMPTZ     NOT NULL,

    -- Temperature & Humidity
    indoor_temp_c           NUMERIC(6,2),           -- Indoor temperature (°C)
    outdoor_temp_c          NUMERIC(6,2),           -- Outdoor temperature (°C), from Ecobee weather
    indoor_humidity_pct     NUMERIC(5,2),           -- Indoor relative humidity (%)

    -- Setpoints
    heat_setpoint_c         NUMERIC(6,2),           -- Heating setpoint (°C)
    cool_setpoint_c         NUMERIC(6,2),           -- Cooling setpoint (°C)

    -- Operating state
    hvac_mode               VARCHAR(20),            -- 'heat' | 'cool' | 'auto' | 'off'
    hvac_state              VARCHAR(20),            -- 'heating' | 'cooling' | 'fan' | 'idle'
    fan_mode                VARCHAR(20),            -- 'auto' | 'on'
    stage                   SMALLINT,               -- Compressor stage (1 or 2 for two-stage units)

    -- Occupancy & comfort
    occupancy_status        VARCHAR(20),            -- 'home' | 'away' | 'sleep'
    eco_mode_active         BOOLEAN,                -- Eco+ / SmartAway active
    hold_type               VARCHAR(30),            -- 'indefinite' | 'nextTransition' | 'dateTime'
    hold_until              TIMESTAMPTZ,            -- Expiry of temporary hold, if applicable

    -- Setpoint adjustments made by ILC / DR agent
    ilc_override_active     BOOLEAN         DEFAULT FALSE,
    ilc_heat_setpoint_c     NUMERIC(6,2),           -- ILC-commanded heat setpoint (if override active)
    ilc_cool_setpoint_c     NUMERIC(6,2),           -- ILC-commanded cool setpoint (if override active)

    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  thermostat_readings IS 'Ecobee thermostat state snapshots collected via Ecobee REST API by the VOLTTRON Ecobee agent.';
COMMENT ON COLUMN thermostat_readings.hvac_state         IS 'Actual HVAC equipment operating state, distinct from the mode setting.';
COMMENT ON COLUMN thermostat_readings.ilc_override_active IS 'TRUE when the ILC agent has applied a demand-response setpoint adjustment.';
COMMENT ON COLUMN thermostat_readings.stage              IS 'Active compressor stage; used by ILC AHP for curtailment priority scoring.';

SELECT create_hypertable('thermostat_readings', 'ts',
    chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_tr_device_ts ON thermostat_readings(device_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_tr_home_ts   ON thermostat_readings(home_id,   ts DESC);
CREATE INDEX IF NOT EXISTS idx_tr_hvac_mode ON thermostat_readings(hvac_mode);

-- -------------------------------------------------------------
-- TABLE: thermostat_runtime
-- HVAC equipment runtime intervals from Ecobee runtime reports.
-- Ecobee provides these in 5-minute interval buckets.
-- Used for energy billing analysis, equipment wear, and DR baselines.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS thermostat_runtime (
    id                  BIGSERIAL       NOT NULL,
    device_id           INTEGER         NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    home_id             INTEGER         NOT NULL REFERENCES homes(home_id)     ON DELETE CASCADE,
    interval_start      TIMESTAMPTZ     NOT NULL,   -- Start of reporting interval
    interval_end        TIMESTAMPTZ     NOT NULL,   -- End of reporting interval

    -- Runtime seconds within interval (max = interval length)
    heat_runtime_s      INTEGER         NOT NULL DEFAULT 0,     -- Compressor heating runtime
    cool_runtime_s      INTEGER         NOT NULL DEFAULT 0,     -- Compressor cooling runtime
    fan_runtime_s       INTEGER         NOT NULL DEFAULT 0,     -- Fan-only runtime
    aux_runtime_s       INTEGER         NOT NULL DEFAULT 0,     -- Auxiliary / emergency heat runtime
    heat_stage2_s       INTEGER         NOT NULL DEFAULT 0,     -- Stage 2 heating runtime
    cool_stage2_s       INTEGER         NOT NULL DEFAULT 0,     -- Stage 2 cooling runtime

    -- Energy estimate (optional — requires rated power from devices.metadata)
    estimated_energy_kwh NUMERIC(10,6),

    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    UNIQUE (device_id, interval_start)
);

COMMENT ON TABLE  thermostat_runtime IS 'Ecobee 5-minute HVAC equipment runtime data; used for energy attribution, wear tracking, and DR baseline calculations.';

SELECT create_hypertable('thermostat_runtime', 'interval_start',
    chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_trt_device_ts ON thermostat_runtime(device_id, interval_start DESC);
CREATE INDEX IF NOT EXISTS idx_trt_home_ts   ON thermostat_runtime(home_id,   interval_start DESC);
