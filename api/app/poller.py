"""Background poller: fetch USGS gauges every few minutes and cache them in the database.

Callers never wait on USGS; they read the cached copy. One failure never crashes the loop.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .config import settings
from .db import SessionLocal, utcnow
from .service import prune_old, store_series
from .sources import usgs
from .state import STATE

log = logging.getLogger("poller")


async def poll_once(client: httpx.AsyncClient) -> int:
    series = await usgs.fetch_county_gauges(client, settings.harris_county_fips)
    with SessionLocal() as session:
        added = store_series(session, series)
        prune_old(session)
    STATE.last_poll = utcnow()
    STATE.last_poll_error = None
    log.info("poll ok: %d gauges, %d new readings", len(series), added)
    return added


async def poll_loop(client: httpx.AsyncClient) -> None:
    while True:
        if STATE.mode == "live":
            try:
                await poll_once(client)
            except Exception as e:  # keep going, record the error for /health
                STATE.last_poll_error = f"{type(e).__name__}: {e}"[:200]
                log.warning("poll failed: %s", STATE.last_poll_error)
        await asyncio.sleep(settings.poll_seconds)
