from app import risk


def g(name="Gauge", trend=None, age=10):
    return {"name": name, "trend_ft_per_hr": trend, "age_minutes": age}


def test_flash_flood_warning_is_high():
    r = risk.assess([g()], [{"event": "Flash Flood Warning"}])
    assert r["risk"] == "high" and "NWS Flash Flood Warning in effect" in r["reasons"]


def test_watch_is_elevated():
    assert risk.assess([g()], [{"event": "Flood Watch"}])["risk"] == "elevated"


def test_rising_gauge_thresholds():
    assert risk.assess([g(trend=0.6)], [])["risk"] == "elevated"
    assert risk.assess([g(trend=1.6)], [])["risk"] == "high"
    assert risk.assess([g(trend=0.2)], [])["risk"] == "low"


def test_low_requires_fresh_data_and_working_alert_service():
    r = risk.assess([g(trend=0.0)], [], alerts_ok=True)
    assert r["risk"] == "low"
    assert "not a guarantee" in r["caveat"].lower()


def test_unknown_when_no_gauges():
    assert risk.assess([], [], alerts_ok=True)["risk"] == "unknown"


def test_unknown_when_alert_service_down_and_no_other_signal():
    assert risk.assess([g(trend=0.0)], [], alerts_ok=False)["risk"] == "unknown"


def test_stale_gauges_are_ignored_for_trend_and_low():
    r = risk.assess([g(trend=3.0, age=600)], [], alerts_ok=True)
    assert r["risk"] == "unknown"  # a stale fast-rise is not trusted, and stale data cannot support 'low'


def test_alert_beats_missing_gauges():
    assert risk.assess([], [{"event": "Flood Warning"}], alerts_ok=True)["risk"] == "high"


def test_output_never_claims_safe():
    for views, alerts, ok in [([g(trend=0.0)], [], True), ([], [], True), ([g()], [{"event": "Flood Watch"}], True)]:
        r = risk.assess(views, alerts, ok)
        text = (r["caveat"] + " " + " ".join(r["reasons"])).lower()
        assert "is safe" not in text and "are safe" not in text and "it's safe" not in text


# ---------- distance guard (GAUGE_MAX_MILES, default 10) ----------
def test_gauge_3_miles_away_is_assessed_normally():
    r = risk.assess([g(trend=0.0)], [], alerts_ok=True, nearest_gauge_miles=3.0)
    assert r["risk"] == "low"


def test_gauge_25_miles_away_is_unknown_with_distance_reason():
    r = risk.assess([], [], alerts_ok=True, nearest_gauge_miles=25.0)
    assert r["risk"] == "unknown"
    assert "the nearest stream gauge is about 25 miles away, too far to say anything about this spot" in r["reasons"]


def test_gauge_exactly_at_the_limit_is_assessed_normally():
    r = risk.assess([g(trend=0.0)], [], alerts_ok=True, nearest_gauge_miles=10.0)
    assert r["risk"] == "low"
    assert not any("too far" in reason for reason in r["reasons"])


def test_official_warning_still_counts_when_gauges_are_too_far():
    r = risk.assess([], [{"event": "Flood Warning"}], alerts_ok=True, nearest_gauge_miles=25.0)
    assert r["risk"] == "high"


def test_miles_in_plain_words():
    assert risk.miles_words(0.0) == "less than a quarter mile"
    assert risk.miles_words(0.6) == "about half a mile"
    assert risk.miles_words(1.2) == "about a mile"
    assert risk.miles_words(3.04) == "about 3 miles"
    assert risk.miles_words(25.0) == "about 25 miles"
