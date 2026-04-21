"""
Transform raw Ecobee thermostat dict into a DB-ready row dict.
"""

from datetime import datetime, timezone


def _f_to_c(val_tenths):
    """Convert Ecobee temperature (Fahrenheit * 10) to Celsius."""
    if val_tenths is None:
        return None
    return round((val_tenths / 10.0 - 32) * 5 / 9, 2)


def _parse_hold_until(event):
    """Parse event endDate + endTime into a UTC-aware datetime, or None if indefinite."""
    if event.get("holdType") == "indefinite":
        return None
    end_date = event.get("endDate", "")
    end_time = event.get("endTime", "")
    if end_date and end_time:
        try:
            return datetime.strptime(
                f"{end_date} {end_time}", "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _derive_hvac_state(runtime):
    """Derive HVAC state from equipmentStatus string."""
    status = runtime.get("equipmentStatus", "")
    if "heatPump" in status or "auxHeat" in status:
        return "heating"
    if "compCool" in status:
        return "cooling"
    if "fan" in status:
        return "fan"
    return "idle"


def transform_thermostat_reading(thermostat, device_id, home_id):
    """Return a dict ready for db.insert_thermostat_reading()."""
    runtime = thermostat.get("runtime", {})
    settings = thermostat.get("settings", {})
    weather = thermostat.get("weather", {})
    program = thermostat.get("program", {})

    # Outdoor temp from weather forecast
    outdoor_temp = None
    forecasts = weather.get("forecasts", [])
    if forecasts:
        outdoor_temp = _f_to_c(forecasts[0].get("temperature"))

    # Fan mode
    fan_min_on = settings.get("fanMinOnTime", 0)
    fan_mode = "on" if fan_min_on > 0 else "auto"

    # Active event (hold, vacation, autoAway, etc.)
    events = thermostat.get("events", [])
    active_event = next((e for e in events if e.get("running")), None)
    hold_type = None
    hold_until = None
    if active_event:
        hold_type = active_event.get("holdType") or active_event.get("type")
        hold_until = _parse_hold_until(active_event)

    return {
        "device_id": device_id,
        "home_id": home_id,
        "ts": datetime.now(timezone.utc),
        "indoor_temp_c": _f_to_c(runtime.get("actualTemperature")),
        "outdoor_temp_c": outdoor_temp,
        "indoor_humidity_pct": runtime.get("actualHumidity"),
        "heat_setpoint_c": _f_to_c(runtime.get("desiredHeat")),
        "cool_setpoint_c": _f_to_c(runtime.get("desiredCool")),
        "hvac_mode": settings.get("hvacMode"),
        "hvac_state": _derive_hvac_state(runtime),
        "fan_mode": fan_mode,
        "occupancy_status": program.get("currentClimateRef"),
        "hold_type": hold_type,
        "hold_until": hold_until,
    }


def dedup_key(thermostat):
    """Return a hashable tuple for dedup comparison."""
    runtime = thermostat.get("runtime", {})
    events = thermostat.get("events", [])
    active_event = next((e for e in events if e.get("running")), None)
    return (
        runtime.get("actualTemperature"),
        runtime.get("actualHumidity"),
        runtime.get("desiredHeat"),
        runtime.get("desiredCool"),
        runtime.get("lastStatusModified"),
        active_event.get("holdType") if active_event else None,
        active_event.get("endDate") if active_event else None,
    )
