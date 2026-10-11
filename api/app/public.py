"""Read-only endpoints for the public website (/public/*). No API key: everything here is public data.

Every endpoint is rate limited per client IP, only answers for the Houston area, and calls upstream services
with short timeouts. Coordinates are rounded to 2 decimals (about 1 km) before use, so the server never handles
a visitor's exact location. The browser talks only to this server; it never calls USGS, NWS, Open-Meteo,
Census, Nominatim or OpenStreetMap directly.
"""
from __future__ import annotations

import asyncio
import math
import re
import time
from collections import OrderedDict, deque

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import geo, service
from .config import settings
from .db import get_session
from .security import _client_ip
from .sources import fema_shelters, openmeteo

router = APIRouter(prefix="/public", tags=["public"])


def get_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.client  # the app's shared client (created in main.lifespan)

# Same box the geocoder searches: Harris County plus a margin (Katy, Sugar Land, The Woodlands).
_left, _top, _right, _bottom = (float(v) for v in geo.HOUSTON_VIEWBOX.split(","))
AREA = {"lat_min": _bottom, "lat_max": _top, "lon_min": _left, "lon_max": _right}


def in_area(lat: float, lon: float) -> bool:
    return AREA["lat_min"] <= lat <= AREA["lat_max"] and AREA["lon_min"] <= lon <= AREA["lon_max"]


def _require_area(lat: float, lon: float) -> None:
    if not in_area(lat, lon):
        raise HTTPException(400, "outside_area")


# ---------------------------------------------------------------- per-IP rate limits
class RateLimit:
    """Sliding window per client IP, in memory (single worker). 429 with Retry-After when exceeded."""

    MAX_IPS = 10_000

    def __init__(self, limit: int, window_seconds: int = 60):
        self.limit, self.window, self.hits = limit, window_seconds, {}

    def __call__(self, request: Request) -> None:
        ip, now = _client_ip(request), time.monotonic()
        q = self.hits.get(ip)
        if q is None:
            if len(self.hits) >= self.MAX_IPS:
                self.hits.clear()
            q = self.hits[ip] = deque()
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            raise HTTPException(429, "too many requests, try again shortly", headers={"Retry-After": str(int(self.window - (now - q[0])) + 1)})
        q.append(now)


conditions_limit = RateLimit(30)
geocode_limit = RateLimit(20)
shelters_limit = RateLimit(60)
tiles_limit = RateLimit(900)  # one map view is ~20-40 tiles; panning and zooming adds up


# ---------------------------------------------------------------- shelters (FEMA, OPEN only)
SHELTERS_NONE = "FEMA lists no open shelters near you right now. Call 211."
SHELTERS_DOWN = "We couldn't reach FEMA's shelter list right now. Call 211 to find an open shelter."


async def shelters_near(client: httpx.AsyncClient, lat: float, lon: float, limit: int) -> dict:
    """Never raises: FEMA being down becomes an honest message, so the page and /conditions still work."""
    base = {"source": fema_shelters.SOURCE, "source_url": fema_shelters.SOURCE_URL, "radius_miles": fema_shelters.RADIUS_MILES,
            "distance": "straight line", "sample_data": False}
    try:
        res = await fema_shelters.open_shelters_near(client, lat, lon, limit)
    except fema_shelters.FemaUnavailable:
        return {**base, "available": False, "count": 0, "shelters": [], "checked_at": None, "message": SHELTERS_DOWN}
    return {**base, "available": True, "count": len(res["shelters"]), "shelters": res["shelters"], "checked_at": res["checked_at"],
            "message": None if res["shelters"] else SHELTERS_NONE}


@router.get("/shelters", dependencies=[Depends(shelters_limit)])
async def public_shelters(lat: float | None = Query(None, ge=-90, le=90), lon: float | None = Query(None, ge=-180, le=180),
                          limit: int = Query(5, ge=1, le=20), client: httpx.AsyncClient = Depends(get_client)):
    """Open shelters FEMA lists near a point (default: central Houston)."""
    if lat is None or lon is None:
        lat, lon = settings.houston_lat, settings.houston_lon
    lat, lon = round(lat, 2), round(lon, 2)
    _require_area(lat, lon)
    return {**await shelters_near(client, lat, lon, limit), "near": {"lat": lat, "lon": lon}}


# ---------------------------------------------------------------- conditions at a point
@router.get("/conditions", dependencies=[Depends(conditions_limit)])
async def public_conditions(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    session: Session = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_client),
):
    """Risk, nearest gauges, NWS alerts for that point, rain context and the nearest official shelters."""
    lat, lon = round(lat, 2), round(lon, 2)
    _require_area(lat, lon)
    status, rain, shelters = await asyncio.gather(service.build_status(session, client, lat, lon), openmeteo.rain_near(client, lat, lon),
                                                  shelters_near(client, lat, lon, 2))
    keep = ("mode", "replay", "risk", "reasons", "caveat", "data_as_of", "nearest_gauges", "nearest_gauge_miles",
            "alerts", "alerts_available", "sources", "disclaimer")
    return {**{k: status[k] for k in keep}, "location": {"lat": lat, "lon": lon, "rounded_to": "about 1 km"},
            "rain": rain, "shelters": shelters}


# ---------------------------------------------------------------- address / place search
class GeocodeIn(BaseModel):
    q: str = Field(min_length=2, max_length=200)


@router.post("/geocode", dependencies=[Depends(geocode_limit)])
async def public_geocode(body: GeocodeIn, session: Session = Depends(get_session), client: httpx.AsyncClient = Depends(get_client)):
    """POST so the text isn't in any URL or access log. Not stored or cached here (ZIP and place names are)."""
    pt = await geo.geocode(client, body.q, service.current_data(session)[0])
    if pt is None:
        return {"found": False, "reason": "not_found"}
    if not in_area(pt.lat, pt.lon):
        return {"found": False, "reason": "outside_area"}
    return {"found": True, "lat": round(pt.lat, 4), "lon": round(pt.lon, 4), "approximate": pt.approximate}


# ---------------------------------------------------------------- map tiles (OpenStreetMap, proxied + cached)
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_ZOOM = (9, 15)
TILE_CACHE_BYTES = 64 * 1024 * 1024
UPSTREAM_PER_MINUTE = 300  # all visitors together: protects OpenStreetMap if someone tries to scrape through us
_tiles: OrderedDict[tuple[int, int, int], tuple[float, float, bytes]] = OrderedDict()  # key -> (fresh_until, stale_until, png)
_tile_bytes = 0
_upstream: deque = deque()


def _tile_range(z: int) -> tuple[int, int, int, int]:
    """x_min, x_max, y_min, y_max of the tiles covering AREA at zoom z, plus one tile of margin."""
    n = 2 ** z

    def xy(lat: float, lon: float) -> tuple[int, int]:
        x = int((lon + 180) / 360 * n)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
        return x, y

    x0, y0 = xy(AREA["lat_max"], AREA["lon_min"])
    x1, y1 = xy(AREA["lat_min"], AREA["lon_max"])
    return x0 - 1, x1 + 1, y0 - 1, y1 + 1


def _max_age(cache_control: str | None) -> int:
    m = re.search(r"max-age=(\d+)", cache_control or "")
    return min(max(int(m.group(1)) if m else 86_400, 3_600), 7 * 86_400)


def _store(key, fresh_until: float, stale_until: float, png: bytes) -> None:
    global _tile_bytes
    if key in _tiles:
        _tile_bytes -= len(_tiles.pop(key)[2])
    _tiles[key] = (fresh_until, stale_until, png)
    _tile_bytes += len(png)
    while _tile_bytes > TILE_CACHE_BYTES and _tiles:
        _tile_bytes -= len(_tiles.popitem(last=False)[1][2])


async def _fetch_tile(client: httpx.AsyncClient, z: int, x: int, y: int) -> httpx.Response:
    return await client.get(TILE_URL.format(z=z, x=x, y=y), headers={"User-Agent": settings.nws_user_agent}, timeout=8)


def _png(body: bytes, max_age: int) -> Response:
    return Response(body, media_type="image/png", headers={"Cache-Control": f"public, max-age={max_age}"})


@router.get("/tiles/{z}/{x}/{y}.png", dependencies=[Depends(tiles_limit)])
async def public_tile(z: int, x: int, y: int, client: httpx.AsyncClient = Depends(get_client)):
    if not TILE_ZOOM[0] <= z <= TILE_ZOOM[1]:
        raise HTTPException(404, "zoom out of range")
    x0, x1, y0, y1 = _tile_range(z)
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        raise HTTPException(404, "outside the Houston area")
    key, now = (z, x, y), time.time()
    hit = _tiles.get(key)
    if hit and now < hit[0]:
        _tiles.move_to_end(key)
        return _png(hit[2], int(hit[0] - now))
    mono = time.monotonic()
    while _upstream and mono - _upstream[0] > 60:
        _upstream.popleft()
    if len(_upstream) >= UPSTREAM_PER_MINUTE:
        if hit and now < hit[1]:
            return _png(hit[2], 300)
        raise HTTPException(503, "map tiles busy, try again shortly", headers={"Retry-After": "30"})
    _upstream.append(mono)
    try:
        r = await _fetch_tile(client, z, x, y)
        r.raise_for_status()
        if not r.headers.get("content-type", "").startswith("image/png"):
            raise ValueError("not a png")
    except Exception:
        if hit and now < hit[1]:  # OpenStreetMap allows serving a stale tile when it is unreachable
            return _png(hit[2], 300)
        raise HTTPException(502, "map tiles unavailable")
    age = _max_age(r.headers.get("cache-control"))
    _store(key, now + age, now + age + 7 * 86_400, r.content)
    return _png(r.content, age)


def clear_caches() -> None:
    global _tile_bytes
    _tiles.clear()
    _tile_bytes = 0
    _upstream.clear()
    for limit in (conditions_limit, geocode_limit, shelters_limit, tiles_limit):
        limit.hits.clear()
