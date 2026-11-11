import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cloudspin import weather

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "forecast.json").read_text())
DELHI = weather.Place("New Delhi", 28.61, 77.21, "IN")


def test_parse_reads_the_forecast():
    report = weather.parse(FIXTURE, DELHI)
    assert round(report.temperature) == 24
    assert report.description == "Partly cloudy"
    assert report.humidity == 46
    assert report.utc_offset == 19800
    assert report.sunrise == datetime(2026, 11, 11, 6, 41)
    assert report.next_sunrise == datetime(2026, 11, 12, 6, 42)
    assert [d.code for d in report.days][:4] == [2, 0, 3, 61]
    assert report.days[3].rain_chance == 65
    assert report.place.label == "New Delhi, IN"


def test_parse_rejects_nonsense():
    with pytest.raises(weather.WeatherError):
        weather.parse({"current": {}}, DELHI)


def test_local_time_uses_the_place_not_this_computer():
    report = weather.parse(FIXTURE, DELHI)
    assert report.local_now(0.0) == datetime(1970, 1, 1, 5, 30)


def test_daylight_through_a_day():
    rise, set_, tomorrow = datetime(2026, 1, 1, 6), datetime(2026, 1, 1, 18), datetime(2026, 1, 2, 6)
    noon = weather.daylight_at(datetime(2026, 1, 1, 12), rise, set_, tomorrow)
    assert noon.elevation == pytest.approx(1.0) and noon.sun_x == pytest.approx(0.5)
    dusk = weather.daylight_at(set_ - timedelta(minutes=5), rise, set_, tomorrow)
    assert 0 < dusk.elevation < 0.05 and dusk.sun_x > 0.98
    midnight = weather.daylight_at(datetime(2026, 1, 2, 0), rise, set_, tomorrow)
    assert midnight.elevation == pytest.approx(-1.0) and midnight.moon_x == pytest.approx(0.5)
    early = weather.daylight_at(datetime(2026, 1, 1, 3), rise, set_, tomorrow)
    assert early.elevation < 0 and early.moon_x == pytest.approx(0.75)


def test_codes_map_to_skies():
    assert weather.sky_for(0, 3).cover < 0.2
    assert weather.sky_for(3, 100).cover > 0.9
    assert weather.sky_for(65).rain == 1.0
    assert weather.sky_for(95).storm == 1.0
    assert weather.sky_for(73).snow > 0.5
    assert weather.sky_for(45).fog == 1.0
    assert weather.describe(1234) == "Unsettled"
    assert weather.icon_for(82) == "rain"


def test_compass_and_moon():
    assert [weather.compass(d) for d in (0, 44, 90, 200, 312, 359)] == ["N", "NE", "E", "S", "NW", "N"]
    new_moon = 947182440.0
    assert weather.moon_phase(new_moon) == pytest.approx(0.0)
    assert weather.moon_phase(new_moon + 29.530588853 * 86400 / 2) == pytest.approx(0.5)


def test_cache_round_trip(tmp_path):
    path = tmp_path / "weather.json"
    weather.save_cache(path, FIXTURE, DELHI, "metric")
    assert weather.load_cache(path, DELHI, "metric").humidity == 46
    assert weather.load_cache(path, DELHI, "imperial") is None
    assert weather.load_cache(path, weather.Place("Mumbai", 19.07, 72.87), "metric") is None
    assert weather.load_cache(path, DELHI, "metric", max_age=-1) is None


def test_network_failures_become_readable(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no route")

    monkeypatch.setattr(weather.urllib.request, "urlopen", boom)
    with pytest.raises(weather.WeatherError, match="connection"):
        weather.geocode("Delhi")


def test_geocode_and_fetch_build_the_right_requests(monkeypatch):
    seen = []

    class Reply:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(self.body).encode()

    def fake(request, timeout):
        seen.append(request.full_url)
        if "geocoding" in request.full_url:
            return Reply({"results": [{"name": "Delhi", "latitude": 28.65, "longitude": 77.23, "country_code": "IN"}]})
        return Reply(FIXTURE)

    monkeypatch.setattr(weather.urllib.request, "urlopen", fake)
    place = weather.geocode("  delhi ")
    assert (place.name, place.country) == ("Delhi", "IN")
    report = weather.fetch(place, "imperial")
    assert report.wind_unit == "mph"
    assert "name=delhi" in seen[0]
    assert "temperature_unit=fahrenheit" in seen[1] and "timezone=auto" in seen[1] and "weather_code" in seen[1]

    monkeypatch.setattr(weather.urllib.request, "urlopen", lambda r, timeout: Reply({}))
    with pytest.raises(weather.WeatherError, match="No place"):
        weather.geocode("Nowhereville")
