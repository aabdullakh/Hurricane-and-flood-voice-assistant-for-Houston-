"""Shared gauge data structure used by BOTH live data and replay data, so they take the same code path."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class GaugeSeries:
    site_id: str
    name: str
    lat: float
    lon: float
    # ascending by time, tz-aware UTC
    readings: list[tuple[datetime, float]] = field(default_factory=list)


def trend_ft_per_hr(readings: list[tuple[datetime, float]]) -> float | None:
    """Change in feet per hour over roughly the last hour. None if there isn't enough history."""
    if len(readings) < 2:
        return None
    t_last, v_last = readings[-1]
    target = t_last - timedelta(hours=1)
    base = None
    for t, v in reversed(readings[:-1]):
        if t <= target:
            base = (t, v)
            break
    if base is None:
        t0, v0 = readings[0]
        if (t_last - t0) >= timedelta(minutes=20):
            base = (t0, v0)
    if base is None:
        return None
    hours = (t_last - base[0]).total_seconds() / 3600
    return (v_last - base[1]) / hours if hours > 0 else None


def gauge_view(g: GaugeSeries, now: datetime, miles: float | None = None) -> dict:
    t, v = g.readings[-1]
    trend = trend_ft_per_hr(g.readings)
    return {
        "site_id": g.site_id,
        "name": g.name,
        "lat": g.lat,
        "lon": g.lon,
        "level_ft": round(v, 2),
        "trend_ft_per_hr": None if trend is None else round(trend, 2),
        "observed_at": t.isoformat(),
        "age_minutes": max(0, round((now - t).total_seconds() / 60)),
        "distance_miles": None if miles is None else round(miles, 1),
    }
