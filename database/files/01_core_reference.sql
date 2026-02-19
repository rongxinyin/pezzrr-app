-- =============================================================
-- FILE: 01_core_reference.sql
-- DESC: Core reference/metadata tables: homes and devices
-- =============================================================

-- -------------------------------------------------------------
-- EXTENSIONS (run once per database)
-- -------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- -------------------------------------------------------------
-- ENUM TYPES
-- -------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE device_type_enum AS ENUM (
        'smart_panel',
        'battery',
        'thermostat',
        'smart_plug'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- -------------------------------------------------------------
-- TABLE: homes
-- One row per physical home / site enrolled in the system.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS homes (
    home_id                 SERIAL          PRIMARY KEY,
    home_name               VARCHAR(100)    NOT NULL,
    address                 VARCHAR(255),
    city                    VARCHAR(100),
    state                   VARCHAR(50),
    zip_code                VARCHAR(20),
    utility_id              VARCHAR(100),           -- utility account / rate tariff reference
    timezone                VARCHAR(50)     NOT NULL DEFAULT 'America/Los_Angeles',
    gateway_id              VARCHAR(100)    UNIQUE,  -- Raspberry Pi 5 gateway identifier
    volttron_instance_id    VARCHAR(100),
    enrolled_dr             BOOLEAN         NOT NULL DEFAULT FALSE,  -- enrolled in OpenADR program
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  homes IS 'One row per physical home enrolled in the smart energy management system.';
COMMENT ON COLUMN homes.gateway_id           IS 'Unique ID of the Raspberry Pi 5 edge gateway at this home.';
COMMENT ON COLUMN homes.volttron_instance_id IS 'VOLTTRON platform instance identifier running on the gateway.';
COMMENT ON COLUMN homes.enrolled_dr          IS 'TRUE if this home participates in OpenADR demand response.';

-- Trigger: auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_homes_updated_at
    BEFORE UPDATE ON homes
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- -------------------------------------------------------------
-- TABLE: devices
-- One row per physical device installed at a home.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS devices (
    device_id               SERIAL              PRIMARY KEY,
    home_id                 INTEGER             NOT NULL REFERENCES homes(home_id) ON DELETE CASCADE,
    device_type             device_type_enum    NOT NULL,
    device_name             VARCHAR(100),               -- e.g. "Living Room Plug", "Main Panel"
    manufacturer            VARCHAR(100),               -- EcoFlow | Ecobee | Kasa
    model                   VARCHAR(100),               -- SHP2 | SmartThermostat | KP125M
    serial_number           VARCHAR(100),
    api_identifier          VARCHAR(200),               -- EcoFlow SN / Ecobee thermostatId / Matter nodeId
    firmware_version        VARCHAR(50),
    installed_at            TIMESTAMPTZ,
    is_active               BOOLEAN             NOT NULL DEFAULT TRUE,
    metadata                JSONB,                      -- flexible extra fields per device type
    created_at              TIMESTAMPTZ         NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  devices IS 'Physical devices at each home: smart panels, batteries, thermostats, smart plugs.';
COMMENT ON COLUMN devices.api_identifier IS 'Device-specific API key: EcoFlow serial number, Ecobee thermostatId, or Matter node ID.';
COMMENT ON COLUMN devices.metadata       IS 'JSONB bag for device-specific config (rated power, circuit count, etc.).';

CREATE INDEX IF NOT EXISTS idx_devices_home_id ON devices(home_id);
CREATE INDEX IF NOT EXISTS idx_devices_type    ON devices(device_type);
CREATE INDEX IF NOT EXISTS idx_devices_metadata ON devices USING gin(metadata);
