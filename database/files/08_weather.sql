-- =============================================================
-- FILE: 08_weather.sql
-- DESC: Dark Sky weather data: locations, observed conditions
--       (current polls + Time Machine history), and hourly forecasts.
-- DEPENDS ON: 01_core_reference.sql
--
-- All meteorological values are stored in SI units (units=si):
--   temperature/dew point  °C
--   wind speed/gust        m/s
--   pressure               hPa
--   visibility             km
--   precip intensity       mm/h
-- Fractions reported by Dark Sky as 0..1 (humidity, cloudCover,
-- precipProbability) are stored as percentages (0..100).
-- =============================================================

-- -------------------------------------------------------------
-- TABLE: weather_locations
-- One row per lat/lng point we collect weather for. Optionally
-- linked to a home (many homes may share one location).
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather_locations (
    location_id     SERIAL          PRIMARY KEY,
    location_name   VARCHAR(100)    NOT NULL UNIQUE,
    latitude        NUMERIC(9,6)    NOT NULL,
    longitude       NUMERIC(9,6)    NOT NULL,
    home_id         INTEGER         REFERENCES homes(home_id) ON DELETE SET NULL,
    timezone        VARCHAR(50)     NOT NULL DEFAULT 'America/Los_Angeles',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  weather_locations IS 'Lat/lng points polled from the Dark Sky API; optionally tied to a home.';
COMMENT ON COLUMN weather_locations.home_id IS 'Representative home at this location (NULL if not tied to a specific home).';

CREATE INDEX IF NOT EXISTS idx_wl_home_id ON weather_locations(home_id);

-- -------------------------------------------------------------
-- TABLE: weather_observations
-- Observed/actual conditions. Populated both by the live "current
-- conditions" poll (source='current') and by Time Machine backfill
-- of past hours (source='timemachine').
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather_observations (
    id                      BIGSERIAL       NOT NULL,
    location_id             INTEGER         NOT NULL REFERENCES weather_locations(location_id) ON DELETE CASCADE,
    ts                      TIMESTAMPTZ     NOT NULL,           -- observation time (Dark Sky `time`)
    source                  VARCHAR(20)     NOT NULL DEFAULT 'current',  -- 'current' | 'timemachine'

    summary                 VARCHAR(255),
    icon                    VARCHAR(50),

    temp_c                  NUMERIC(6,2),
    apparent_temp_c         NUMERIC(6,2),
    dew_point_c             NUMERIC(6,2),
    humidity_pct            NUMERIC(5,2),
    pressure_hpa            NUMERIC(7,2),
    wind_speed_ms           NUMERIC(6,2),
    wind_gust_ms            NUMERIC(6,2),
    wind_bearing_deg        SMALLINT,
    cloud_cover_pct         NUMERIC(5,2),
    uv_index                SMALLINT,
    visibility_km           NUMERIC(6,2),
    ozone                   NUMERIC(7,2),

    precip_intensity_mmph   NUMERIC(7,3),
    precip_probability_pct  NUMERIC(5,2),
    precip_type             VARCHAR(20),

    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    UNIQUE (location_id, ts)
);

COMMENT ON TABLE  weather_observations IS 'Observed weather conditions per location: live current-condition polls plus Time Machine historical backfill.';
COMMENT ON COLUMN weather_observations.source IS 'How the row was obtained: ''current'' (live forecast poll) or ''timemachine'' (historical backfill).';

SELECT create_hypertable('weather_observations', 'ts',
    chunk_time_interval => INTERVAL '30 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_wo_location_ts ON weather_observations(location_id, ts DESC);

-- -------------------------------------------------------------
-- TABLE: weather_forecast
-- Hourly forecast points (the next 24h) captured at each poll.
-- generated_at = when the forecast was retrieved; forecast_ts =
-- the hour the prediction is for. Keeping generated_at preserves
-- forecast revisions over time.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather_forecast (
    id                      BIGSERIAL       NOT NULL,
    location_id             INTEGER         NOT NULL REFERENCES weather_locations(location_id) ON DELETE CASCADE,
    generated_at            TIMESTAMPTZ     NOT NULL,           -- when this forecast was fetched
    forecast_ts             TIMESTAMPTZ     NOT NULL,           -- target hour of the prediction

    summary                 VARCHAR(255),
    icon                    VARCHAR(50),

    temp_c                  NUMERIC(6,2),
    apparent_temp_c         NUMERIC(6,2),
    dew_point_c             NUMERIC(6,2),
    humidity_pct            NUMERIC(5,2),
    pressure_hpa            NUMERIC(7,2),
    wind_speed_ms           NUMERIC(6,2),
    wind_gust_ms            NUMERIC(6,2),
    wind_bearing_deg        SMALLINT,
    cloud_cover_pct         NUMERIC(5,2),
    uv_index                SMALLINT,
    visibility_km           NUMERIC(6,2),
    ozone                   NUMERIC(7,2),

    precip_intensity_mmph   NUMERIC(7,3),
    precip_probability_pct  NUMERIC(5,2),
    precip_type             VARCHAR(20),

    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    UNIQUE (location_id, generated_at, forecast_ts)
);

COMMENT ON TABLE  weather_forecast IS 'Hourly Dark Sky forecast points (next 24h) captured per poll; generated_at preserves forecast revisions.';

SELECT create_hypertable('weather_forecast', 'forecast_ts',
    chunk_time_interval => INTERVAL '30 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_wf_location_fts ON weather_forecast(location_id, forecast_ts DESC);
CREATE INDEX IF NOT EXISTS idx_wf_location_gen ON weather_forecast(location_id, generated_at DESC);
