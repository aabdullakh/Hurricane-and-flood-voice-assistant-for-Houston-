"""Tiny in-process runtime state (single worker). Fine for a hackathon; use the DB/Redis if you scale out."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class State:
    mode: str = "live"  # "live" or "replay"
    replay_name: str | None = None
    replay_index: int = 0
    last_poll: datetime | None = None
    last_poll_error: str | None = None


STATE = State()
