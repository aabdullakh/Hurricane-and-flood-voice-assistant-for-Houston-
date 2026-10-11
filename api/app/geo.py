"""Distance helpers and address geocoding."""
from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
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
# Greater Houston: Harris County plus the nearby cities callers name (Katy, Sugar Land, Pearland, The Woodlands,
# Conroe, League City, Galveston). Searches are bounded to this box; anything outside is "outside the area".
HOUSTON_VIEWBOX = "-96.10,30.45,-94.70,29.15"
_VB_LEFT, _VB_TOP, _VB_RIGHT, _VB_BOTTOM = (float(v) for v in HOUSTON_VIEWBOX.split(","))


def in_area(lat: float, lon: float) -> bool:
    return _VB_BOTTOM <= lat <= _VB_TOP and _VB_LEFT <= lon <= _VB_RIGHT


class Located(NamedTuple):
    status: str  # "found" | "not_found" | "outside_area"
    point: "GeoPoint | None"  # set for "found" (and for "outside_area", where it is outside the box)


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


NOMINATIM_MIN_INTERVAL = 1.0  # seconds between requests, for the whole server (Nominatim usage policy)
_nominatim_gate: tuple[object, asyncio.Lock] | None = None
_nominatim_last = 0.0


def _gate() -> asyncio.Lock:
    """One lock per event loop (tests run several loops); requests queue behind it."""
    global _nominatim_gate
    loop = asyncio.get_running_loop()
    if _nominatim_gate is None or _nominatim_gate[0] is not loop:
        _nominatim_gate = (loop, asyncio.Lock())
    return _nominatim_gate[1]


async def _nominatim_search(client: httpx.AsyncClient, params: dict) -> list[dict] | None:
    """Raw Nominatim results, or None if it failed. Throttled to 1 request per second across all callers."""
    global _nominatim_last
    async with _gate():
        wait = _nominatim_last + NOMINATIM_MIN_INTERVAL - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _nominatim_last = time.monotonic()
        try:
            r = await client.get(
                NOMINATIM_URL,
                params={**params, "format": "json"},
                headers={"User-Agent": settings.nws_user_agent},  # Nominatim policy requires an identifying User-Agent
                timeout=8,
            )
            r.raise_for_status()
            hits = r.json()
            return hits if isinstance(hits, list) else None
        except Exception:
            return None


async def _nominatim(client: httpx.AsyncClient, params: dict) -> tuple[float, float] | None:
    hits = await _nominatim_search(client, {**params, "limit": 1})
    try:
        return float(hits[0]["lat"]), float(hits[0]["lon"])
    except (TypeError, IndexError, KeyError, ValueError):
        return None


# A town's own entry beats its county or state of the same name ("Galveston" city, not Galveston County).
_SETTLEMENT_TYPES = {"city", "town", "village", "hamlet", "municipality", "borough", "suburb", "quarter", "neighbourhood"}
_settlement_cache: dict[str, tuple[float, float] | None] = {}  # hash -> point, or None = not a place we know


async def find_settlement(client: httpx.AsyncClient, text: str, bounded: bool = True) -> tuple[float, float] | None:
    """Center of a city/town/neighborhood by name. bounded=True: only inside Greater Houston; False: anywhere in the US.
    Cached (successes and confirmed misses) by a hash of the text, never the text itself."""
    key = _cache_key(("in:" if bounded else "us:") + places.normalize(text))
    if key in _settlement_cache:
        return _settlement_cache[key]
    params = {"q": text.strip(), "featureType": "settlement", "countrycodes": "us", "limit": 5}
    if bounded:
        params |= {"viewbox": HOUSTON_VIEWBOX, "bounded": 1}
    hits = await _nominatim_search(client, params)
    if hits is None:
        return None  # Nominatim failed: don't cache, try again next time
    pick = next((h for h in hits if h.get("addresstype") in _SETTLEMENT_TYPES), hits[0] if hits else None)
    try:
        pt = (float(pick["lat"]), float(pick["lon"])) if pick else None
    except (KeyError, TypeError, ValueError):
        pt = None
    if len(_settlement_cache) >= 5_000:
        _settlement_cache.clear()
    _settlement_cache[key] = pt
    return pt


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


async def locate(client: httpx.AsyncClient, text: str, gauges: Iterable = ()) -> Located:
    """ZIP -> built-in place table -> loaded gauge / bayou names -> Census street address -> Nominatim city/town
    (Greater Houston only) -> Nominatim landmark (Greater Houston only) -> is it a real place elsewhere?
    Never raises. Tells "not found" apart from "outside the area" so the caller can be told which."""
    text = (text or "").strip()
    if not text:
        return Located("not_found", None)
    m = _ZIP_ONLY.match(text)
    if m:
        pt = await geocode_zip(client, m.group(1))
        if pt is None:
            return Located("not_found", None)
        return Located("found" if in_area(*pt) else "outside_area", GeoPoint(*pt, approximate=True))
    pt = places.lookup(text) or match_gauge(text, gauges)
    if pt:
        return Located("found", GeoPoint(*pt, approximate=True))
    pt = await _census(client, text)
    if pt:  # Census can find a real address elsewhere when the caller names another city or state
        return Located("found" if in_area(*pt) else "outside_area", GeoPoint(*pt, approximate=False))
    pt = await find_settlement(client, text, bounded=True) or await search_place(client, text)
    if pt:
        return Located("found", GeoPoint(*pt, approximate=True))
    pt = await find_settlement(client, text, bounded=False)
    if pt and not in_area(*pt):
        return Located("outside_area", GeoPoint(*pt, approximate=True))
    return Located("not_found", None)


async def geocode(client: httpx.AsyncClient, address: str, gauges: Iterable = ()) -> GeoPoint | None:
    """The point, only if it's inside Greater Houston; None otherwise (see locate() for why)."""
    res = await locate(client, address, gauges)
    return res.point if res.status == "found" else None
