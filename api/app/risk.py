"""Risk assessment. Deliberately conservative: the system NEVER says an area is "safe".

IMPORTANT: this is a HEURISTIC. We do not yet have official flood-stage thresholds per gauge.
Rule of thumb used here:
  - an official NWS flood warning/emergency (or storm-surge/hurricane warning) => high
  - an official watch/advisory, or a fast-rising gauge                       => elevated
  - nothing in the data we have                                              => low (with a caveat)
  - not enough data to say anything                                          => unknown
    (including: nearest gauge farther than GAUGE_MAX_MILES; an official alert still counts)
"""
from __future__ import annotations

from .config import settings

HIGH_EVENTS = {
    "flash flood emergency",
    "flash flood warning",
    "flood warning",
    "storm surge warning",
    "hurricane warning",
}
ELEVATED_EVENTS = {
    "flash flood watch",
    "flood watch",
    "flood advisory",
    "urban and small stream flood advisory",
    "coastal flood warning",
    "coastal flood watch",
    "coastal flood advisory",
    "tropical storm warning",
    "tropical storm watch",
    "hurricane watch",
    "storm surge watch",
}

DISCLAIMER = (
    "Prototype decision-support, not official guidance. Data may be delayed or incomplete. "
    "In immediate danger, call 911."
)
LOW_CAVEAT = "No flood alerts or fast-rising gauges in the data available. This is not a guarantee of safety."


def miles_words(miles: float) -> str:
    """Distance as a person would say it: 'less than a quarter mile', 'about half a mile', 'about 25 miles'."""
    if miles < 0.25:
        return "less than a quarter mile"
    if miles < 0.75:
        return "about half a mile"
    if miles < 1.5:
        return "about a mile"
    return f"about {round(miles)} miles"


def too_far(nearest_gauge_miles: float | None) -> bool:
    return nearest_gauge_miles is not None and nearest_gauge_miles > settings.gauge_max_miles


def too_far_reason(nearest_gauge_miles: float) -> str:
    return f"the nearest stream gauge is {miles_words(nearest_gauge_miles)} away, too far to say anything about this spot"


def assess(gauge_views: list[dict], alerts: list[dict], alerts_ok: bool = True, nearest_gauge_miles: float | None = None) -> dict:
    """gauge_views: gauges within GAUGE_MAX_MILES. nearest_gauge_miles: distance to the nearest gauge at all."""
    reasons: list[str] = []
    level = "low"

    def bump(new: str) -> None:
        nonlocal level
        order = {"low": 0, "elevated": 1, "high": 2}
        if order[new] > order[level]:
            level = new

    for a in alerts:
        ev = (a.get("event") or "").strip().lower()
        if ev in HIGH_EVENTS:
            bump("high")
            reasons.append(f"NWS {a['event']} in effect")
        elif ev in ELEVATED_EVENTS:
            bump("elevated")
            reasons.append(f"NWS {a['event']} in effect")

    fresh = [g for g in gauge_views if g["age_minutes"] <= settings.stale_minutes]
    for g in fresh:
        t = g.get("trend_ft_per_hr")
        if t is None:
            continue
        if t >= settings.rise_high_ft_hr:
            bump("high")
            reasons.append(f"{g['name']} rising fast ({t:+.1f} ft/hr)")
        elif t >= settings.rise_elevated_ft_hr:
            bump("elevated")
            reasons.append(f"{g['name']} rising ({t:+.1f} ft/hr)")

    if level == "low":
        # Not enough signal to honestly say "low".
        if not alerts_ok:
            return _unknown("weather alert service unavailable", reasons)
        if too_far(nearest_gauge_miles):
            return _unknown(too_far_reason(nearest_gauge_miles), reasons)
        if not fresh:
            why = "no nearby gauge data" if not gauge_views else "nearby gauge data is stale"
            return _unknown(why, reasons)
        reasons.append("no flood alerts or fast-rising gauges found")
        return {"risk": "low", "reasons": reasons, "caveat": LOW_CAVEAT}

    return {"risk": level, "reasons": reasons, "caveat": DISCLAIMER}


def _unknown(why: str, reasons: list[str]) -> dict:
    return {
        "risk": "unknown",
        "reasons": reasons + [why],
        "caveat": "Not enough current data to assess this location. Check official sources and call 911 in an emergency.",
    }
