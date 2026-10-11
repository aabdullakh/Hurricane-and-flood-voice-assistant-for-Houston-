"""Callers name places, not addresses: "Houston", "Katy", "Sugar Land". The agent's tools must resolve them,
explain clearly when they can't, and say plainly when a place is outside Greater Houston."""
import asyncio

import pytest

from app import geo
from app.sources import nws

from .conftest import HEADERS


@pytest.fixture()
def calls(monkeypatch):
    """Fake Census + Nominatim. Records every lookup; nothing reaches the network."""
    log = {"census": [], "nominatim": [], "settlements": {}, "landmarks": {}, "census_hits": {}}

    async def census(client, text):
        log["census"].append(text)
        return log["census_hits"].get(text)

    async def nominatim_search(client, params):
        log["nominatim"].append(dict(params))
        if params.get("featureType") == "settlement":
            return log["settlements"].get((params["q"], "viewbox" in params), [])
        hit = log["landmarks"].get(params["q"])
        return [hit] if hit else []

    async def no_alerts(*a, **k):
        return []

    monkeypatch.setattr(geo, "_census", census)
    monkeypatch.setattr(geo, "_nominatim_search", nominatim_search)
    monkeypatch.setattr(nws, "alerts_for_point", no_alerts)
    return log


def town(lat, lon, kind="town"):
    return {"lat": str(lat), "lon": str(lon), "addresstype": kind}


# ---------- city names resolve ----------
@pytest.mark.parametrize("name", ["Houston", "Katy", "Sugar Land", "Pearland", "The Woodlands", "Galveston",
                                  "Houston, Texas", "Katy, TX", "sugar land"])
def test_city_names_resolve_for_flood_status_and_shelters(client, calls, name):
    d = client.get("/flood-status", params={"location": name}, headers=HEADERS).json()
    assert d["found"] is True and d["approximate"] is True and geo.in_area(d["location"]["lat"], d["location"]["lon"])
    s = client.get("/shelters", params={"location": name}, headers=HEADERS).json()
    assert s.get("found", True) is not False and "location" in s
    assert calls["nominatim"] == [] and calls["census"] == []  # common cities answer instantly from the table


def test_galveston_is_the_city_not_a_road_or_the_county(client, calls):
    d = client.get("/flood-status", params={"address": "Galveston"}, headers=HEADERS).json()
    assert abs(d["location"]["lat"] - 29.306) < 0.02 and abs(d["location"]["lon"] + 94.793) < 0.02


def test_other_city_falls_back_to_a_bounded_nominatim_place_lookup(client, calls):
    calls["settlements"][("Seabrook", True)] = [town(29.5641, -95.0255)]
    d = client.get("/flood-status", params={"location": "Seabrook"}, headers=HEADERS).json()
    assert d["found"] is True and d["location"] == {"lat": 29.5641, "lon": -95.0255}
    q = calls["nominatim"][0]
    assert q["q"] == "Seabrook" and q["featureType"] == "settlement" and q["countrycodes"] == "us"
    assert q["viewbox"] == geo.HOUSTON_VIEWBOX and q["bounded"] == 1  # Greater Houston only


def test_place_lookup_prefers_the_town_over_a_county_of_the_same_name(client, calls):
    calls["settlements"][("Webster", True)] = [town(29.40, -95.00, "county"), town(29.5377, -95.1183, "city")]
    d = client.get("/flood-status", params={"location": "Webster"}, headers=HEADERS).json()
    assert d["location"] == {"lat": 29.5377, "lon": -95.1183}


def test_place_lookups_are_cached(client, calls):
    calls["settlements"][("Seabrook", True)] = [town(29.5641, -95.0255)]
    for _ in range(3):
        client.get("/flood-status", params={"location": "Seabrook"}, headers=HEADERS)
    assert len(calls["nominatim"]) == 1
    assert all(len(k) == 64 for k in geo._settlement_cache)  # keyed by a hash, never the text


def test_location_and_address_are_both_accepted(client, calls):
    assert client.get("/flood-status?location=Katy", headers=HEADERS).json()["found"] is True
    assert client.get("/flood-status?address=Katy", headers=HEADERS).json()["found"] is True
    assert client.get("/shelters?address=Katy", headers=HEADERS).json().get("found", True) is not False


# ---------- unknown place: helpful, structured message ----------
@pytest.mark.parametrize("path", ["/flood-status", "/shelters"])
def test_unknown_place_says_what_failed_and_what_works(client, calls, path):
    d = client.get(path, params={"location": "Zzqx Gardens"}, headers=HEADERS).json()
    assert d["found"] is False and d["reason"] == "not_found" and d["query"] == "Zzqx Gardens"
    assert "Zzqx Gardens" in d["message"]
    assert "77002" in d["message"] and "cross street" in d["message"] and "Katy" in d["message"]
    assert d["examples"] == ["a ZIP like 77002", "a cross street like Main St and Texas Ave", "a city like Katy"]


@pytest.mark.parametrize("path", ["/flood-status", "/shelters"])
def test_missing_location_is_a_clear_question_not_an_error(client, calls, path):
    r = client.get(path, headers=HEADERS)
    assert r.status_code == 200 and r.json()["reason"] == "missing_location" and "77002" in r.json()["message"]


# ---------- outside Greater Houston: say so plainly ----------
@pytest.mark.parametrize("path", ["/flood-status", "/shelters"])
def test_city_outside_the_area_is_named_as_such(client, calls, path):
    calls["settlements"][("Dallas", False)] = [town(32.7763, -96.7969, "city")]  # found only by the US-wide lookup
    d = client.get(path, params={"location": "Dallas"}, headers=HEADERS).json()
    assert d["found"] is False and d["reason"] == "outside_area"
    assert "outside the area" in d["message"] and "Dallas" in d["message"] and "Greater Houston" in d["message"]
    assert "risk" not in d and d.get("shelters", []) == []  # no empty or misleading data


def test_street_address_in_another_city_is_outside_the_area(client, calls):
    calls["census_hits"]["100 Main St, Dallas, TX"] = (32.78, -96.80)
    d = client.get("/flood-status", params={"location": "100 Main St, Dallas, TX"}, headers=HEADERS).json()
    assert d["reason"] == "outside_area"


def test_coordinates_outside_the_area_are_refused_plainly(client, calls):
    d = client.get("/flood-status?lat=32.78&lon=-96.80", headers=HEADERS).json()
    assert d["found"] is False and d["reason"] == "outside_area"
    assert client.get("/shelters?lat=32.78&lon=-96.80", headers=HEADERS).json()["reason"] == "outside_area"


def test_dallas_street_inside_houston_is_not_mistaken_for_the_city(client, calls):
    # A bounded *settlement* lookup never returns roads, so "Dallas" can't become a Dallas St in Houston.
    calls["landmarks"]["Dallas"] = None
    calls["settlements"][("Dallas", False)] = [town(32.7763, -96.7969, "city")]
    d = client.get("/flood-status", params={"location": "Dallas"}, headers=HEADERS).json()
    assert d["reason"] == "outside_area"
    assert [c["featureType"] for c in calls["nominatim"] if "featureType" in c] == ["settlement", "settlement"]


# ---------- Nominatim usage policy: at most 1 request per second, identifying User-Agent ----------
def test_nominatim_requests_are_spaced_one_second_apart(monkeypatch):
    import httpx

    monkeypatch.setattr(geo, "NOMINATIM_MIN_INTERVAL", 1.0)
    monkeypatch.setattr(geo, "_nominatim_last", 0.0)
    clock = [1000.0]
    waits, sent = [], []
    monkeypatch.setattr(geo.time, "monotonic", lambda: clock[0])

    async def fake_sleep(s):
        waits.append(round(s, 3))
        clock[0] += s

    monkeypatch.setattr(geo.asyncio, "sleep", fake_sleep)

    def handler(request):
        sent.append((clock[0], request.headers["User-Agent"]))
        return httpx.Response(200, json=[])

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await asyncio.gather(*(geo._nominatim_search(c, {"q": f"x{i}"}) for i in range(3)))

    asyncio.run(go())
    assert [t for t, _ in sent] == [1000.0, 1001.0, 1002.0]  # queued, one per second
    assert waits == [1.0, 1.0]
    assert all(ua == geo.settings.nws_user_agent for _, ua in sent)
