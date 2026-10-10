"""National Weather Service alerts. No API key; a identifying User-Agent header is REQUIRED.

Docs: https://www.weather.gov/documentation/services-web-api
`alerts/active?point=lat,lon` is the intended way to ask "what alerts apply here". [VERIFY] from a laptop.
"""
from __future__ import annotations

import time

import httpx

NWS_ALERTS = "https://api.weather.gov/alerts/active"
_CACHE: dict[tuple[float, float], tuple[float, list[dict]]] = {}
TTL_SECONDS = 300


def parse_alerts(payload: dict) -> list[dict]:
    out = []
    for f in payload.get("features", []):
        p = f.get("properties", {})
        out.append(
            {
                "event": p.get("event", ""),
                "severity": p.get("severity", ""),
                "headline": p.get("headline", ""),
                "area": p.get("areaDesc", ""),
                "effective": p.get("effective"),
                "expires": p.get("expires"),
            }
        )
    return out


async def alerts_for_point(client: httpx.AsyncClient, lat: float, lon: float, user_agent: str) -> list[dict]:
    """Active alerts covering a point. Cached for 5 minutes per ~1 km cell so 1,000 callers don't mean 1,000 requests."""
    key = (round(lat, 2), round(lon, 2))
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < TTL_SECONDS:
        return hit[1]
    r = await client.get(
        NWS_ALERTS,
        params={"point": f"{lat:.4f},{lon:.4f}"},
        headers={"User-Agent": user_agent, "Accept": "application/geo+json"},
        timeout=10,
    )
    r.raise_for_status()
    alerts = parse_alerts(r.json())
    _CACHE[key] = (time.monotonic(), alerts)
    return alerts


def clear_cache() -> None:
    _CACHE.clear()
