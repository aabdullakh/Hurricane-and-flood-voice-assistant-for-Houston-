"""FastAPI app: the backend the ElevenLabs voice agent calls through server tools (webhooks)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import geo, memory, public, replay, service, triage
from .config import REPO_ROOT, settings
from .db import CallSummary, Profile, get_session, init_db, to_db, utcnow
from .poller import poll_loop
from .security import hash_caller, require_key
from .state import STATE
from .sources import fema_shelters

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("api")
# httpx logs every outgoing URL at INFO, which would put searched addresses and map points in the logs.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.allow_insecure_dev and (not settings.api_shared_secret or not settings.phone_hash_salt):
        raise RuntimeError("Set API_SHARED_SECRET and PHONE_HASH_SALT in .env (or ALLOW_INSECURE_DEV=1 for local dev only).")
    init_db()
    async with httpx.AsyncClient(headers={"User-Agent": settings.nws_user_agent}, follow_redirects=True) as client:
        app.state.client = client
        task = None if settings.disable_poller else asyncio.create_task(poll_loop(client))
        try:
            yield
        finally:
            if task:
                task.cancel()


_docs = {} if settings.enable_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="Houston Flood Voice Assistant API", version="0.1.0", lifespan=lifespan, **_docs)


def get_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.client


Auth = Depends(require_key)
app.include_router(public.router)  # read-only website data, mounted before the static files


def require_admin_enabled() -> None:
    """Admin endpoints look like they don't exist unless ENABLE_ADMIN=1."""
    if not settings.enable_admin:
        raise HTTPException(404, "Not Found")


Admin = Depends(require_admin_enabled)
Short = Annotated[str, StringConstraints(max_length=100)]


# ---------------------------------------------------------------- public
@app.get("/health")
def health():
    return {
        "ok": True,
        "mode": STATE.mode,
        "last_poll": None if STATE.last_poll is None else STATE.last_poll.isoformat(),
        "last_poll_error": STATE.last_poll_error,
    }


@app.get("/overview")
async def overview(session: Session = Depends(get_session), client: httpx.AsyncClient = Depends(get_client)):
    """Public data for the dashboard (no personal information)."""
    return await service.overview(session, client)


# ---------------------------------------------------------------- tools the agent calls
LOCATION_EXAMPLES = ["a ZIP like 77002", "a cross street like Main St and Texas Ave", "a city like Katy"]
AREA_NAME = "Greater Houston: Harris County and nearby cities like Katy, Sugar Land, Pearland, The Woodlands and Galveston"


def location_problem(reason: str, query: str | None = None) -> dict:
    """What went wrong with the caller's location, in words the agent can say, plus what works instead."""
    q = (query or "").strip()
    if reason == "outside_area":
        msg = (f"“{q}” is" if q else "That place is") + f" outside the area this service covers ({AREA_NAME}). " \
              "Tell the caller this line only has flood data for that area; for other places, check weather.gov or call 211."
    elif reason == "missing_location":
        msg = "Ask the caller where they are: a ZIP like 77002, a cross street like Main St and Texas Ave, or a city like Katy."
    else:
        msg = (f"I couldn't find “{q}” in the Houston area." if q else "I couldn't find that place.") + \
              " Ask for a ZIP like 77002, a cross street like Main St and Texas Ave, or a city like Katy."
    return {"found": False, "reason": reason, "query": q or None, "message": msg, "examples": LOCATION_EXAMPLES}


async def resolve_point(client, session, lat, lon, text) -> tuple[geo.GeoPoint | None, dict | None]:
    """lat/lon or free text -> (point, None), or (None, structured problem). 400 only for half a coordinate pair."""
    if (lat is None) != (lon is None):
        raise HTTPException(400, "provide both lat and lon, or a location")
    if lat is not None:
        if not geo.in_area(lat, lon):
            return None, location_problem("outside_area")
        return geo.GeoPoint(lat, lon, approximate=False), None
    if not text or not text.strip():
        return None, location_problem("missing_location")
    res = await geo.locate(client, text, service.current_data(session)[0])  # Houston-aware; the text is never kept
    if res.status != "found":
        return None, location_problem(res.status, text)
    return res.point, None


@app.get("/flood-status", dependencies=[Auth])
async def flood_status(
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
    address: str | None = Query(None, max_length=200),
    location: str | None = Query(None, max_length=200),  # same as address (tools may be set up with either name)
    session: Session = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_client),
):
    pt, problem = await resolve_point(client, session, lat, lon, address or location)
    if problem:
        return problem
    lat, lon, approximate = pt
    result = await service.build_status(session, client, lat, lon)
    result["location"] = {"lat": lat, "lon": lon}
    result["approximate"] = approximate  # ZIP / neighborhood / landmark: the answer is for the general area
    return result



SHELTERS_NONE = "FEMA lists no open shelters near you right now. Call 211."
SHELTERS_DOWN = "The FEMA shelter list isn't reachable right now. Tell the caller to call 211 to find an open shelter."
SHELTERS_FOUND = "FEMA lists these shelters as open. Distances are in a straight line. Tell the caller to confirm a shelter is open before going, or call 211."


@app.get("/shelters", dependencies=[Auth])
async def shelters(
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
    location: str | None = Query(None, max_length=200),
    address: str | None = Query(None, max_length=200),  # same as location
    limit: int = Query(3, ge=1, le=3),
    session: Session = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_client),
):
    """For the voice agent: up to 3 nearest shelters FEMA lists as OPEN, from lat/lon OR a location (ZIP, city, address)."""
    pt, problem = await resolve_point(client, session, lat, lon, location or address)
    if problem:
        return {"count": 0, "shelters": [], **problem}
    lat, lon = pt.lat, pt.lon
    try:
        res = await fema_shelters.open_shelters_near(client, lat, lon, limit)
    except fema_shelters.FemaUnavailable:
        return {"count": 0, "shelters": [], "available": False, "source": fema_shelters.SOURCE, "message": SHELTERS_DOWN}
    items = [{k: s[k] for k in ("name", "address", "distance_miles", "source")} for s in res["shelters"]]
    return {
        "count": len(items), "shelters": items, "available": True,
        "location": {"lat": round(lat, 2), "lon": round(lon, 2)},
        "source": res["source"], "checked_at": res["checked_at"], "radius_miles": res["radius_miles"],
        "message": SHELTERS_FOUND if items else SHELTERS_NONE,
    }


class ProfileIn(BaseModel):
    caller_id: str = Field(min_length=7, max_length=32)
    consent: bool
    language: str = Field("en", max_length=16)
    name: str | None = Field(None, max_length=100)
    needs: list[Short] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(None, max_length=500)


class CallerIn(BaseModel):
    caller_id: str = Field(min_length=7, max_length=32)


def _hash_or_400(caller_id: str) -> str:
    try:
        return hash_caller(caller_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/profile", dependencies=[Auth])
async def save_profile(body: ProfileIn, session: Session = Depends(get_session)):
    """Save household facts. Only with explicit consent. The phone number is hashed, never stored."""
    if not body.consent:
        raise HTTPException(400, "consent is required to store household information")
    h = _hash_or_400(body.caller_id)
    p = session.get(Profile, h)
    if p is None:
        p = Profile(caller_hash=h, consent_at=to_db(utcnow()))
    p.language, p.name, p.needs, p.notes = body.language, body.name, body.needs, body.notes
    session.add(p)
    session.commit()
    fact = f"Household facts shared by caller: language={p.language}; name={p.name}; needs={p.needs}; notes={p.notes}"
    remembered = await memory.remember(session, p, fact)
    return {"saved": True, "backboard": remembered}


@app.post("/profile/lookup", dependencies=[Auth])
async def lookup_profile(body: CallerIn, session: Session = Depends(get_session)):
    h = _hash_or_400(body.caller_id)
    p = session.get(Profile, h)
    if p is None:
        return {"found": False}
    recalled = await memory.recall(session, p)
    return {"found": True, "language": p.language, "name": p.name, "needs": p.needs, "notes": p.notes, "memory": recalled}


@app.post("/profile/forget", dependencies=[Auth])
def forget_profile(body: CallerIn, session: Session = Depends(get_session)):
    """'Forget me'. Deletes our copy. (Backboard assistant deletion is not implemented, see README.)"""
    h = _hash_or_400(body.caller_id)
    p = session.get(Profile, h)
    if p is None:
        return {"deleted": False}
    session.delete(p)
    session.commit()
    return {"deleted": True}


class PostCallIn(BaseModel):
    transcript: str = Field(min_length=1, max_length=20000)
    language: str = Field("en", max_length=16)


@app.post("/post-call", dependencies=[Auth])
async def post_call(body: PostCallIn, session: Session = Depends(get_session), client: httpx.AsyncClient = Depends(get_client)):
    """Triage a finished call. Stores only the structured result, NOT the transcript."""
    t = await triage.triage(client, body.transcript, body.language)
    session.add(
        CallSummary(
            created_at=to_db(utcnow()),
            language=t["language"],
            needs_help=t["needs_help"],
            urgency=t["urgency"],
            categories=t["categories"],
            summary=t["summary"],
        )
    )
    session.commit()
    return t


@app.get("/calls", dependencies=[Auth])
def recent_calls(limit: int = Query(20, ge=1, le=100), session: Session = Depends(get_session)):
    rows = session.scalars(select(CallSummary).order_by(CallSummary.id.desc()).limit(limit)).all()
    return [
        {"at": r.created_at.isoformat() + "Z", "language": r.language, "needs_help": r.needs_help, "urgency": r.urgency, "categories": r.categories, "summary": r.summary}
        for r in rows
    ]


# ---------------------------------------------------------------- admin: live/replay switch for the demo
class ModeIn(BaseModel):
    mode: Literal["live", "replay"]
    replay: str | None = None
    index: int = 0


@app.get("/admin/mode", dependencies=[Admin, Auth])
def get_mode():
    return {"mode": STATE.mode, "replay": STATE.replay_name, "index": STATE.replay_index, "available_replays": replay.list_replays()}


@app.post("/admin/mode", dependencies=[Admin, Auth])
def set_mode(body: ModeIn):
    if body.mode == "replay":
        name = body.replay or (replay.list_replays() or [None])[0]
        if not name:
            raise HTTPException(400, "no replay files found in data/replay")
        try:
            rp = replay.load(name)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(400, f"cannot load replay: {e}")
        STATE.mode, STATE.replay_name = "replay", name
        STATE.replay_index = max(0, min(body.index, len(rp.frames) - 1))
    else:
        STATE.mode, STATE.replay_name, STATE.replay_index = "live", None, 0
    return get_mode()


@app.post("/admin/replay/step", dependencies=[Admin, Auth])
def step_replay(delta: int = 1):
    if STATE.mode != "replay" or not STATE.replay_name:
        raise HTTPException(400, "not in replay mode")
    rp = replay.load(STATE.replay_name)
    STATE.replay_index = max(0, min(STATE.replay_index + delta, len(rp.frames) - 1))
    return {"index": STATE.replay_index, "frames": len(rp.frames), "t": rp.frames[STATE.replay_index]["t"]}


# ---------------------------------------------------------------- dashboard (static), mounted last
_web = REPO_ROOT / "web"
if _web.exists():
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")
