"""Per-IP rate limits behind Caddy: the limiter must see the REAL client, trust X-Forwarded-For only from Caddy,
and ignore the header from anyone else. Uses uvicorn's own ProxyHeadersMiddleware, configured like production."""
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import public
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
CADDY_IP = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["caddy"]["networks"]["backend"]["ipv4_address"]
PROD = ProxyHeadersMiddleware(app, trusted_hosts=CADDY_IP)  # what uvicorn builds from FORWARDED_ALLOW_IPS


def _from(peer_ip):
    return TestClient(PROD, client=(peer_ip, 50000))


@pytest.fixture()
def limit2(client, monkeypatch):  # `client` resets limiter state and the DB; FEMA is mocked by conftest
    monkeypatch.setattr(public.shelters_limit, "limit", 2)
    return public.shelters_limit


def test_limiter_keys_on_the_real_client_not_on_caddy(limit2):
    with _from(CADDY_IP) as c:
        real = {"X-Forwarded-For": "203.0.113.5"}
        assert [c.get("/public/shelters", headers=real).status_code for _ in range(3)] == [200, 200, 429]
        # A different real visitor behind the same Caddy is NOT blocked: the limit is per client, not per proxy.
        assert c.get("/public/shelters", headers={"X-Forwarded-For": "203.0.113.6"}).status_code == 200
    assert "203.0.113.5" in limit2.hits and CADDY_IP not in limit2.hits


def test_header_from_outside_caddy_is_ignored(limit2):
    with _from("198.51.100.7") as c:  # someone reaching the api directly (not possible in prod: port not published)
        for spoof in ("203.0.113.1", "203.0.113.2", "203.0.113.3"):  # rotating fake IPs to dodge the limit...
            last = c.get("/public/shelters", headers={"X-Forwarded-For": spoof}).status_code
    assert last == 429  # ...doesn't work: all counted against the real peer
    assert set(limit2.hits) == {"198.51.100.7"}


def test_forged_entry_before_caddy_cannot_pick_the_ip(limit2):
    # Even if a proxy APPENDED instead of replacing, uvicorn takes the rightmost untrusted entry (the one Caddy saw).
    with _from(CADDY_IP) as c:
        for fake in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
            last = c.get("/public/shelters", headers={"X-Forwarded-For": f"{fake}, 203.0.113.9"}).status_code
    assert last == 429 and set(limit2.hits) == {"203.0.113.9"}


def test_bad_key_limit_also_uses_the_real_client(client, monkeypatch):
    import dataclasses

    from app import security
    monkeypatch.setattr(security, "settings", dataclasses.replace(security.settings, auth_fail_limit=2))
    with _from(CADDY_IP) as c:
        bad = {"X-API-Key": "wrong", "X-Forwarded-For": "203.0.113.20"}
        assert [c.get("/shelters", headers=bad).status_code for _ in range(3)] == [401, 401, 429]
        assert c.get("/shelters", headers={"X-API-Key": "wrong", "X-Forwarded-For": "203.0.113.21"}).status_code == 401


def test_production_config_trusts_only_caddy():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert compose["services"]["api"]["environment"]["FORWARDED_ALLOW_IPS"] == CADDY_IP
    assert "ports" not in compose["services"]["api"]  # nothing but Caddy can reach the api
    dockerfile = (ROOT / "api" / "Dockerfile").read_text()
    assert "--proxy-headers" in dockerfile and '"*"' not in dockerfile and "--forwarded-allow-ips" not in dockerfile
    caddyfile = (ROOT / "Caddyfile").read_text()
    assert re.search(r"header_up X-Forwarded-For \{remote_host\}", caddyfile)
    assert "header_up -X-Real-IP" in caddyfile and "header_up -Forwarded" in caddyfile
