"""Shared-secret auth for the tool endpoints and phone-number hashing."""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections import deque

from fastapi import Header, HTTPException, Request

from .config import settings


# Bad API keys per client IP: {ip: deque of failure times}. In-process, single worker (see state.py).
_failures: dict[str, deque] = {}
_MAX_TRACKED_IPS = 10_000  # memory cap if someone sprays from many addresses


def _client_ip(request: Request) -> str:
    # Behind Caddy, uvicorn --proxy-headers sets this from X-Forwarded-For (Caddy overwrites that header,
    # so callers cannot spoof it). The API port itself is not published.
    return request.client.host if request.client else "unknown"


def _recent_failures(ip: str, now: float) -> deque:
    q = _failures.get(ip)
    if q is None:
        if len(_failures) >= _MAX_TRACKED_IPS:
            _failures.clear()
        q = _failures[ip] = deque()
    while q and now - q[0] > settings.auth_fail_window_seconds:
        q.popleft()
    return q


def require_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    """ElevenLabs webhook tools send the secret in an X-API-Key header.
    After AUTH_FAIL_LIMIT bad keys from one IP within the window, that IP gets 429 until the window passes."""
    if settings.allow_insecure_dev and not settings.api_shared_secret:
        return
    ip, now = _client_ip(request), time.monotonic()
    failures = _recent_failures(ip, now)
    if len(failures) >= settings.auth_fail_limit:
        retry = int(settings.auth_fail_window_seconds - (now - failures[0])) + 1
        raise HTTPException(status_code=429, detail="too many invalid API keys, try again later", headers={"Retry-After": str(retry)})
    if not settings.api_shared_secret or not x_api_key or not hmac.compare_digest(x_api_key, settings.api_shared_secret):
        failures.append(now)
        raise HTTPException(status_code=401, detail="invalid or missing API key")


def hash_caller(phone: str) -> str:
    """Normalize to digits, then HMAC-SHA256 with a secret salt. The raw number is never stored."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 7:
        raise ValueError("caller_id does not look like a phone number")
    if len(digits) == 11 and digits.startswith("1"):  # US/Canada: +1 713... and 713... are the same caller
        digits = digits[1:]
    return hmac.new(settings.phone_hash_salt.encode(), digits.encode(), hashlib.sha256).hexdigest()
