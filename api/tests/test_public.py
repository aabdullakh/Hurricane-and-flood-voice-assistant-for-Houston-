"""Public website endpoints (/public/*): no key, Houston area only, rounded coordinates, rate limits, honest shelters."""
import httpx
import pytest

from app import geo, public, risk
from app.series import GaugeSeries
from app.sources import nws, openmeteo


async def _no_alerts(*a, **k):
    return []


async def _no_rain(*a, **k):
    return None


@pytest.fixture()
def quiet(monkeypatch):
    monkeypatch.setattr(nws, "alerts_for_point", _no_alerts)
    monkeypatch.setattr(openmeteo, "rain_near", _no_rain)


# ---------- conditions ----------
def test_conditions_needs_no_key_and_rounds_the_point(client, quiet):
    r = client.get("/public/conditions?lat=29.761234&lon=-95.369876")
    assert r.status_code == 200
    d = r.json()
    assert d["location"] == {"lat": 29.76, "lon": -95.37, "rounded_to": "about 1 km"}
    assert d["risk"] == "unknown"  # no gauge data in the test database
    assert set(d) >= {"risk", "reasons", "caveat", "nearest_gauges", "nearest_gauge_miles", "alerts", "rain", "shelters", "disclaimer"}
    assert "speak_hint" not in d  # the page writes its own text, in English or Spanish


def test_conditions_outside_houston_is_refused(client, quiet):
    r = client.get("/public/conditions?lat=40.71&lon=-74.0")  # New York
    assert r.status_code == 400 and r.json()["detail"] == "outside_area"


def test_conditions_rate_limit(client, quiet, monkeypatch):
    monkeypatch.setattr(public.conditions_limit, "limit", 2)
    codes = [client.get("/public/conditions?lat=29.76&lon=-95.37").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_conditions_include_rain_when_available(client, monkeypatch):
    async def rain(*a, **k):
        return {"observed_at": "2026-10-10T19:30", "kind": "rain", "rain_past_3h_in": 0.4, "rain_next_3h_in": 0.1, "source": "Open-Meteo weather model"}

    monkeypatch.setattr(nws, "alerts_for_point", _no_alerts)
    monkeypatch.setattr(openmeteo, "rain_near", rain)
    assert client.get("/public/conditions?lat=29.76&lon=-95.37").json()["rain"]["rain_past_3h_in"] == 0.4


# ---------- shelters: FEMA, OPEN only ----------
def _fema(monkeypatch, records=None, exc=None):
    from app.sources import fema_shelters

    async def q(*a, **k):
        if exc:
            raise exc
        return records or []

    monkeypatch.setattr(fema_shelters, "_query", q)


def test_public_shelters_empty_state_is_honest(client):
    d = client.get("/public/shelters").json()
    assert d["available"] is True and d["count"] == 0 and d["shelters"] == []
    assert d["message"] == "FEMA lists no open shelters near you right now. Call 211."
    assert d["source"].startswith("FEMA Open Shelters") and d["checked_at"] and d["sample_data"] is False


def test_public_shelters_lists_only_open_fema_shelters(client, monkeypatch):
    _fema(monkeypatch, [
        {"shelter_name": "George R. Brown Convention Center", "address": "1001 Avenida de las Americas", "city": "Houston", "state": "TX",
         "zip": "77010", "shelter_status": "OPEN", "latitude": 29.7522, "longitude": -95.3576, "wheelchair_accessible": "YES"},
        {"shelter_name": "Closed School", "shelter_status": "CLOSED", "latitude": 29.76, "longitude": -95.37},
    ])
    d = client.get("/public/shelters?lat=29.7612&lon=-95.3698").json()
    assert [s["name"] for s in d["shelters"]] == ["George R. Brown Convention Center"]
    s = d["shelters"][0]
    assert s["address"] == "1001 Avenida de las Americas, Houston, TX 77010" and s["accessible"] is True and s["accepts_pets"] is None
    assert s["source"].startswith("FEMA") and d["distance"] == "straight line" and d["message"] is None


def test_public_shelters_when_fema_is_down(client, monkeypatch):
    from app.sources import fema_shelters

    _fema(monkeypatch, exc=fema_shelters.FemaUnavailable("timeout"))
    d = client.get("/public/shelters").json()
    assert d["available"] is False and d["count"] == 0 and "211" in d["message"]


def test_conditions_still_work_when_fema_is_down(client, quiet, monkeypatch):
    from app.sources import fema_shelters

    _fema(monkeypatch, exc=fema_shelters.FemaUnavailable("timeout"))
    r = client.get("/public/conditions?lat=29.76&lon=-95.37")
    assert r.status_code == 200 and r.json()["shelters"]["available"] is False


def test_public_shelters_outside_houston_refused(client):
    assert client.get("/public/shelters?lat=40.71&lon=-74.0").status_code == 400


# ---------- geocode ----------
def test_geocode_is_post_only(client, monkeypatch):
    called = []

    async def fake(*a, **k):
        called.append(1)

    monkeypatch.setattr(geo, "geocode", fake)
    assert client.get("/public/geocode?q=downtown").status_code in (404, 405)  # falls through to the static site
    assert called == []  # the text never reached the geocoder, so it can't end up in a URL or log


def test_geocode_found_inside_houston(client, monkeypatch):
    async def fake(*a, **k):
        return geo.GeoPoint(29.760724, -95.369483, approximate=False)

    monkeypatch.setattr(geo, "geocode", fake)
    d = client.post("/public/geocode", json={"q": "901 Bagby St"}).json()
    assert d == {"found": True, "lat": 29.7607, "lon": -95.3695, "approximate": False}


def test_geocode_outside_houston_is_not_found(client, monkeypatch):
    async def fake(*a, **k):
        return geo.GeoPoint(40.71, -74.0, approximate=True)

    monkeypatch.setattr(geo, "geocode", fake)
    assert client.post("/public/geocode", json={"q": "New York"}).json() == {"found": False, "reason": "outside_area"}


def test_geocode_nothing_found(client, monkeypatch):
    async def fake(*a, **k):
        return None

    monkeypatch.setattr(geo, "geocode", fake)
    assert client.post("/public/geocode", json={"q": "zzqx"}).json() == {"found": False, "reason": "not_found"}


def test_geocode_rejects_empty_and_huge_input(client):
    assert client.post("/public/geocode", json={"q": "a"}).status_code == 422
    assert client.post("/public/geocode", json={"q": "x" * 201}).status_code == 422


# ---------- map tiles ----------
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def _tile_in_houston(z=12):
    x0, x1, y0, y1 = public._tile_range(z)
    return z, (x0 + x1) // 2, (y0 + y1) // 2


def _fake_upstream(monkeypatch, responses):
    calls = []

    async def fetch(client, z, x, y):
        calls.append((z, x, y))
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(public, "_fetch_tile", fetch)
    return calls


def _ok():
    return httpx.Response(200, content=PNG, headers={"content-type": "image/png", "cache-control": "max-age=92341"},
                          request=httpx.Request("GET", "https://tile.openstreetmap.org/x"))


def test_tile_is_proxied_then_served_from_cache(client, monkeypatch):
    calls = _fake_upstream(monkeypatch, [_ok()])
    z, x, y = _tile_in_houston()
    a = client.get(f"/public/tiles/{z}/{x}/{y}.png")
    b = client.get(f"/public/tiles/{z}/{x}/{y}.png")
    assert a.status_code == b.status_code == 200 and a.content == b.content == PNG
    assert a.headers["content-type"] == "image/png" and "max-age=" in a.headers["cache-control"]
    assert len(calls) == 1  # second one came from the cache


def test_tiles_outside_houston_or_zoom_are_refused(client, monkeypatch):
    calls = _fake_upstream(monkeypatch, [])
    assert client.get("/public/tiles/12/0/0.png").status_code == 404  # other side of the world
    assert client.get("/public/tiles/3/1/3.png").status_code == 404  # zoomed out too far
    z, x, y = _tile_in_houston(17)
    assert client.get(f"/public/tiles/{z}/{x}/{y}.png").status_code == 404  # zoomed in too far
    assert calls == []


def test_tile_upstream_failure_is_502_but_a_stale_tile_is_reused(client, monkeypatch):
    z, x, y = _tile_in_houston()
    _fake_upstream(monkeypatch, [httpx.ConnectError("down")])
    assert client.get(f"/public/tiles/{z}/{x}/{y}.png").status_code == 502
    _fake_upstream(monkeypatch, [_ok(), httpx.ConnectError("down")])
    client.get(f"/public/tiles/{z}/{x + 1}/{y}.png")
    public._tiles[(z, x + 1, y)] = (0, 9e12, PNG)  # expired but within the stale window
    assert client.get(f"/public/tiles/{z}/{x + 1}/{y}.png").content == PNG


def test_tile_upstream_cap_protects_openstreetmap(client, monkeypatch):
    monkeypatch.setattr(public, "UPSTREAM_PER_MINUTE", 1)
    _fake_upstream(monkeypatch, [_ok(), _ok()])
    z, x, y = _tile_in_houston()
    assert client.get(f"/public/tiles/{z}/{x}/{y}.png").status_code == 200
    assert client.get(f"/public/tiles/{z}/{x + 1}/{y}.png").status_code == 503


# ---------- overview additions ----------
def test_overview_has_gauge_states_alert_levels_and_thresholds(client, monkeypatch):
    async def warning(*a, **k):
        return [{"event": "Flood Warning", "severity": "Severe", "headline": "h", "area": "Harris"}]

    monkeypatch.setattr(nws, "alerts_for_point", warning)
    from datetime import timedelta

    from app.db import SessionLocal, utcnow
    from app.service import store_series

    now = utcnow()
    with SessionLocal() as s:
        store_series(s, [GaugeSeries("T-9", "Fast", 29.76, -95.37, [(now - timedelta(minutes=60), 4.0), (now - timedelta(minutes=1), 6.0)])])
    d = client.get("/overview").json()
    assert d["gauges"][0]["state"] == "rising_fast"
    assert d["alerts"][0]["level"] == "high"
    assert d["thresholds"]["rise_high_ft_hr"] == 1.5 and d["alerts_area"] == "central Houston"


def test_gauge_state_rules():
    v = lambda t, age=10: {"trend_ft_per_hr": t, "age_minutes": age}  # noqa: E731
    assert risk.gauge_state(v(2.0)) == "rising_fast"
    assert risk.gauge_state(v(0.6)) == "rising"
    assert risk.gauge_state(v(0.1)) == "not_rising"
    assert risk.gauge_state(v(-1.0)) == "not_rising"
    assert risk.gauge_state(v(None)) == "no_trend"
    assert risk.gauge_state(v(3.0, age=999)) == "no_recent_data"


# ---------- rain parsing ----------
def test_open_meteo_parse_uses_preceding_hour_totals():
    payload = {
        "current": {"time": "2026-10-10T19:30", "weather_code": 63, "precipitation": 0.02},
        "hourly": {"time": ["2026-10-10T16:00", "2026-10-10T17:00", "2026-10-10T18:00", "2026-10-10T19:00",
                            "2026-10-10T20:00", "2026-10-10T21:00", "2026-10-10T22:00"],
                   "precipitation": [0.5, 0.1, 0.2, 0.3, 0.05, 0.0, 0.1]},
    }
    d = openmeteo.parse(payload)
    assert d["kind"] == "rain"
    assert d["rain_past_3h_in"] == 0.6  # 17:00 + 18:00 + 19:00 (covers 16:00-19:00)
    assert d["rain_next_3h_in"] == 0.15  # 20:00 + 21:00 + 22:00
