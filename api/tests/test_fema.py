"""FEMA Open Shelters: OPEN only, nearest first, honest when empty or down, cached, and the agent's /shelters endpoint."""
import httpx
import pytest

from app import geo
from app.sources import fema_shelters

from .conftest import HEADERS

REAL_QUERY = fema_shelters._query  # saved at import, before conftest's autouse fixture swaps in a no-network mock

GRB = {"shelter_name": "George R. Brown Convention Center", "address": "1001 Avenida de las Americas", "city": "Houston",
       "state": "TX", "zip": "77010", "shelter_status": "OPEN", "latitude": 29.7522, "longitude": -95.3576,
       "org_name": "AMERICAN RED CROSS", "wheelchair_accessible": "YES", "pet_accommodations_code": "NONE"}
NRG = {"shelter_name": "NRG Center", "address": "1 NRG Pkwy", "city": "Houston", "state": "TX", "zip": "77054",
       "shelter_status": "open ", "latitude": 29.6847, "longitude": -95.4107, "wheelchair_accessible": "UNK", "pet_accommodations_code": " "}
FAR = {"shelter_name": "Austin Site", "shelter_status": "OPEN", "latitude": 30.27, "longitude": -97.74}  # ~145 miles away
CLOSED = {"shelter_name": "Closed Middle School", "shelter_status": "CLOSED", "latitude": 29.76, "longitude": -95.37}
FULL = {"shelter_name": "Full Church", "shelter_status": "FULL", "latitude": 29.77, "longitude": -95.36}
NO_COORDS = {"shelter_name": "No Coordinates", "shelter_status": "OPEN", "latitude": None, "longitude": None}


def _fema(monkeypatch, records=None, exc=None):
    calls = []

    async def q(client, lat, lon):
        calls.append((lat, lon))
        if exc:
            raise exc
        return list(records or [])

    monkeypatch.setattr(fema_shelters, "_query", q)
    return calls


# ---------- the agent's key-protected /shelters ----------
def test_shelters_still_needs_the_api_key(client):
    assert client.get("/shelters?lat=29.76&lon=-95.37").status_code == 401


def test_open_only_nearest_first_max_three(client, monkeypatch):
    _fema(monkeypatch, [CLOSED, FAR, NRG, FULL, GRB, NO_COORDS, {**GRB, "shelter_name": "Third"}, {**GRB, "shelter_name": "Fourth"}])
    d = client.get("/shelters?lat=29.7604&lon=-95.3698", headers=HEADERS).json()
    names = [s["name"] for s in d["shelters"]]
    assert "Closed Middle School" not in names and "Full Church" not in names  # status OPEN only
    assert "Austin Site" not in names and "No Coordinates" not in names  # beyond 50 miles / unusable
    assert len(names) == 3 and names[0] == "George R. Brown Convention Center"
    assert [s["distance_miles"] for s in d["shelters"]] == sorted(s["distance_miles"] for s in d["shelters"])
    assert set(d["shelters"][0]) == {"name", "address", "distance_miles", "source"}
    assert d["shelters"][0]["address"] == "1001 Avenida de las Americas, Houston, TX 77010"
    assert d["source"].startswith("FEMA") and "confirm" in d["message"] and "straight line" in d["message"]


def test_no_open_shelters_gives_the_honest_message(client, monkeypatch):
    _fema(monkeypatch, [CLOSED, FULL])
    d = client.get("/shelters?lat=29.76&lon=-95.37", headers=HEADERS).json()
    assert d["count"] == 0 and d["available"] is True
    assert d["message"] == "FEMA lists no open shelters near you right now. Call 211."


def test_fema_down_returns_a_clear_message_not_a_crash(client, monkeypatch):
    _fema(monkeypatch, exc=fema_shelters.FemaUnavailable("timeout"))
    r = client.get("/shelters?lat=29.76&lon=-95.37", headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is False and d["count"] == 0 and "211" in d["message"] and "isn't reachable" in d["message"]


def test_location_zip_is_resolved_then_searched(client, monkeypatch):
    calls = _fema(monkeypatch, [GRB])

    async def fake_geocode(client, text, gauges=()):
        assert text == "77002"
        return geo.GeoPoint(29.7594, -95.3594, approximate=True)

    monkeypatch.setattr(geo, "geocode", fake_geocode)
    d = client.get("/shelters?location=77002", headers=HEADERS).json()
    assert d["count"] == 1 and d["location"] == {"lat": 29.76, "lon": -95.36}
    assert calls == [(29.76, -95.36)]  # FEMA gets the ~1 km rounded point, not the exact one


def test_bad_location_asks_for_another(client, monkeypatch):
    calls = _fema(monkeypatch, [GRB])

    async def nowhere(*a, **k):
        return None

    monkeypatch.setattr(geo, "geocode", nowhere)
    d = client.get("/shelters?location=zzqx", headers=HEADERS).json()
    assert d["count"] == 0 and d["found"] is False and "ZIP" in d["message"]
    assert calls == []  # nothing to search


def test_missing_location_and_half_coordinates(client):
    d = client.get("/shelters", headers=HEADERS).json()
    assert d["count"] == 0 and "location" in d["message"]
    assert client.get("/shelters?lat=29.76", headers=HEADERS).status_code == 400
    assert client.get("/shelters?lat=29.76&lon=-95.37&limit=4", headers=HEADERS).status_code == 422  # at most 3


# ---------- the FEMA client itself ----------
async def _run(coro):
    return await coro


def test_results_are_cached_for_five_minutes(client, monkeypatch):
    calls = _fema(monkeypatch, [GRB])
    for _ in range(3):
        client.get("/shelters?lat=29.76&lon=-95.37", headers=HEADERS)
    assert len(calls) == 1 and fema_shelters.TTL_SECONDS == 300


def test_failures_are_cached_briefly_so_a_slow_fema_cant_pile_up(client, monkeypatch):
    calls = _fema(monkeypatch, exc=fema_shelters.FemaUnavailable("timeout"))
    for _ in range(3):
        assert client.get("/public/shelters").json()["available"] is False
    assert len(calls) == 1 and fema_shelters.FAILURE_TTL_SECONDS == 60


def test_short_timeouts():
    assert fema_shelters.TIMEOUT.connect <= 3 and fema_shelters.TIMEOUT.read <= 6


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"error": {"code": 400, "message": "Unable to complete operation."}}),  # ArcGIS error with HTTP 200
    httpx.Response(503, text="busy"),
    httpx.Response(200, text="<html>not json</html>"),
])
def test_query_turns_fema_errors_into_unavailable(response):
    import asyncio

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response)) as c:
            await REAL_QUERY(c, 29.76, -95.37)

    with pytest.raises(fema_shelters.FemaUnavailable):
        asyncio.run(go())


def test_query_timeout_is_unavailable():
    import asyncio

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as c:
            await REAL_QUERY(c, 29.76, -95.37)

    with pytest.raises(fema_shelters.FemaUnavailable):
        asyncio.run(go())


def test_query_asks_fema_for_open_shelters_only():
    import asyncio
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(200, json={"features": [{"attributes": GRB}]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await REAL_QUERY(c, 29.76, -95.37)

    rows = asyncio.run(go())
    assert seen["where"] == "shelter_status='OPEN'" and seen["distance"] == "50" and seen["returnGeometry"] == "false"
    assert rows == [GRB]


def test_no_debug_prints_or_demo_code_left():
    import inspect

    src = inspect.getsource(fema_shelters)
    assert "print(" not in src and "def main" not in src and "__main__" not in src
