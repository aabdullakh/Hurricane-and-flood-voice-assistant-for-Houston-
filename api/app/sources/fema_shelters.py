"""Open shelters from FEMA's Open Shelters feed (National Shelter System; public, no key).

Official data only: we return shelters FEMA lists with status OPEN, nothing else, and never invent one.
The feed has no per-shelter "last updated" field, so every answer carries the time we checked FEMA.
Short timeouts and a 5-minute cache (failures cached for 1 minute) mean a slow FEMA can't hang the site.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from .. import geo

URL = "https://gis.fema.gov/arcgis/rest/services/NSS/OpenShelters/FeatureServer/0/query"
SOURCE = "FEMA Open Shelters (National Shelter System)"
SOURCE_URL = "https://gis.fema.gov/arcgis/rest/services/NSS/OpenShelters/FeatureServer/0"
RADIUS_MILES = 50
TIMEOUT = httpx.Timeout(6.0, connect=3.0)
TTL_SECONDS = 300
FAILURE_TTL_SECONDS = 60
MAX_CACHED_CELLS = 2_000
_FIELDS = ("shelter_name", "address", "city", "state", "zip", "shelter_status", "latitude", "longitude",
           "org_name", "wheelchair_accessible", "pet_accommodations_code")
# key (lat, lon rounded to 0.01) -> (monotonic time, records or None if FEMA failed, checked_at iso)
_CACHE: dict[tuple[float, float], tuple[float, list[dict] | None, str]] = {}


class FemaUnavailable(Exception):
    """FEMA's feed didn't answer in time, or answered with an error."""


async def _query(client: httpx.AsyncClient, lat: float, lon: float) -> list[dict]:
    """One request to FEMA: OPEN shelters within RADIUS_MILES of the point. Raises FemaUnavailable."""
    try:
        r = await client.get(URL, params={
            "where": "shelter_status='OPEN'",
            "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "distance": RADIUS_MILES, "units": "esriSRUnit_StatuteMile",
            "outFields": ",".join(_FIELDS), "returnGeometry": "false", "f": "json",
        }, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise FemaUnavailable(str(e)) from e
    if not isinstance(data, dict) or "error" in data:  # ArcGIS reports errors with HTTP 200
        raise FemaUnavailable("FEMA returned an error")
    return [f.get("attributes") or {} for f in data.get("features") or []]


def _yes_no(value) -> bool | None:
    v = str(value or "").strip().upper()
    return True if v in {"YES", "Y", "TRUE"} else False if v in {"NO", "N", "FALSE", "NONE"} else None  # blank / UNK -> not listed


def _shelter(item: dict, lat: float, lon: float) -> dict | None:
    if str(item.get("shelter_status") or "").strip().upper() != "OPEN":  # defense in depth: never trust the filter alone
        return None
    try:
        s_lat, s_lon = float(item["latitude"]), float(item["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    street = (item.get("address") or "").strip()
    place = ", ".join(p for p in ((item.get("city") or "").strip(), " ".join(
        p for p in ((item.get("state") or "").strip(), (item.get("zip") or "").strip()) if p)) if p)
    return {
        "name": (item.get("shelter_name") or "").strip() or "Unnamed shelter",
        "address": ", ".join(p for p in (street, place) if p) or None,
        "distance_miles": round(geo.haversine_miles(lat, lon, s_lat, s_lon), 1),
        "lat": s_lat, "lon": s_lon,
        "status": "OPEN",
        "organization": (item.get("org_name") or "").strip() or None,
        "accessible": _yes_no(item.get("wheelchair_accessible")),
        "accepts_pets": _yes_no(item.get("pet_accommodations_code")),
        "source": SOURCE,
    }


async def open_shelters_near(client: httpx.AsyncClient, lat: float, lon: float, limit: int = 3) -> dict:
    """Nearest OPEN shelters FEMA lists within RADIUS_MILES, closest first. Raises FemaUnavailable."""
    lat, lon = round(lat, 2), round(lon, 2)
    key, now = (lat, lon), time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < (TTL_SECONDS if hit[1] is not None else FAILURE_TTL_SECONDS):
        records, checked_at = hit[1], hit[2]
    else:
        checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            records = await _query(client, lat, lon)
        except FemaUnavailable:
            records = None
        if len(_CACHE) >= MAX_CACHED_CELLS:
            _CACHE.clear()
        _CACHE[key] = (now, records, checked_at)
    if records is None:
        raise FemaUnavailable("FEMA's shelter list is not reachable right now")
    shelters = sorted((s for s in (_shelter(i, lat, lon) for i in records) if s and s["distance_miles"] <= RADIUS_MILES),
                      key=lambda s: s["distance_miles"])
    return {"shelters": shelters[:limit], "checked_at": checked_at, "radius_miles": RADIUS_MILES,
            "source": SOURCE, "source_url": SOURCE_URL}


def clear_cache() -> None:
    _CACHE.clear()
