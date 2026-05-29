-- =====================================================================
-- 010_dashboard_auth.sql
-- Dashboard users + per-user home access for the API's RBAC
-- (docs/DASHBOARD_DESIGN.md §9). fleet_analyst/admin see all homes
-- regardless of user_home_access; viewer/operator are scoped to their rows.
-- =====================================================================

CREATE TABLE IF NOT EXISTS app_users (
  user_id       SERIAL PRIMARY KEY,
  username      VARCHAR(100) NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role          VARCHAR(20) NOT NULL
                CHECK (role IN ('viewer', 'operator', 'fleet_analyst', 'admin')),
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_home_access (
  user_id INTEGER NOT NULL REFERENCES app_users(user_id) ON DELETE CASCADE,
  home_id INTEGER NOT NULL REFERENCES homes(home_id)     ON DELETE CASCADE,
  PRIMARY KEY (user_id, home_id)
);

CREATE INDEX IF NOT EXISTS idx_user_home_access_user ON user_home_access(user_id);
