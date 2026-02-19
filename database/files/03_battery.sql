-- =============================================================
-- FILE: 03_battery.sql
-- DESC: EcoFlow DELTA Pro Ultra / DELTA Pro battery readings.
-- DEPENDS ON: 01_core_reference.sql
-- =============================================================

-- -------------------------------------------------------------
-- TABLE: battery_readings
-- Telemetry for each EcoFlow battery unit (DELTA Pro Ultra or
-- DELTA Pro) collected via EcoFlow MQTT or HTTP API.
-- One row per device per polling interval (~1 min or on change).
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS battery_readings (
    id                      BIGSERIAL       NOT NULL,
    device_id               INTEGER         NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    home_id                 INTEGER         NOT NULL REFERENCES homes(home_id)     ON DELETE CASCADE,
    ts                      TIMESTAMPTZ     NOT NULL,

    -- State
    soc_pct                 NUMERIC(5,2),           -- State of charge (0–100 %)
    soh_pct                 NUMERIC(5,2),           -- State of health (0–100 %)
    status                  VARCHAR(50),            -- 'charging' | 'discharging' | 'standby' | 'error'

    -- Electrical
    voltage_v               NUMERIC(8,3),           -- Pack terminal voltage (V)
    current_a               NUMERIC(8,3),           -- Pack current (A), + = charging
    power_w                 NUMERIC(10,3),          -- Net pack power (W), + = charging
    temp_c                  NUMERIC(6,2),           -- Battery temperature (°C)

    -- Capacity
    capacity_wh             NUMERIC(10,3),          -- Usable capacity at current SoH (Wh)
    cycles                  INTEGER,                -- Full charge cycle count

    -- Power port measurements
    ac_in_power_w           NUMERIC(10,3),          -- AC input power (W) — grid/wall charging
    ac_out_power_w          NUMERIC(10,3),          -- AC output power (W) — powering home loads
    dc_out_power_w          NUMERIC(10,3),          -- DC output power (W)
    solar_in_power_w        NUMERIC(10,3),          -- Solar (PV) input power (W)

    -- Mode
    charge_mode             VARCHAR(50),            -- 'AC' | 'Solar' | 'AC+Solar'

    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  battery_readings IS 'Per-battery telemetry from EcoFlow DELTA Pro Ultra / DELTA Pro units polled via EcoFlow IoT API.';
COMMENT ON COLUMN battery_readings.soc_pct     IS 'State of charge percentage (0–100). Primary signal for ILC battery dispatch decisions.';
COMMENT ON COLUMN battery_readings.current_a   IS 'Positive = charging; negative = discharging.';
COMMENT ON COLUMN battery_readings.power_w     IS 'Net battery power. Positive = charging; negative = discharging.';
COMMENT ON COLUMN battery_readings.cycles      IS 'Cumulative full equivalent charge cycles; used for SoH monitoring.';

SELECT create_hypertable('battery_readings', 'ts',
    chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_br_device_ts ON battery_readings(device_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_br_home_ts   ON battery_readings(home_id,   ts DESC);
CREATE INDEX IF NOT EXISTS idx_br_status    ON battery_readings(status);
