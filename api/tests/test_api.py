import dataclasses
from datetime import timedelta

from app import geo, risk, service
from app.config import settings
from app.db import Profile, SessionLocal, utcnow
from app.series import GaugeSeries
from app.service import store_series
from app.sources import nws

from .conftest import HEADERS

PHONE = "+1 (713) 555-0142"


# ---------- auth ----------
def test_health_is_public(client):
    assert client.get("/health").json()["ok"] is True


def test_tools_require_api_key(client):
    assert client.get("/flood-status?lat=29.76&lon=-95.37").status_code == 401
    assert client.get("/flood-status?lat=29.76&lon=-95.37", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/profile/lookup", json={"caller_id": PHONE}).status_code == 401
    assert client.get("/admin/mode").status_code == 401


def test_overview_is_public(client):
    assert client.get("/overview").status_code == 200  # alerts service unreachable in tests -> still answers


# ---------- replay path ----------
def _replay(client, index):
    r = client.post("/admin/mode", json={"mode": "replay", "replay": "sample_synthetic", "index": index}, headers=HEADERS)
    assert r.status_code == 200, r.text


def test_replay_escalates_and_is_labeled(client):
    _replay(client, 0)
    calm = client.get("/flood-status?lat=29.76&lon=-95.37", headers=HEADERS).json()
    assert calm["risk"] == "low" and calm["replay"]["synthetic"] is True

    _replay(client, 4)
    bad = client.get("/flood-status?lat=29.76&lon=-95.37", headers=HEADERS).json()
    assert bad["risk"] == "high"
    assert "replay" in bad["speak_hint"].lower() and "synthetic" in bad["speak_hint"].lower()
    assert bad["nearest_gauges"][0]["trend_ft_per_hr"] > 1.5
    assert "911" in bad["disclaimer"]


def test_replay_step(client):
    _replay(client, 0)
    r = client.post("/admin/replay/step?delta=2", headers=HEADERS).json()
    assert r["index"] == 2
    assert client.post("/admin/replay/step?delta=999", headers=HEADERS).json()["index"] == 6  # clamped


def test_replay_name_cannot_traverse_paths(client):
    r = client.post("/admin/mode", json={"mode": "replay", "replay": "../etc/passwd"}, headers=HEADERS)
    assert r.status_code == 400


def test_switch_back_to_live(client):
    _replay(client, 3)
    assert client.post("/admin/mode", json={"mode": "live"}, headers=HEADERS).json()["mode"] == "live"


# ---------- live path (network mocked) ----------
def _seed_rising(session):
    now = utcnow()
    s = GaugeSeries("T-1", "Test Gauge", 29.76, -95.37, [(now - timedelta(minutes=60), 4.0), (now - timedelta(minutes=1), 4.8)])
    store_series(session, [s])


def test_live_rising_gauge_is_elevated(client, monkeypatch):
    async def no_alerts(*a, **k):
        return []

    monkeypatch.setattr(nws, "alerts_for_point", no_alerts)
    with SessionLocal() as s:
        _seed_rising(s)
    r = client.get("/flood-status?lat=29.76&lon=-95.37", headers=HEADERS).json()
    assert r["mode"] == "live" and r["risk"] == "elevated"
    assert r["data_as_of"] is not None and "As of" in r["speak_hint"]


def test_live_with_no_data_is_unknown_not_low(client, monkeypatch):
    async def no_alerts(*a, **k):
        return []

    monkeypatch.setattr(nws, "alerts_for_point", no_alerts)
    assert client.get("/flood-status?lat=29.76&lon=-95.37", headers=HEADERS).json()["risk"] == "unknown"


def test_live_alert_service_failure_is_reported(client, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("nws down")

    monkeypatch.setattr(nws, "alerts_for_point", boom)
    with SessionLocal() as s:
        _seed_rising(s)
    r = client.get("/flood-status?lat=29.76&lon=-95.37", headers=HEADERS).json()
    assert r["alerts_available"] is False and r["risk"] == "elevated"  # rising gauge still counts


def test_store_series_is_idempotent(client):
    with SessionLocal() as s:
        now = utcnow()
        ser = GaugeSeries("T-2", "G", 29.7, -95.3, [(now, 1.0)])
        assert store_series(s, [ser]) == 1
        assert store_series(s, [ser]) == 0


# ---------- address handling ----------
def test_flood_status_needs_a_location(client):
    assert client.get("/flood-status", headers=HEADERS).status_code == 400


def test_unresolvable_address_asks_agent_to_reask(client, monkeypatch):
    async def none(*a, **k):
        return None

    monkeypatch.setattr(geo, "geocode", none)
    r = client.get("/flood-status?address=nowhere", headers=HEADERS).json()
    assert r["found"] is False and "cross street" in r["message"]


def test_flood_status_marks_area_lookups_approximate(client, monkeypatch):
    async def area(*a, **k):
        return geo.GeoPoint(29.7604, -95.3698, approximate=True)

    monkeypatch.setattr(geo, "geocode", area)
    r = client.get("/flood-status?address=downtown", headers=HEADERS).json()
    assert r["found"] is True and r["approximate"] is True


def test_flood_status_resolves_a_bayou_name_from_loaded_gauges(client, monkeypatch):
    async def no_alerts(*a, **k):
        return []

    cedar = GaugeSeries("08067505", "Cedar Bayou at I-10 nr Mont Belvieu, TX", 29.82151389, -94.9097056)
    monkeypatch.setattr(service, "current_data", lambda session: ([cedar], None, utcnow(), False, None))
    monkeypatch.setattr(nws, "alerts_for_point", no_alerts)
    r = client.get("/flood-status?address=near Cedar Bayou", headers=HEADERS).json()
    assert r["found"] is True and r["approximate"] is True
    assert r["location"] == {"lat": 29.82151389, "lon": -94.9097056}


def test_flood_status_with_coordinates_is_not_approximate(client):
    r = client.get("/flood-status?lat=29.76&lon=-95.37", headers=HEADERS).json()
    assert r["approximate"] is False


def test_bad_coordinates_rejected(client):
    assert client.get("/flood-status?lat=999&lon=-95", headers=HEADERS).status_code == 422


# ---------- shelters ----------

def test_shelters_empty_list_tells_agent_what_to_say(
    client, monkeypatch
):
    async def no_shelters(*args, **kwargs):
        return []

    # Mock FEMA so the test doesn't depend on live data.
    monkeypatch.setattr(
        "app.main.get_open_shelters",
        no_shelters
    )

    response = client.get(
        "/shelters?lat=29.76&lon=-95.37",
        headers=HEADERS
    )

    assert response.status_code == 200

    data = response.json()

    assert data["count"] == 0
    assert data["shelters"] == []
    assert data["sample_data"] is False
    assert "211" in data["message"]


# ---------- profiles: consent, hashing, forget ----------
def test_profile_requires_consent(client):
    r = client.post("/profile", json={"caller_id": PHONE, "consent": False, "needs": ["oxygen"]}, headers=HEADERS)
    assert r.status_code == 400


def test_profile_roundtrip_hashes_phone_and_can_be_forgotten(client):
    body = {"caller_id": PHONE, "consent": True, "language": "es", "name": "Maria", "needs": ["oxygen", "dog"], "notes": "second floor"}
    assert client.post("/profile", json=body, headers=HEADERS).json()["saved"] is True

    found = client.post("/profile/lookup", json={"caller_id": "713-555-0142"}, headers=HEADERS).json()  # different formatting, same number
    assert found["found"] is True and found["language"] == "es" and "oxygen" in found["needs"]

    with SessionLocal() as s:
        rows = s.query(Profile).all()
        assert len(rows) == 1 and len(rows[0].caller_hash) == 64
        assert "5550142" not in rows[0].caller_hash and "5550142" not in str(rows[0].notes)

    assert client.post("/profile/forget", json={"caller_id": PHONE}, headers=HEADERS).json()["deleted"] is True
    assert client.post("/profile/lookup", json={"caller_id": PHONE}, headers=HEADERS).json()["found"] is False


def test_profile_rejects_garbage_phone(client):
    r = client.post("/profile/lookup", json={"caller_id": "abcdefgh"}, headers=HEADERS)
    assert r.status_code == 400


# ---------- post-call triage ----------
def test_post_call_keyword_fallback_without_gemini(client):
    r = client.post("/post-call", json={"transcript": "I need oxygen and a ride to a shelter", "language": "en"}, headers=HEADERS).json()
    assert r["needs_help"] is True and r["urgency"] == "high" and "medical" in r["categories"] and r["method"] == "keywords"
    calls = client.get("/calls", headers=HEADERS).json()
    assert len(calls) == 1 and "transcript" not in calls[0]


def test_post_call_requires_transcript(client):
    assert client.post("/post-call", json={"transcript": ""}, headers=HEADERS).status_code == 422


# ---------- distance guard ----------
GAUGE_LAT, GAUGE_LON = 29.76, -95.37
MILES_PER_DEG_LAT = 69.09


def _seed_flat_gauge_and_quiet_alerts(monkeypatch):
    async def no_alerts(*a, **k):
        return []

    monkeypatch.setattr(nws, "alerts_for_point", no_alerts)
    now = utcnow()
    flat = GaugeSeries("T-2", "Flat Gauge", GAUGE_LAT, GAUGE_LON, [(now - timedelta(minutes=60), 4.0), (now - timedelta(minutes=1), 4.0)])
    with SessionLocal() as s:
        store_series(s, [flat])


def _status_miles_north(client, miles):
    lat = GAUGE_LAT + miles / MILES_PER_DEG_LAT
    return client.get(f"/flood-status?lat={lat}&lon={GAUGE_LON}", headers=HEADERS).json()


def test_point_3_miles_from_a_gauge_is_normal(client, monkeypatch):
    _seed_flat_gauge_and_quiet_alerts(monkeypatch)
    r = _status_miles_north(client, 3)
    assert r["found"] is True and r["risk"] == "low"
    assert r["nearest_gauge_miles"] == 3.0
    assert [v["site_id"] for v in r["nearest_gauges"]] == ["T-2"]
    assert "Flat Gauge, about 3 miles away" in r["speak_hint"]


def test_point_25_miles_from_any_gauge_is_unknown(client, monkeypatch):
    _seed_flat_gauge_and_quiet_alerts(monkeypatch)
    r = _status_miles_north(client, 25)
    assert r["found"] is True and r["risk"] == "unknown"
    assert r["nearest_gauge_miles"] == 25.0
    assert r["nearest_gauges"] == []
    assert any("about 25 miles away, too far to say anything about this spot" in x for x in r["reasons"])
    assert "The nearest stream gauge is about 25 miles away, too far" in r["speak_hint"]
    assert "risk level: unknown" in r["speak_hint"].lower()


def test_point_exactly_at_the_limit_is_normal_and_just_past_it_is_unknown(client, monkeypatch):
    _seed_flat_gauge_and_quiet_alerts(monkeypatch)
    lat = GAUGE_LAT + 10 / MILES_PER_DEG_LAT
    exact = geo.haversine_miles(lat, GAUGE_LON, GAUGE_LAT, GAUGE_LON)  # the precise distance, ~10 miles

    def limit(miles):
        patched = dataclasses.replace(settings, gauge_max_miles=miles)
        monkeypatch.setattr(service, "settings", patched)
        monkeypatch.setattr(risk, "settings", patched)

    limit(exact)
    at = client.get(f"/flood-status?lat={lat}&lon={GAUGE_LON}", headers=HEADERS).json()
    assert at["risk"] == "low" and len(at["nearest_gauges"]) == 1

    limit(exact - 0.001)
    past = client.get(f"/flood-status?lat={lat}&lon={GAUGE_LON}", headers=HEADERS).json()
    assert past["risk"] == "unknown" and past["nearest_gauges"] == []


def test_nearest_gauge_miles_is_null_when_there_are_no_gauges(client, monkeypatch):
    async def no_alerts(*a, **k):
        return []

    monkeypatch.setattr(nws, "alerts_for_point", no_alerts)
    r = client.get("/flood-status?lat=29.76&lon=-95.37", headers=HEADERS).json()
    assert "nearest_gauge_miles" in r and r["nearest_gauge_miles"] is None
    assert r["risk"] == "unknown"
