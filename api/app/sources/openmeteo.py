"""Rain near a point from Open-Meteo (free, no key). Context only: it is a model, not a gauge or a warning."""
from __future__ import annotations

import time
from datetime import datetime

import httpx

URL = "https://api.open-meteo.com/v1/forecast"
TTL_SECONDS = 600
_CACHE: dict[tuple[float, float], tuple[float, dict | None]] = {}

# WMO weather codes Open-Meteo uses, grouped into the few words the website needs.
_KIND = {0: "clear", 1: "clear", 2: "cloudy", 3: "cloudy", 45: "fog", 48: "fog",
         51: "drizzle", 53: "drizzle", 55: "drizzle", 56: "drizzle", 57: "drizzle",
         61: "rain", 63: "rain", 65: "heavy_rain", 66: "rain", 67: "heavy_rain",
         80: "showers", 81: "showers", 82: "heavy_rain", 95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm"}


def parse(payload: dict) -> dict:
    """Hourly values are the total for the PRECEDING hour, so 'past 3 hours' is the 3 latest entries at or before now."""
    cur = payload["current"]
    now = datetime.fromisoformat(cur["time"])
    times = [datetime.fromisoformat(t) for t in payload["hourly"]["time"]]
    rain = payload["hourly"]["precipitation"]
    past = [r for t, r in zip(times, rain) if t <= now and r is not None][-3:]
    nxt = [r for t, r in zip(times, rain) if t > now and r is not None][:3]
    code = cur.get("weather_code")
    return {
        "observed_at": cur["time"],  # local Houston time (America/Chicago)
        "kind": _KIND.get(code, "other") if code is not None else None,
        "rain_past_3h_in": round(sum(past), 2) if len(past) == 3 else None,
        "rain_next_3h_in": round(sum(nxt), 2) if len(nxt) == 3 else None,
        "source": "Open-Meteo weather model",
    }


async def rain_near(client: httpx.AsyncClient, lat: float, lon: float) -> dict | None:
    """None if Open-Meteo is unavailable. Cached 10 minutes per ~1 km cell."""
    key = (round(lat, 2), round(lon, 2))
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < TTL_SECONDS:
        return hit[1]
    try:
        r = await client.get(
            URL,
            params={"latitude": key[0], "longitude": key[1], "current": "weather_code,precipitation",
                    "hourly": "precipitation", "past_hours": 3, "forecast_hours": 4,
                    "precipitation_unit": "inch", "timezone": "America/Chicago"},
            timeout=6,
        )
        r.raise_for_status()
        out = parse(r.json())
    except Exception:
        out = None
    _CACHE[key] = (time.monotonic(), out)
    return out


def clear_cache() -> None:
    _CACHE.clear()
