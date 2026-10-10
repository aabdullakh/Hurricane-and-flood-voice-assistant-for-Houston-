"""Production protections: bad-key rate limit, admin endpoints off, API docs off."""
import dataclasses
import os
import subprocess
import sys
from pathlib import Path

from app import main, security

from .conftest import HEADERS

BAD = {"X-API-Key": "wrong"}


def _limit(monkeypatch, n=3, window=300):
    monkeypatch.setattr(security, "settings", dataclasses.replace(security.settings, auth_fail_limit=n, auth_fail_window_seconds=window))


# ---------- bad API keys ----------
def test_repeated_bad_keys_get_429(client, monkeypatch):
    _limit(monkeypatch, n=3)
    codes = [client.get("/shelters", headers=BAD).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]


def test_missing_key_counts_as_a_bad_key(client, monkeypatch):
    _limit(monkeypatch, n=2)
    codes = [client.get("/shelters").status_code for _ in range(3)]
    assert codes == [401, 401, 429]


def test_locked_out_ip_is_refused_even_with_the_right_key(client, monkeypatch):
    _limit(monkeypatch, n=2)
    for _ in range(2):
        client.get("/shelters", headers=BAD)
    r = client.get("/shelters", headers=HEADERS)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


def test_good_keys_never_count_against_the_limit(client, monkeypatch):
    _limit(monkeypatch, n=2)
    assert all(client.get("/shelters", headers=HEADERS).status_code == 200 for _ in range(5))
    assert client.get("/shelters", headers=BAD).status_code == 401


def test_lockout_ends_after_the_window(client, monkeypatch):
    _limit(monkeypatch, n=2, window=60)
    clock = [1000.0]
    monkeypatch.setattr(security.time, "monotonic", lambda: clock[0])
    for _ in range(2):
        client.get("/shelters", headers=BAD)
    assert client.get("/shelters", headers=HEADERS).status_code == 429
    clock[0] += 61
    assert client.get("/shelters", headers=HEADERS).status_code == 200


def test_public_endpoints_are_not_rate_limited(client, monkeypatch):
    _limit(monkeypatch, n=1)
    client.get("/shelters", headers=BAD)
    assert client.get("/health").status_code == 200


# ---------- admin endpoints ----------
def test_admin_endpoints_are_404_when_disabled(client, monkeypatch):
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, enable_admin=False))
    assert client.get("/admin/mode", headers=HEADERS).status_code == 404
    assert client.post("/admin/mode", json={"mode": "replay"}, headers=HEADERS).status_code == 404
    assert client.post("/admin/replay/step", headers=HEADERS).status_code == 404


def test_admin_endpoints_work_when_enabled(client):
    assert client.get("/admin/mode", headers=HEADERS).status_code == 200  # conftest sets ENABLE_ADMIN=1


# ---------- API docs (decided when the app is created, so check in a fresh process) ----------
def _docs_status(enable_docs: str) -> list[int]:
    code = (
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "with TestClient(app) as c:\n"
        "    print(*[c.get(p).status_code for p in ('/docs', '/redoc', '/openapi.json')])\n"
    )
    env = {**os.environ, "ENABLE_DOCS": enable_docs}
    api_dir = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-c", code], cwd=api_dir, env=env, capture_output=True, text=True, check=True)
    return [int(x) for x in out.stdout.split()]


def test_docs_are_404_by_default():
    assert _docs_status("0") == [404, 404, 404]


def test_docs_can_be_enabled_for_local_dev():
    assert _docs_status("1") == [200, 200, 200]
