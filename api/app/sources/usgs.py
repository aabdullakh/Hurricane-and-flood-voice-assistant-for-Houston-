"""USGS Instantaneous Values service: gauge height (parameter 00065) for a county.

Docs: https://waterservices.usgs.gov/docs/instantaneous-values/
Readings are taken about every 15 minutes and TRANSMITTED roughly hourly, so data can lag. Always report its age.
The county filter is `countyCd` (5-digit FIPS). [VERIFY] from a laptop with scripts/smoke.sh.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from ..series import GaugeSeries

USGS_IV = "https://waterservices.usgs.gov/nwis/iv/"


def parse_iv(payload: dict) -> list[GaugeSeries]:
    """Parse the USGS JSON (WaterML-style) into GaugeSeries. Tolerant of missing or odd fields."""
    by_site: dict[str, GaugeSeries] = {}
    for ts in payload.get("value", {}).get("timeSeries", []):
        info = ts.get("sourceInfo", {})
        site_codes = info.get("siteCode") or [{}]
        site_id = site_codes[0].get("value")
        geo = info.get("geoLocation", {}).get("geogLocation", {})
        try:
            lat, lon = float(geo["latitude"]), float(geo["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not site_id:
            continue
        nodata = ts.get("variable", {}).get("noDataValue")
        series = by_site.setdefault(site_id, GaugeSeries(site_id, info.get("siteName", site_id), lat, lon))
        for block in ts.get("values", []):
            for v in block.get("value", []):
                try:
                    val = float(v["value"])
                    when = datetime.fromisoformat(v["dateTime"]).astimezone(timezone.utc)
                except (KeyError, TypeError, ValueError):
                    continue
                if nodata is not None and val == float(nodata):
                    continue
                series.readings.append((when, val))
    out = []
    for s in by_site.values():
        s.readings = sorted(set(s.readings))
        if s.readings:
            out.append(s)
    return out


async def fetch_county_gauges(client: httpx.AsyncClient, county_fips: str, period: str = "PT6H") -> list[GaugeSeries]:
    params = {
        "format": "json",
        "countyCd": county_fips,
        "parameterCd": "00065",
        "siteStatus": "active",
        "period": period,
    }
    r = await client.get(USGS_IV, params=params, timeout=20)
    r.raise_for_status()
    return parse_iv(r.json())
