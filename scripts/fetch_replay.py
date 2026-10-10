#!/usr/bin/env python3
"""Build a REAL-storm replay file from USGS historical gauge data. Run this on your laptop (needs internet).

    cd api && python ../scripts/fetch_replay.py --start 2019-09-17 --end 2019-09-21 --name imelda_2019

Writes data/replay/<name>.json in the format app/replay.py expects. Gauge data is real; ALERTS ARE EMPTY because the
NWS API only keeps about 7 days. Add real alerts by hand from public records (event, start time) if you want them in the
replay, and say so in your demo. Never present replay data as live.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))
from app.sources.usgs import USGS_IV, parse_iv  # noqa: E402


def build_frames(series, step_minutes: int, max_age_minutes: int = 120) -> list[dict]:
    times = [t for s in series for t, _ in s.readings]
    if not times:
        return []
    t, end = min(times), max(times)
    frames = []
    while t <= end:
        gauges = []
        for s in series:
            prior = [(rt, v) for rt, v in s.readings if rt <= t]
            if not prior:
                continue
            rt, v = prior[-1]
            if (t - rt) > timedelta(minutes=max_age_minutes):
                continue
            gauges.append({"site_id": s.site_id, "name": s.name, "lat": s.lat, "lon": s.lon, "value_ft": v})
        if gauges:
            frames.append({"t": t.isoformat(), "gauges": gauges, "alerts": []})
        t += timedelta(minutes=step_minutes)
    return frames


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--county", default="48201", help="county FIPS (Harris = 48201)")
    ap.add_argument("--step-minutes", type=int, default=60)
    ap.add_argument("--name", required=True)
    args = ap.parse_args()

    params = {
        "format": "json",
        "countyCd": args.county,
        "parameterCd": "00065",
        "startDT": args.start,
        "endDT": args.end,
    }
    print("Requesting USGS (this can be large)…")
    r = httpx.get(USGS_IV, params=params, timeout=120)
    r.raise_for_status()
    series = parse_iv(r.json())
    frames = build_frames(series, args.step_minutes)
    if not frames:
        sys.exit("No data returned for that range. Check dates/county.")
    out = {
        "name": args.name,
        "description": f"USGS gauge height for county {args.county}, {args.start} to {args.end}. REAL gauge data; alerts not included.",
        "synthetic": False,
        "frames": frames,
    }
    path = ROOT / "data" / "replay" / f"{args.name}.json"
    path.write_text(json.dumps(out))
    print(f"Wrote {path} with {len(frames)} frames, {len(series)} gauges.")


if __name__ == "__main__":
    main()
