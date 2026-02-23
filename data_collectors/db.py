"""
Database manager for data collectors.
Single persistent connection with auto-reconnect, parameterized inserts,
and seed/upsert helpers.
"""

import logging
import psycopg2
import psycopg2.extras

from .config import get_db_dsn

log = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self, dsn=None):
        self._dsn = dsn or get_db_dsn()
        self._conn = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def connect(self):
        if self._conn is None or self._conn.closed:
            log.info("Connecting to database ...")
            self._conn = psycopg2.connect(self._dsn)
            self._conn.autocommit = True
            log.info("Database connected.")
        return self._conn

    def _cursor(self):
        conn = self.connect()
        try:
            conn.isolation_level  # lightweight check
        except psycopg2.InterfaceError:
            self._conn = None
            conn = self.connect()
        return conn.cursor()

    def close(self):
        if self._conn and not self._conn.closed:
            self._conn.close()

    # ------------------------------------------------------------------
    # Seed / upsert helpers
    # ------------------------------------------------------------------
    def upsert_home(self, home_name, address, city, state, zip_code,
                    utility_id, timezone):
        existing = self.get_home_id(home_name)
        if existing:
            return existing
        cur = self._cursor()
        cur.execute(
            """
            INSERT INTO homes (home_name, address, city, state, zip_code,
                               utility_id, timezone)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING home_id
            """,
            (home_name, address, city, state, zip_code, utility_id, timezone),
        )
        return cur.fetchone()[0]

    def upsert_device(self, home_id, device_type, device_name,
                      manufacturer, model, serial_number, api_identifier):
        existing = self.get_device_id(serial_number)
        if existing:
            return existing
        cur = self._cursor()
        cur.execute(
            """
            INSERT INTO devices
                (home_id, device_type, device_name, manufacturer, model,
                 serial_number, api_identifier)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING device_id
            """,
            (home_id, device_type, device_name, manufacturer, model,
             serial_number, api_identifier),
        )
        return cur.fetchone()[0]

    def upsert_panel_circuit(self, device_id, channel_num, circuit_name):
        cur = self._cursor()
        cur.execute(
            """
            INSERT INTO panel_circuits (device_id, channel_num, circuit_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (device_id, channel_num) DO UPDATE
                SET circuit_name = EXCLUDED.circuit_name
            RETURNING circuit_id
            """,
            (device_id, channel_num, circuit_name),
        )
        row = cur.fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------
    def get_home_id(self, home_name):
        cur = self._cursor()
        cur.execute("SELECT home_id FROM homes WHERE home_name = %s",
                    (home_name,))
        row = cur.fetchone()
        return row[0] if row else None

    def get_device_id(self, serial_number):
        cur = self._cursor()
        cur.execute("SELECT device_id FROM devices WHERE serial_number = %s",
                    (serial_number,))
        row = cur.fetchone()
        return row[0] if row else None

    def get_device_id_by_api_id(self, api_identifier):
        cur = self._cursor()
        cur.execute(
            "SELECT device_id FROM devices WHERE api_identifier = %s",
            (api_identifier,))
        row = cur.fetchone()
        return row[0] if row else None

    def get_circuit_map(self, device_id):
        """Return {channel_num: circuit_id} for the given panel device."""
        cur = self._cursor()
        cur.execute(
            "SELECT channel_num, circuit_id FROM panel_circuits "
            "WHERE device_id = %s",
            (device_id,))
        return {row[0]: row[1] for row in cur.fetchall()}

    # ------------------------------------------------------------------
    # Insert methods (time-series readings)
    # ------------------------------------------------------------------
    def insert_smart_panel_reading(self, row):
        cur = self._cursor()
        cur.execute(
            """
            INSERT INTO smart_panel_readings
                (device_id, home_id, ts,
                 grid_power_w, grid_frequency_hz, solar_power_w,
                 battery_power_w, battery_soc_pct,
                 home_load_w, grid_status, eps_mode_active)
            VALUES (%s,%s,%s, %s,%s,%s, %s,%s, %s,%s,%s)
            """,
            (
                row["device_id"], row["home_id"], row["ts"],
                row.get("grid_power_w"), row.get("grid_frequency_hz"),
                row.get("solar_power_w"),
                row.get("battery_power_w"), row.get("battery_soc_pct"),
                row.get("home_load_w"), row.get("grid_status"),
                row.get("eps_mode_active"),
            ),
        )

    def insert_panel_circuit_reading(self, row):
        cur = self._cursor()
        cur.execute(
            """
            INSERT INTO panel_circuit_readings
                (circuit_id, device_id, home_id, ts, power_w, is_enabled)
            VALUES (%s,%s,%s,%s,%s,%s)
            """,
            (
                row["circuit_id"], row["device_id"], row["home_id"],
                row["ts"], row.get("power_w"), row.get("is_enabled"),
            ),
        )

    def insert_battery_reading(self, row):
        cur = self._cursor()
        cur.execute(
            """
            INSERT INTO battery_readings
                (device_id, home_id, ts,
                 soc_pct, capacity_wh, power_w,
                 ac_in_power_w, ac_out_power_w, status)
            VALUES (%s,%s,%s, %s,%s,%s, %s,%s,%s)
            """,
            (
                row["device_id"], row["home_id"], row["ts"],
                row.get("soc_pct"), row.get("capacity_wh"),
                row.get("power_w"),
                row.get("ac_in_power_w"), row.get("ac_out_power_w"),
                row.get("status"),
            ),
        )

    def insert_thermostat_reading(self, row):
        cur = self._cursor()
        cur.execute(
            """
            INSERT INTO thermostat_readings
                (device_id, home_id, ts,
                 indoor_temp_c, outdoor_temp_c, indoor_humidity_pct,
                 heat_setpoint_c, cool_setpoint_c,
                 hvac_mode, hvac_state, fan_mode, occupancy_status)
            VALUES (%s,%s,%s, %s,%s,%s, %s,%s, %s,%s,%s,%s)
            """,
            (
                row["device_id"], row["home_id"], row["ts"],
                row.get("indoor_temp_c"), row.get("outdoor_temp_c"),
                row.get("indoor_humidity_pct"),
                row.get("heat_setpoint_c"), row.get("cool_setpoint_c"),
                row.get("hvac_mode"), row.get("hvac_state"),
                row.get("fan_mode"), row.get("occupancy_status"),
            ),
        )
