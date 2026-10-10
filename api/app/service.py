"""Business logic: store/read gauge data, build the flood-status answer the voice agent speaks from."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import geo, replay, risk
from .config import settings
from .db import GaugeReading, from_db, to_db, utcnow
from .series import GaugeSeries, gauge_view
from .sources import nws
from .state import STATE

CT = ZoneInfo("America/Chicago")


# ---------- storage ----------
def store_series(session: Session, series_list: list[GaugeSeries]) -> int:
    """Insert readings we have not seen yet. Keeps the ORIGINAL observation time of every reading."""
    fetched = to_db(utcnow())
    added = 0
    for s in series_list:
        if not s.readings:
            continue
        earliest = to_db(s.readings[0][0])
        existing = set(
            session.scalars(
                select(GaugeReading.observed_at).where(GaugeReading.site_id == s.site_id, GaugeReading.observed_at >= earliest)
            )
        )
        for t, v in s.readings:
            when = to_db(t)
            if when in existing:
                continue
            session.add(
                GaugeReading(site_id=s.site_id, site_name=s.name, lat=s.lat, lon=s.lon, level_ft=v, observed_at=when, fetched_at=fetched)
            )
            added += 1
    session.commit()
    return added


def prune_old(session: Session, hours: int = 48) -> None:
    session.execute(delete(GaugeReading).where(GaugeReading.observed_at < to_db(utcnow() - timedelta(hours=hours))))
    session.commit()


def series_from_db(session: Session, hours: int = 6) -> list[GaugeSeries]:
    since = to_db(utcnow() - timedelta(hours=hours))
    rows = session.scalars(select(GaugeReading).where(GaugeReading.observed_at >= since).order_by(GaugeReading.observed_at)).all()
    by_site: dict[str, GaugeSeries] = {}
    for r in rows:
        s = by_site.setdefault(r.site_id, GaugeSeries(r.site_id, r.site_name, r.lat, r.lon))
        s.readings.append((from_db(r.observed_at), r.level_ft))
    return list(by_site.values())


# ---------- current snapshot (live or replay: same downstream code path) ----------
def current_data(session: Session):
    """Returns (series, replay_alerts_or_None, now, synthetic, replay_name_or_None)."""
    if STATE.mode == "replay" and STATE.replay_name:
        rp = replay.load(STATE.replay_name)
        series, alerts, now = replay.snapshot(rp, STATE.replay_index)
        return series, alerts, now, rp.synthetic, rp.name
    return series_from_db(session), None, utcnow(), False, None


def fmt_time(dt: datetime) -> str:
    d = dt.astimezone(CT)
    return f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


async def _alerts(client: httpx.AsyncClient, lat: float, lon: float, replay_alerts):
    if replay_alerts is not None:
        return replay_alerts, True
    try:
        return await nws.alerts_for_point(client, lat, lon, settings.nws_user_agent), True
    except Exception:
        return [], False


# ---------- the main answer ----------
async def build_status(session: Session, client: httpx.AsyncClient, lat: float, lon: float) -> dict:
    series, replay_alerts, now, synthetic, replay_name = current_data(session)
    alerts, alerts_ok = await _alerts(client, lat, lon, replay_alerts)

    near = geo.nearest([s for s in series if s.readings], lat, lon, k=3, max_miles=settings.max_gauge_miles)
    views = [gauge_view(s, now, miles) for s, miles in near]
    assessment = risk.assess(views, alerts, alerts_ok)

    as_of = None
    if views:
        as_of = max(near[i][0].readings[-1][0] for i in range(len(near)))

    return {
        "found": True,
        "mode": STATE.mode,
        "replay": None if replay_name is None else {"name": replay_name, "synthetic": synthetic, "frame": STATE.replay_index},
        "risk": assessment["risk"],
        "reasons": assessment["reasons"],
        "caveat": assessment["caveat"],
        "data_as_of": None if as_of is None else as_of.isoformat(),
        "nearest_gauges": views,
        "alerts": [{k: a.get(k) for k in ("event", "severity", "headline", "area", "expires")} for a in alerts[:5]],
        "alerts_available": alerts_ok,
        "sources": ["USGS gauge readings", "National Weather Service alerts"] if replay_name is None else ["Replay data"],
        "disclaimer": risk.DISCLAIMER,
        "speak_hint": _speak_hint(views, alerts, assessment, replay_name, synthetic),
    }


def _speak_hint(views, alerts, assessment, replay_name, synthetic) -> str:
    parts: list[str] = []
    if replay_name:
        parts.append("This is a replay of recorded sample data, not live conditions." + (" The data is synthetic." if synthetic else ""))
    if views:
        g = views[0]
        obs = datetime.fromisoformat(g["observed_at"])
        sentence = f"As of {fmt_time(obs)}, the nearest gauge, {g['name']}, about {g['distance_miles']} miles away, reads {g['level_ft']} feet"
        t = g["trend_ft_per_hr"]
        if t is not None and abs(t) >= 0.1:
            sentence += f" and is {'rising' if t > 0 else 'falling'} about {abs(t):.1f} feet per hour"
        parts.append(sentence + ".")
    else:
        parts.append("I don't have a recent gauge reading near that location.")
    for a in alerts[:2]:
        if a.get("event"):
            parts.append(f"There is an active {a['event']}.")
    parts.append(f"Risk level: {assessment['risk']}. {assessment['caveat']}")
    return " ".join(parts)


async def overview(session: Session, client: httpx.AsyncClient) -> dict:
    """Everything the dashboard needs in one call. Public data only."""
    series, replay_alerts, now, synthetic, replay_name = current_data(session)
    alerts, alerts_ok = await _alerts(client, settings.houston_lat, settings.houston_lon, replay_alerts)
    gauges = [gauge_view(s, now) for s in series if s.readings]
    return {
        "mode": STATE.mode,
        "replay": None if replay_name is None else {"name": replay_name, "synthetic": synthetic, "frame": STATE.replay_index},
        "now": now.isoformat(),
        "last_poll": None if STATE.last_poll is None else STATE.last_poll.isoformat(),
        "last_poll_error": STATE.last_poll_error,
        "alerts_available": alerts_ok,
        "alerts": [{k: a.get(k) for k in ("event", "severity", "headline", "area", "expires")} for a in alerts[:10]],
        "gauges": gauges,
        "disclaimer": risk.DISCLAIMER,
    }
