"""Well-known Houston-area neighborhoods and landmarks, so callers can say "downtown" or "near NRG".

Coordinates are APPROXIMATE AREA CENTERS, not exact addresses. Answers based on them are for the
general area, and /flood-status marks them "approximate": true.
"""
from __future__ import annotations

import re

# (lat, lon), aliases
_RAW: list[tuple[tuple[float, float], list[str]]] = [
    ((29.7604, -95.3698), ["downtown"]),
    ((29.7420, -95.3777), ["midtown"]),
    ((29.7450, -95.3910), ["montrose"]),
    ((29.7980, -95.3980), ["the heights", "houston heights"]),
    ((29.7390, -95.4630), ["galleria", "the galleria", "uptown"]),
    ((29.7280, -95.3580), ["third ward", "3rd ward"]),
    ((29.7790, -95.3330), ["fifth ward", "5th ward"]),
    ((29.7450, -95.3250), ["east end"]),
    ((29.7072, -95.3971), ["medical center", "texas medical center", "tmc"]),
    ((29.7174, -95.4018), ["rice university", "rice"]),
    ((29.7199, -95.3422), ["university of houston", "uh"]),
    ((29.6847, -95.4107), ["nrg stadium", "nrg"]),
    ((29.7573, -95.3555), ["minute maid park", "minute maid", "daikin park"]),
    ((29.7508, -95.3621), ["toyota center"]),
    ((29.7533, -95.3594), ["discovery green"]),
    ((29.7620, -95.3880), ["buffalo bayou park"]),
    ((29.6454, -95.2789), ["hobby airport", "hobby", "william p hobby airport"]),
    ((29.9902, -95.3368), ["bush airport", "iah", "bush intercontinental", "intercontinental airport"]),
    ((29.7650, -95.4410), ["memorial park"]),
    ((29.7550, -95.4190), ["river oaks"]),
    ((29.7058, -95.4588), ["bellaire"]),
    ((29.7858, -95.8245), ["katy"]),
    ((29.6197, -95.6349), ["sugar land", "sugarland"]),
    ((29.6911, -95.2091), ["pasadena"]),
    ((29.7355, -94.9774), ["baytown"]),
    ((30.0560, -95.1880), ["kingwood"]),
    ((29.9691, -95.6972), ["cypress"]),
    ((30.1658, -95.4613), ["the woodlands", "woodlands"]),
]

_FILLER = {"in", "near", "the"}
_STATE_SUFFIX = {"tx", "texas"}


def normalize(text: str) -> str:
    """'Near the Heights, Houston, TX' -> 'heights houston'. Drops punctuation, filler words and a trailing state."""
    words = [w for w in re.sub(r"[^\w\s]", " ", text.lower()).split() if w not in _FILLER]
    while words and words[-1] in _STATE_SUFFIX:
        words.pop()
    return " ".join(words)


PLACES: dict[str, tuple[float, float]] = {normalize(alias): pt for pt, aliases in _RAW for alias in aliases}


def lookup(text: str) -> tuple[float, float] | None:
    """Exact match on the normalized name, then again without a trailing 'houston'
    (so 'downtown Houston' works but 'University of Houston' keeps its own entry)."""
    key = normalize(text)
    if key in PLACES:
        return PLACES[key]
    if key.endswith(" houston"):
        return PLACES.get(key.removesuffix(" houston"))
    return None
