"""
Transform raw EcoFlow SHP2 quota data into DB-ready row dicts.
Field paths based on agents/ecoflow_agent/simple_test_ecoflow.py:300-440.
"""

from datetime import datetime, timezone


def _now_utc():
    return datetime.now(timezone.utc)


# Branch voltage used to derive current_a from the API's per-channel watts.
# The SHP2 API reports per-circuit load in watts only (no measured amps), so
# current is derived as I = P / V. Most branches are 120 V; 240 V loads (AC,
# dryer) get a per-channel override via voltage_map once those are mapped.
DEFAULT_CIRCUIT_VOLTAGE_V = 120.0


# ------------------------------------------------------------------
# smart_panel_readings
# ------------------------------------------------------------------
def transform_panel_reading(data, device_id, home_id):
    """Return a dict ready for db.insert_smart_panel_reading()."""
    hall1_watt = data.get("loadInfo.hall1Watt", [])
    backup_watt = data.get("backupInfo.chWatt",
                           data.get("wattInfo.chWatt", []))

    home_load = sum(hall1_watt) if hall1_watt else None
    grid_power = sum(backup_watt) if backup_watt else None

    battery_soc = data.get(
        "pd303_mc.backupIncreInfo.curDischargeSoc",
        data.get("backupIncreInfo.curDischargeSoc"),
    )

    # Battery power: sum of energy unit output powers
    battery_power = 0.0
    for i in range(1, 4):
        p = data.get(
            f"pd303_mc.backupIncreInfo.Energy{i}Info.outputPower", 0
        )
        battery_power += p
    # Negate so positive = charging, negative = discharging (panel convention)
    battery_power = -battery_power if battery_power else None

    grid_status = data.get("pd303_mc.gridSta")
    eps_mode = data.get("pd303_mc.epsModeInfo.eps")
    if eps_mode is not None:
        eps_mode = bool(eps_mode)

    return {
        "device_id": device_id,
        "home_id": home_id,
        "ts": _now_utc(),
        "grid_power_w": grid_power,
        "grid_frequency_hz": data.get("pd303_mc.gridFreq"),
        "solar_power_w": data.get("pd303_mc.pvPower"),
        "battery_power_w": battery_power,
        "battery_soc_pct": battery_soc,
        "home_load_w": home_load,
        "grid_status": grid_status,
        "eps_mode_active": eps_mode,
    }


# ------------------------------------------------------------------
# panel_circuit_readings  (12 rows)
# ------------------------------------------------------------------
def transform_circuit_readings(data, device_id, home_id, circuit_map,
                               voltage_map=None, default_voltage_v=DEFAULT_CIRCUIT_VOLTAGE_V):
    """Return list of dicts, one per circuit.

    The SHP2 API exposes per-channel load in watts only, so current_a is
    derived as power_w / voltage. `voltage_map` is an optional
    {channel_num: volts} override (e.g. 240 for the AC/dryer branches); any
    channel not in it uses `default_voltage_v`."""
    hall1_watt = data.get("loadInfo.hall1Watt", [])
    voltage_map = voltage_map or {}
    ts = _now_utc()
    rows = []

    for ch_num in range(1, 13):  # channels 1-12, matching EcoFlow API (ch1-ch12)
        circuit_id = circuit_map.get(ch_num)
        if circuit_id is None:
            continue

        arr_idx = ch_num - 1  # hall1Watt list is 0-indexed
        power = hall1_watt[arr_idx] if arr_idx < len(hall1_watt) else 0.0

        volts = voltage_map.get(ch_num, default_voltage_v)
        current = power / volts if volts else None

        load_sta = data.get(
            f"pd303_mc.loadIncreInfo.hall1IncreInfo.ch{ch_num}Sta.loadSta",
            "",
        )
        is_enabled = load_sta == "LOAD_CH_POWER_ON"

        rows.append({
            "circuit_id": circuit_id,
            "device_id": device_id,
            "home_id": home_id,
            "ts": ts,
            "power_w": power,
            "current_a": current,
            "voltage_v": volts,
            "is_enabled": is_enabled,
        })

    return rows


# ------------------------------------------------------------------
# battery_readings
# ------------------------------------------------------------------
def transform_battery_reading(data, device_id, home_id):
    """Return a dict ready for db.insert_battery_reading()."""
    soc = data.get(
        "pd303_mc.backupIncreInfo.curDischargeSoc",
        data.get("backupIncreInfo.curDischargeSoc"),
    )
    capacity = data.get(
        "pd303_mc.backupIncreInfo.backupDischargeRmainBatCap",
        data.get("backupIncreInfo.backupDischargeRmainBatCap"),
    )

    # Output power from Energy2 (primary battery unit)
    output_power = data.get(
        "pd303_mc.backupIncreInfo.Energy2Info.outputPower", 0
    )
    # Negate: API reports positive when discharging
    power = -output_power if output_power else 0.0

    ac_in = data.get(
        "pd303_mc.chargeWattPower",
        data.get("chargeWattPower", 0),
    )
    backup_watt = data.get("backupInfo.chWatt",
                           data.get("wattInfo.chWatt", []))
    ac_out = sum(backup_watt) if backup_watt else 0.0

    # Derive status from battery port control state
    status = "standby"
    for i in range(1, 4):
        ctrl = str(data.get(
            f"pd303_mc.backupIncreInfo.ch{i}Info.ctrlSta", ""
        ))
        if "DISCHARGE" in ctrl:
            status = "discharging"
            break
        elif "CHARGE" in ctrl:
            status = "charging"

    return {
        "device_id": device_id,
        "home_id": home_id,
        "ts": _now_utc(),
        "soc_pct": soc,
        "capacity_wh": capacity,
        "power_w": power,
        "ac_in_power_w": ac_in,
        "ac_out_power_w": ac_out,
        "status": status,
    }
