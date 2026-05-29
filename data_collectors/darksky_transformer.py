"""
Transform raw Dark Sky data points into DB-ready row dicts.

Dark Sky reports humidity, cloudCover and precipProbability as 0..1
fractions; we store them as 0..100 percentages. With units=si,
temperatures are °C, wind m/s, pressure hPa, visibility km, precip mm/h.
"""

from datetime import datetime, timezone


def _now_utc():
    return datetime.now(timezone.utc)


def _ts(point):
    t = point.get("time")
    return datetime.fromtimestamp(t, tz=timezone.utc) if t is not None else None


def _pct(value):
    return value * 100 if value is not None else None


def _weather_fields(point):
    """Shared meteorological columns common to observations and forecasts."""
    return {
        "summary": point.get("summary"),
        "icon": point.get("icon"),
        "temp_c": point.get("temperature"),
        "apparent_temp_c": point.get("apparentTemperature"),
        "dew_point_c": point.get("dewPoint"),
        "humidity_pct": _pct(point.get("humidity")),
        "pressure_hpa": point.get("pressure"),
        "wind_speed_ms": point.get("windSpeed"),
        "wind_gust_ms": point.get("windGust"),
        "wind_bearing_deg": point.get("windBearing"),
        "cloud_cover_pct": _pct(point.get("cloudCover")),
        "uv_index": point.get("uvIndex"),
        "visibility_km": point.get("visibility"),
        "ozone": point.get("ozone"),
        "precip_intensity_mmph": point.get("precipIntensity"),
        "precip_probability_pct": _pct(point.get("precipProbability")),
        "precip_type": point.get("precipType"),
    }


def transform_observation(point, location_id, source):
    """One observed-conditions row from a Dark Sky data point."""
    return {
        "location_id": location_id,
        "ts": _ts(point) or _now_utc(),
        "source": source,
        **_weather_fields(point),
    }


def transform_current(data, location_id):
    """Observed row from the `currently` block (source='current')."""
    point = data.get("currently")
    if not point:
        return None
    return transform_observation(point, location_id, "current")


def transform_hourly_forecast(data, location_id, generated_at, hours=24):
    """List of forecast rows from the `hourly.data` block (next `hours`)."""
    points = (data.get("hourly") or {}).get("data", [])
    rows = []
    for point in points[:hours]:
        fts = _ts(point)
        if fts is None:
            continue
        rows.append({
            "location_id": location_id,
            "generated_at": generated_at,
            "forecast_ts": fts,
            **_weather_fields(point),
        })
    return rows


def transform_history(data, location_id):
    """List of observed rows from a Time Machine `hourly.data` block."""
    points = (data.get("hourly") or {}).get("data", [])
    return [
        transform_observation(point, location_id, "timemachine")
        for point in points
        if point.get("time") is not None
    ]
