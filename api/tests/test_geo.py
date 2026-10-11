import asyncio
import re

import httpx
import pytest

from app import geo, places
from app.series import GaugeSeries

CENSUS_MATCH = {"result": {"addressMatches": [{"coordinates": {"x": -95.3695, "y": 29.7607}}]}}


def _geocode_and_capture(address):
    """Runs geo.geocode against a mocked Census API and returns (result, address sent)."""
    sent = {}

    def handler(request):
        sent["address"] = request.url.params["address"]
        return httpx.Response(200, json=CENSUS_MATCH)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await geo.geocode(client, address)

    return asyncio.run(run()), sent["address"]


def test_geocode_appends_houston_when_no_state_or_zip():
    result, sent = _geocode_and_capture("901 Bagby St")
    assert sent == "901 Bagby St, Houston, TX"
    assert result == geo.GeoPoint(29.7607, -95.3695, approximate=False)


def test_geocode_leaves_address_with_zip_unchanged():
    result, sent = _geocode_and_capture("901 Bagby St 77002")
    assert sent == "901 Bagby St 77002"
    assert result == geo.GeoPoint(29.7607, -95.3695, approximate=False)


def test_geocode_treats_texas_ave_as_a_street_not_the_state():
    result, sent = _geocode_and_capture("Main and Texas Ave")
    assert sent == "Main and Texas Ave, Houston, TX"
    assert result == geo.GeoPoint(29.7607, -95.3695, approximate=False)


# --- ZIP-only input -----------------------------------------------------------

ZIPPO_77002 = {"post code": "77002", "places": [{"latitude": "29.7594", "longitude": "-95.3594"}]}
NOMINATIM_77002 = [{"lat": "29.7560", "lon": "-95.3650"}]


def _geocode_with(address, handler):
    """Runs geo.geocode with a mocked HTTP handler; returns (result, list of hosts called)."""
    calls = []

    def recording(request):
        calls.append(request)
        return handler(request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(recording)) as client:
            return await geo.geocode(client, address)

    return asyncio.run(run()), calls


@pytest.fixture(autouse=True)
def empty_caches(monkeypatch):
    monkeypatch.setattr(geo, "_zip_cache", {})
    monkeypatch.setattr(geo, "_search_cache", {})
    monkeypatch.setattr(geo, "_settlement_cache", {})


def test_zip_only_uses_zippopotam_not_census():
    def handler(request):
        assert request.url.host == "api.zippopotam.us"
        assert request.url.path == "/us/77002"
        return httpx.Response(200, json=ZIPPO_77002)

    result, calls = _geocode_with("77002", handler)
    assert result == geo.GeoPoint(29.7594, -95.3594, approximate=True)
    assert [c.url.host for c in calls] == ["api.zippopotam.us"]


def test_zip_with_city_and_state_still_uses_zip_lookup():
    result, calls = _geocode_with("77002, Houston, TX", lambda r: httpx.Response(200, json=ZIPPO_77002))
    assert result == geo.GeoPoint(29.7594, -95.3594, approximate=True)
    assert calls[0].url.host == "api.zippopotam.us"


def test_zip_result_is_cached():
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=ZIPPO_77002)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first = await geo.geocode(client, "77002")
            second = await geo.geocode(client, "77002 Houston")
        return first, second, calls

    first, second, calls = asyncio.run(run())
    assert first == second == geo.GeoPoint(29.7594, -95.3594, approximate=True)
    assert len(calls) == 1


def test_zippopotam_404_falls_back_to_nominatim_with_user_agent():
    def handler(request):
        if request.url.host == "api.zippopotam.us":
            return httpx.Response(404, json={})
        assert request.url.host == "nominatim.openstreetmap.org"
        assert request.url.params["postalcode"] == "77002"
        assert request.url.params["country"] == "US"
        assert request.headers["User-Agent"] == geo.settings.nws_user_agent
        return httpx.Response(200, json=NOMINATIM_77002)

    result, calls = _geocode_with("77002", handler)
    assert result == geo.GeoPoint(29.7560, -95.3650, approximate=True)
    assert [c.url.host for c in calls] == ["api.zippopotam.us", "nominatim.openstreetmap.org"]


def test_zippopotam_empty_places_falls_back_to_nominatim():
    def handler(request):
        if request.url.host == "api.zippopotam.us":
            return httpx.Response(200, json={"places": []})
        return httpx.Response(200, json=NOMINATIM_77002)

    result, _ = _geocode_with("77002", handler)
    assert result == geo.GeoPoint(29.7560, -95.3650, approximate=True)


def test_zip_not_found_anywhere_returns_none_and_is_not_cached():
    def handler(request):
        if request.url.host == "api.zippopotam.us":
            return httpx.Response(404, json={})
        return httpx.Response(200, json=[])

    result, _ = _geocode_with("00000", handler)
    assert result is None
    assert geo._zip_cache == {}


def test_street_address_with_zip_still_uses_census():
    result, calls = _geocode_with("901 Bagby St 77002", lambda r: httpx.Response(200, json=CENSUS_MATCH))
    assert result == geo.GeoPoint(29.7607, -95.3695, approximate=False)
    assert [c.url.host for c in calls] == ["geocoding.geo.census.gov"]


# --- neighborhoods and landmarks ---------------------------------------------

NO_CENSUS_MATCH = {"result": {"addressMatches": []}}
NOMINATIM_ASTRODOME = [{"lat": "29.6852", "lon": "-95.4078"}]


def _no_http(request):
    raise AssertionError(f"unexpected HTTP call to {request.url}")


@pytest.mark.parametrize("text", ["downtown Houston", "downtown", "Near downtown, Houston, TX", "in the Heights"])
def test_table_places_resolve_without_http(text):
    result, calls = _geocode_with(text, _no_http)
    assert result is not None and result.approximate is True
    assert calls == []


def test_downtown_houston_matches_downtown_entry():
    result, _ = _geocode_with("downtown Houston", _no_http)
    assert (result.lat, result.lon) == places.PLACES["downtown"]


def test_university_of_houston_keeps_its_own_entry():
    result, _ = _geocode_with("University of Houston", _no_http)
    assert (result.lat, result.lon) == places.PLACES["university of houston"]


def test_street_address_uses_census_not_table_or_nominatim():
    result, calls = _geocode_with("1001 Avenida de las Americas", lambda r: httpx.Response(200, json=CENSUS_MATCH))
    assert result == geo.GeoPoint(29.7607, -95.3695, approximate=False)
    assert [c.url.host for c in calls] == ["geocoding.geo.census.gov"]


def test_unknown_place_falls_back_to_bounded_nominatim_search():
    def handler(request):
        if request.url.host == "geocoding.geo.census.gov":
            return httpx.Response(200, json=NO_CENSUS_MATCH)
        assert request.url.host == "nominatim.openstreetmap.org"
        q = request.url.params
        if q.get("featureType") == "settlement":
            return httpx.Response(200, json=[])  # the Astrodome is not a city or town
        assert q["q"] == "Astrodome"
        assert q["viewbox"] == geo.HOUSTON_VIEWBOX and q["bounded"] == "1"
        assert q["countrycodes"] == "us" and q["limit"] == "1"
        assert request.headers["User-Agent"] == geo.settings.nws_user_agent
        return httpx.Response(200, json=NOMINATIM_ASTRODOME)

    result, calls = _geocode_with("Astrodome", handler)
    assert result == geo.GeoPoint(29.6852, -95.4078, approximate=True)
    assert [c.url.host for c in calls] == ["geocoding.geo.census.gov", "nominatim.openstreetmap.org", "nominatim.openstreetmap.org"]  # Census, city/town lookup, landmark search


def test_nominatim_place_result_is_cached():
    async def run():
        calls = []

        def handler(request):
            calls.append(request.url.host)
            if request.url.host == "geocoding.geo.census.gov":
                return httpx.Response(200, json=NO_CENSUS_MATCH)
            return httpx.Response(200, json=NOMINATIM_ASTRODOME)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await geo.geocode(client, "Astrodome")
            await geo.geocode(client, "near the Astrodome")
        return calls

    assert asyncio.run(run()).count("nominatim.openstreetmap.org") == 1


def test_nothing_found_anywhere_returns_none():
    def handler(request):
        if request.url.host == "geocoding.geo.census.gov":
            return httpx.Response(200, json=NO_CENSUS_MATCH)
        return httpx.Response(200, json=[])

    result, calls = _geocode_with("zzqx nowhere", handler)
    assert result is None
    assert [c.url.host for c in calls] == ["geocoding.geo.census.gov", "nominatim.openstreetmap.org", "nominatim.openstreetmap.org", "nominatim.openstreetmap.org"]  # Census, city/town, landmark, city/town elsewhere
    assert geo._search_cache == {}


# --- gauge and bayou names ------------------------------------------------------

# Real USGS names and coordinates (Harris County, Oct 2026).
GAUGES = [
    GaugeSeries("08067505", "Cedar Bayou at I-10 nr Mont Belvieu, TX", 29.82151389, -94.9097056),
    GaugeSeries("08068700", "Cypress Ck at Sharp Rd nr Hockley, TX", 29.92105823, -95.840229),
    GaugeSeries("08069000", "Cypress Ck nr Westfield, TX", 30.0357753, -95.428827),
    GaugeSeries("08072000", "Lk Houston nr Sheldon, TX", 29.91633316, -95.1413198),
    GaugeSeries("08073500", "Buffalo Bayou nr Addicks, TX", 29.7618958, -95.6057782),
    GaugeSeries("08074000", "Buffalo Bayou at Houston, TX", 29.76022829, -95.4085505),
    GaugeSeries("08074598", "Whiteoak Bayou at Main St, Houston, TX", 29.76661676, -95.358549),
    GaugeSeries("08074760", "Brays Bayou at Alief, TX", 29.7091197, -95.583),
    GaugeSeries("08074810", "Brays Bayou at Gessner Dr, Houston, TX", 29.6727317, -95.5282765),
    GaugeSeries("08075000", "Brays Bayou at Houston, TX", 29.69717469, -95.412162),
    GaugeSeries("08075110", "Brays Bayou at MLK Jr Blvd, Houston, TX", 29.71416667, -95.3388889),
]
BY_ID = {g.site_id: g for g in GAUGES}


def _geocode_gauges(text, handler=_no_http):
    calls = []

    def recording(request):
        calls.append(request)
        return handler(request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(recording)) as client:
            return await geo.geocode(client, text, GAUGES)

    return asyncio.run(run()), calls


def _at(site_id):
    g = BY_ID[site_id]
    return geo.GeoPoint(g.lat, g.lon, approximate=True)


def test_exact_gauge_name_matches_that_gauge():
    result, calls = _geocode_gauges("Cedar Bayou at I-10 nr Mont Belvieu, TX")
    assert result == _at("08067505")
    assert calls == []


def test_bayou_name_alone_matches_its_gauge():
    assert _geocode_gauges("cedar bayou")[0] == _at("08067505")


def test_several_matches_pick_the_gauge_closest_to_downtown():
    # Four Brays Bayou gauges; MLK Jr Blvd is the closest to downtown.
    assert _geocode_gauges("near Brays Bayou")[0] == _at("08075110")
    # Buffalo Bayou: "at Houston" (in town), not "nr Addicks" (far west).
    assert _geocode_gauges("Buffalo Bayou")[0] == _at("08074000")


def test_gauge_matching_ignores_case_punctuation_and_trailing_city():
    assert _geocode_gauges("BRAYS BAYOU, Houston, TX")[0] == _at("08075000")  # full name "Brays Bayou at Houston, TX"
    assert _geocode_gauges("Buffalo Bayou, Houston")[0] == _at("08074000")
    assert _geocode_gauges("the Whiteoak Bayou")[0] == _at("08074598")
    assert _geocode_gauges("white oak bayou")[0] == _at("08074598")


def test_usgs_abbreviations_are_expanded():
    assert _geocode_gauges("Cypress Creek")[0] == _at("08069000")  # "Cypress Ck"; Westfield is closer to downtown
    assert _geocode_gauges("Lake Houston")[0] == _at("08072000")  # "Lk Houston nr Sheldon"


def test_places_table_wins_over_gauge_names():
    result, _ = _geocode_gauges("Cypress")  # the town, not Cypress Ck
    assert (result.lat, result.lon) == places.PLACES["cypress"]


def test_street_containing_a_bayou_name_still_uses_census():
    result, calls = _geocode_gauges("1000 Buffalo Bayou Dr", lambda r: httpx.Response(200, json=CENSUS_MATCH))
    assert result == geo.GeoPoint(29.7607, -95.3695, approximate=False)
    assert [c.url.host for c in calls] == ["geocoding.geo.census.gov"]


def test_unknown_bayou_with_no_gauge_falls_through_to_census_and_nominatim():
    def handler(request):
        if request.url.host == "geocoding.geo.census.gov":
            return httpx.Response(200, json=NO_CENSUS_MATCH)
        return httpx.Response(200, json=[])

    result, calls = _geocode_gauges("Sims Bayou", handler)  # no Sims gauge in this fixture list
    assert result is None
    assert [c.url.host for c in calls] == ["geocoding.geo.census.gov", "nominatim.openstreetmap.org", "nominatim.openstreetmap.org", "nominatim.openstreetmap.org"]  # Census, city/town, landmark, city/town elsewhere


def test_caches_never_hold_the_searched_text():
    async def run():
        def handler(request):
            if request.url.host == "api.zippopotam.us":
                return httpx.Response(200, json=ZIPPO_77002)
            if request.url.host == "geocoding.geo.census.gov":
                return httpx.Response(200, json=NO_CENSUS_MATCH)
            return httpx.Response(200, json=NOMINATIM_ASTRODOME)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await geo.geocode(client, "77002")
            await geo.geocode(client, "Astrodome")

    asyncio.run(run())
    keys = list(geo._zip_cache) + list(geo._search_cache) + list(geo._settlement_cache)
    assert len(keys) == 2  # the ZIP, and "Astrodome" (answered here by the city/town lookup)
    assert all(re.fullmatch(r"[0-9a-f]{64}", k) for k in keys)  # SHA-256 hashes only
    assert not {"77002", "astrodome", "Astrodome"} & set(keys)
