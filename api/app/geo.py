"""Distance helpers and address geocoding."""
from __future__ import annotations

import hashlib
import math
import re
from typing import Iterable, NamedTuple

import httpx

from . import places
from .config import settings

# US Census one-line geocoder (free, no key). [VERIFY] the benchmark name from a laptop before relying on it.
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
# ZIP-only lookups: Census needs a street, so use ZIP centroids instead.
ZIPPOPOTAM_URL = "https://api.zippopotam.us/us/{zip}"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# "77002", "77002 Houston", "77002, Houston, TX", "77002-1234 Texas"
_ZIP_ONLY = re.compile(r"^\s*(\d{5})(?:-\d{4})?(?:[\s,]+(?:houston|tx|texas))*[\s,]*$", re.IGNORECASE)
_zip_cache: dict[str, tuple[float, float]] = {}  # successes only, so a failed lookup is retried next time
_search_cache: dict[str, tuple[float, float]] = {}  # Nominatim free-text results, same rule


def _cache_key(text: str) -> str:
    """Caches are keyed by a SHA-256 hash, so no searched text (address, place) is kept in memory."""
    return hashlib.sha256(text.encode()).hexdigest()
# Nominatim viewbox (left, top, right, bottom): Harris County plus a margin for Katy, Sugar Land, The Woodlands.
HOUSTON_VIEWBOX = "-96.00,30.25,-94.85,29.45"


class GeoPoint(NamedTuple):
    lat: float
    lon: float
    approximate: bool  # True for ZIP centroids, neighborhoods and landmarks, so answers are for the general area


# "Texas Ave" etc. are Houston streets, not the state.
_STATE = re.compile(r"\btx\b|\btexas\b(?!\s+(ave|avenue|st|street|rd|road)\b)", re.IGNORECASE)
# ZIP only at the end, so 5-digit house numbers ("10101 Westheimer Rd") don't count.
_ZIP = re.compile(r"\b\d{5}(-\d{4})?\s*$")


def with_houston_default(address: str) -> str:
    """Callers often say just a street. Without a state or ZIP, assume Houston, TX."""
    address = address.strip()
    if _STATE.search(address) or _ZIP.search(address):
        return address
    return f"{address}, Houston, TX"


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


# Gauge names are USGS-abbreviated: "Cypress Ck nr Westfield, TX", "W Fk San Jacinto Rv nr Humble, TX".
_GAUGE_DROP = {"tx", "texas", "nr", "near", "at", "in", "the"}
_GAUGE_ABBR = {"ck": "creek", "cr": "creek", "rv": "river", "lk": "lake", "fk": "fork", "res": "reservoir",
               "n": "north", "s": "south", "e": "east", "w": "west"}
_WATER_BODY = re.compile(r"\s+(?:at|nr|near)\s+", re.IGNORECASE)  # "Brays Bayou at Gessner Dr" -> "Brays Bayou"


def _gauge_key(text: str) -> str:
    """Comparable form of a gauge or caller phrase. Spaces removed so "white oak" matches "Whiteoak"."""
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    return "".join(_GAUGE_ABBR.get(w, w) for w in words if w not in _GAUGE_DROP)


def match_gauge(text: str, gauges: Iterable) -> tuple[float, float] | None:
    """Caller said a gauge name or a bayou/creek name ("near Brays Bayou"). Exact match only, so a street
    like "1000 Buffalo Bayou Dr" does not count. Several matches: the gauge closest to downtown Houston."""
    gauges = [g for g in gauges if g.name]
    words = text.strip()
    for attempt in (words, re.sub(r"[\s,]+houston\s*$", "", words, flags=re.IGNORECASE)):
        key = _gauge_key(attempt)
        if not key:
            continue
        hits = [g for g in gauges if _gauge_key(g.name) == key] or [
            g for g in gauges if _gauge_key(_WATER_BODY.split(g.name, maxsplit=1)[0]) == key
        ]
        if hits:
            lat0, lon0 = places.PLACES["downtown"]
            best = min(hits, key=lambda g: haversine_miles(lat0, lon0, g.lat, g.lon))
            return best.lat, best.lon
    return None


async def _zippopotam(client: httpx.AsyncClient, zip_code: str) -> tuple[float, float] | None:
    try:
        r = await client.get(ZIPPOPOTAM_URL.format(zip=zip_code), timeout=8)
        r.raise_for_status()
        place = r.json()["places"][0]
        return float(place["latitude"]), float(place["longitude"])
    except Exception:
        return None


async def _nominatim(client: httpx.AsyncClient, params: dict) -> tuple[float, float] | None:
    try:
        r = await client.get(
            NOMINATIM_URL,
            params={**params, "format": "json", "limit": 1},
            headers={"User-Agent": settings.nws_user_agent},  # Nominatim policy requires an identifying User-Agent
            timeout=8,
        )
        r.raise_for_status()
        hit = r.json()[0]
        return float(hit["lat"]), float(hit["lon"])
    except Exception:
        return None


async def geocode_zip(client: httpx.AsyncClient, zip_code: str) -> tuple[float, float] | None:
    """ZIP centroid from zippopotam.us, falling back to Nominatim. Never raises."""
    key = _cache_key(zip_code)
    if key in _zip_cache:
        return _zip_cache[key]
    pt = await _zippopotam(client, zip_code) or await _nominatim(client, {"postalcode": zip_code, "country": "US"})
    if pt is not None:
        _zip_cache[key] = pt
    return pt


async def _census(client: httpx.AsyncClient, address: str) -> tuple[float, float] | None:
    try:
        r = await client.get(
            CENSUS_URL,
            params={"address": with_houston_default(address), "benchmark": "Public_AR_Current", "format": "json"},
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


async def search_place(client: httpx.AsyncClient, text: str) -> tuple[float, float] | None:
    """Free-text landmark search on Nominatim, limited to the Houston area. Never raises."""
    key = _cache_key(places.normalize(text))
    if key in _search_cache:
        return _search_cache[key]
    pt = await _nominatim(
        client, {"q": text.strip(), "viewbox": HOUSTON_VIEWBOX, "bounded": 1, "countrycodes": "us"}
    )
    if pt is not None:
        _search_cache[key] = pt
    return pt


async def geocode(client: httpx.AsyncClient, address: str, gauges: Iterable = ()) -> GeoPoint | None:
    """ZIP -> built-in place table -> loaded gauge / bayou names -> Census street address -> Nominatim search.
    Returns None if nothing matched. Never raises, a failed lookup just means 'ask the caller again'."""
    m = _ZIP_ONLY.match(address)
    if m:
        pt = await geocode_zip(client, m.group(1))
        return GeoPoint(*pt, approximate=True) if pt else None
    pt = places.lookup(address) or match_gauge(address, gauges)
    if pt:
        return GeoPoint(*pt, approximate=True)
    pt = await _census(client, address)
    if pt:
        return GeoPoint(*pt, approximate=False)
    pt = await search_place(client, address)
    return GeoPoint(*pt, approximate=True) if pt else None
