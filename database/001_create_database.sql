-- ============================================================================
-- 001_create_database.sql
-- Create the pezerr_db database and pezerr user for smart home data analytics
-- Run as postgres superuser: psql -U postgres -f 001_create_database.sql
-- ============================================================================

-- Create user if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'pezerr') THEN
        CREATE ROLE pezerr WITH LOGIN PASSWORD '840810';
    END IF;
END
$$;

-- Create database
SELECT 'CREATE DATABASE pezerr_db OWNER pezerr'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'pezerr_db')\gexec

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE pezerr_db TO pezerr;

\c pezerr_db

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Grant schema usage
GRANT ALL ON SCHEMA public TO pezerr;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO pezerr;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO pezerr;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO pezerr;
