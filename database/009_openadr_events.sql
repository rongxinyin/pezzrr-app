-- ============================================================================
-- 009_openadr_events.sql
-- Time-series table for OpenADR 3.1 VTN price event readings polled by the
-- VEN client. Partitioned as a TimescaleDB hypertable on ts.
--
-- Run as: psql -U pezerr -h localhost pezerr_db -f 009_openadr_events.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS openadr_events (
    id              BIGSERIAL           NOT NULL,
    ts              TIMESTAMPTZ         NOT NULL,           -- poll timestamp
    program_name    TEXT                NOT NULL,
    program_id      TEXT                NOT NULL,
    event_name      TEXT,
    event_id        TEXT,               -- VTN-assigned event UUID
    priority        SMALLINT,           -- 1 = peak, 2 = off-peak
    period_type     TEXT,               -- 'peak' | 'off_peak'
    price_per_kwh   NUMERIC(8, 5)       NOT NULL,
    interval_start  TIMESTAMPTZ         NOT NULL,           -- active interval window start
    interval_end    TIMESTAMPTZ         NOT NULL,           -- active interval window end
    ven_id          TEXT,
    ven_name        TEXT,
    PRIMARY KEY (id, ts)
);

SELECT create_hypertable(
    'openadr_events', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists       => TRUE
);

CREATE INDEX IF NOT EXISTS idx_openadr_events_ts
    ON openadr_events (ts DESC);

CREATE INDEX IF NOT EXISTS idx_openadr_events_program
    ON openadr_events (program_name, ts DESC);

CREATE INDEX IF NOT EXISTS idx_openadr_events_period
    ON openadr_events (period_type, ts DESC);

\echo 'openadr_events hypertable created.'
