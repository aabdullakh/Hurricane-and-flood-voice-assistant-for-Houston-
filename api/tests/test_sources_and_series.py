import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.series import GaugeSeries, trend_ft_per_hr
from app.sources import nws, usgs

FIX = Path(__file__).parent / "fixtures"


def test_parse_usgs_drops_nodata_and_sites_without_coordinates():
    series = usgs.parse_iv(json.loads((FIX / "usgs_iv_sample.json").read_text()))
    assert len(series) == 1  # second site has no coordinates
    s = series[0]
    assert s.site_id == "00000001"
    assert [v for _, v in s.readings] == [4.0, 4.2, 5.1]  # -999999 dropped
    assert s.readings[0][0] == datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)  # -05:00 converted to UTC


def test_parse_usgs_tolerates_garbage():
    assert usgs.parse_iv({}) == []
    assert usgs.parse_iv({"value": {"timeSeries": [{"sourceInfo": {}}]}}) == []


def test_parse_nws_alerts():
    payload = {"features": [{"properties": {"event": "Flood Warning", "severity": "Severe", "headline": "h", "areaDesc": "Harris"}}]}
    out = nws.parse_alerts(payload)
    assert out[0]["event"] == "Flood Warning" and out[0]["area"] == "Harris"


def _r(minutes_ago, value, now):
    return (now - timedelta(minutes=minutes_ago), value)


def test_trend_over_about_one_hour():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    readings = [_r(60, 4.0, now), _r(30, 4.4, now), _r(0, 4.8, now)]
    assert abs(trend_ft_per_hr(readings) - 0.8) < 1e-9


def test_trend_none_when_not_enough_history():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert trend_ft_per_hr([]) is None
    assert trend_ft_per_hr([_r(0, 4.0, now)]) is None
    assert trend_ft_per_hr([_r(10, 4.0, now), _r(0, 4.5, now)]) is None  # only 10 minutes of history


def test_trend_uses_available_span_when_shorter_than_an_hour():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert abs(trend_ft_per_hr([_r(30, 4.0, now), _r(0, 4.5, now)]) - 1.0) < 1e-9


def test_gauge_series_dataclass_defaults():
    assert GaugeSeries("x", "n", 1.0, 2.0).readings == []
