"""Replay mode: play a recorded (or synthetic) storm through the SAME code path as live data.

Files live in data/replay/*.json:
  {"name": "...", "description": "...", "synthetic": true|false,
   "frames": [{"t": "ISO time", "gauges": [{"site_id","name","lat","lon","value_ft"}], "alerts": [{...}]}, ...]}
Always label replay clearly in the UI and in what the agent says. Never present it as live.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import settings
from .series import GaugeSeries

_NAME_OK = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class Replay:
    name: str
    description: str
    synthetic: bool
    frames: list[dict]


def replay_dir():
    return settings.data_dir / "replay"


def list_replays() -> list[str]:
    d = replay_dir()
    return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []


def load(name: str) -> Replay:
    if not _NAME_OK.match(name):
        raise ValueError("invalid replay name")
    path = replay_dir() / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(name)
    raw = json.loads(path.read_text())
    frames = raw.get("frames") or []
    if not frames:
        raise ValueError("replay has no frames")
    return Replay(name=raw.get("name", name), description=raw.get("description", ""), synthetic=bool(raw.get("synthetic", False)), frames=frames)


def _parse(t: str) -> datetime:
    return datetime.fromisoformat(t).astimezone(timezone.utc)


def snapshot(rp: Replay, index: int) -> tuple[list[GaugeSeries], list[dict], datetime]:
    """Gauge series built from frames 0..index, alerts from frame `index`, and the replay 'now'."""
    index = max(0, min(index, len(rp.frames) - 1))
    by_site: dict[str, GaugeSeries] = {}
    for fr in rp.frames[: index + 1]:
        when = _parse(fr["t"])
        for g in fr.get("gauges", []):
            s = by_site.setdefault(g["site_id"], GaugeSeries(g["site_id"], g["name"], g["lat"], g["lon"]))
            s.readings.append((when, float(g["value_ft"])))
    alerts = rp.frames[index].get("alerts", [])
    return list(by_site.values()), alerts, _parse(rp.frames[index]["t"])
