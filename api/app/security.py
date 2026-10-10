"""Shared-secret auth for the tool endpoints and phone-number hashing."""
from __future__ import annotations

import hashlib
import hmac
import re

from fastapi import Header, HTTPException

from .config import settings


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    """ElevenLabs webhook tools send the secret in an X-API-Key header."""
    if settings.allow_insecure_dev and not settings.api_shared_secret:
        return
    if not settings.api_shared_secret or not x_api_key or not hmac.compare_digest(x_api_key, settings.api_shared_secret):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


def hash_caller(phone: str) -> str:
    """Normalize to digits, then HMAC-SHA256 with a secret salt. The raw number is never stored."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 7:
        raise ValueError("caller_id does not look like a phone number")
    if len(digits) == 11 and digits.startswith("1"):  # US/Canada: +1 713... and 713... are the same caller
        digits = digits[1:]
    return hmac.new(settings.phone_hash_salt.encode(), digits.encode(), hashlib.sha256).hexdigest()
