"""Central settings, read once from environment variables (and a .env file if present)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # optional convenience for local dev
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_shared_secret: str  # ElevenLabs tools send this in the X-API-Key header
    phone_hash_salt: str  # caller phone numbers are HMAC-hashed with this before storage
    allow_insecure_dev: bool  # lets the API run without secrets. Never set this in production.
    nws_user_agent: str  # NWS requires a User-Agent that identifies you
    poll_seconds: int
    disable_poller: bool
    harris_county_fips: str
    houston_lat: float
    houston_lon: float
    gauge_max_miles: float  # farther than this, a gauge says nothing about the caller's spot
    rise_elevated_ft_hr: float
    rise_high_ft_hr: float
    stale_minutes: int
    gemini_api_key: str
    gemini_model: str
    enable_backboard: bool
    backboard_api_key: str
    data_dir: Path
    enable_docs: bool  # /docs, /redoc, /openapi.json. Off in production.
    enable_admin: bool  # /admin/* (live/replay switch). Off in production.
    auth_fail_limit: int  # bad API keys from one IP within the window before it gets 429
    auth_fail_window_seconds: int


def load_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR", REPO_ROOT / "data"))
    return Settings(
        database_url=os.getenv("DATABASE_URL", f"sqlite:///{data_dir / 'app.db'}"),
        api_shared_secret=os.getenv("API_SHARED_SECRET", ""),
        phone_hash_salt=os.getenv("PHONE_HASH_SALT", ""),
        allow_insecure_dev=_bool("ALLOW_INSECURE_DEV"),
        nws_user_agent=os.getenv("NWS_USER_AGENT", "(orion-flood-dev, set-NWS_USER_AGENT@example.com)"),
        poll_seconds=int(os.getenv("POLL_SECONDS", "600")),
        disable_poller=_bool("DISABLE_POLLER"),
        harris_county_fips=os.getenv("HARRIS_COUNTY_FIPS", "48201"),
        houston_lat=float(os.getenv("HOUSTON_LAT", "29.7604")),
        houston_lon=float(os.getenv("HOUSTON_LON", "-95.3698")),
        # GAUGE_MAX_MILES; MAX_GAUGE_MILES is the old name, still read so existing .env files keep working.
        gauge_max_miles=float(os.getenv("GAUGE_MAX_MILES") or os.getenv("MAX_GAUGE_MILES") or "10"),
        # Heuristic thresholds. We do NOT have official flood-stage values per gauge yet.
        rise_elevated_ft_hr=float(os.getenv("RISE_ELEVATED_FT_HR", "0.5")),
        rise_high_ft_hr=float(os.getenv("RISE_HIGH_FT_HR", "1.5")),
        stale_minutes=int(os.getenv("STALE_MINUTES", "180")),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        enable_backboard=_bool("ENABLE_BACKBOARD"),
        backboard_api_key=os.getenv("BACKBOARD_API_KEY", ""),
        data_dir=data_dir,
        # Secure by default: docs and admin stay off unless explicitly enabled (local dev sets them to 1).
        enable_docs=_bool("ENABLE_DOCS"),
        enable_admin=_bool("ENABLE_ADMIN"),
        auth_fail_limit=int(os.getenv("AUTH_FAIL_LIMIT", "10")),
        auth_fail_window_seconds=int(os.getenv("AUTH_FAIL_WINDOW_SECONDS", "300")),
    )


settings = load_settings()
