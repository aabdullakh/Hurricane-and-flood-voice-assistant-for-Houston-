"""Distance helpers and address geocoding."""
from __future__ import annotations

import math

import httpx

# US Census one-line geocoder (free, no key). [VERIFY] the benchmark name from a laptop before relying on it.
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest(items, lat: float, lon: float, k: int = 3, max_miles: float | None = None):
    """items need .lat and .lon attributes (or dict keys). Returns [(item, miles)] sorted by distance."""

    def coords(x):
        return (x["lat"], x["lon"]) if isinstance(x, dict) else (x.lat, x.lon)

    scored = [(x, haversine_miles(lat, lon, *coords(x))) for x in items]
    scored.sort(key=lambda t: t[1])
    if max_miles is not None:
        scored = [t for t in scored if t[1] <= max_miles]
    return scored[:k]


async def geocode(client: httpx.AsyncClient, address: str) -> tuple[float, float] | None:
    """Returns (lat, lon) or None. Never raises, a failed lookup just means 'ask the caller again'."""
    try:
        r = await client.get(
            CENSUS_URL,
            params={"address": address, "benchmark": "Public_AR_Current", "format": "json"},
            timeout=8,
        )
        r.raise_for_status()
        matches = r.json()["result"]["addressMatches"]
        if not matches:
            return None
        c = matches[0]["coordinates"]
        return float(c["y"]), float(c["x"])
    except Exception:
        return None
