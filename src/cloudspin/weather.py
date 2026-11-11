"""Weather from Open-Meteo (free, no API key) and how it maps onto the sky."""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .sky import Daylight, Weather

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 8.0

_CURRENT = (
    "temperature_2m,apparent_temperature,relative_humidity_2m,is_day,precipitation,"
    "weather_code,cloud_cover,wind_speed_10m,wind_direction_10m"
)
_DAILY = "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_probability_max"

# WMO weather code -> (description, icon, sky). Sky wind is filled in later.
_CODES: dict[int, tuple[str, str, Weather]] = {
    0: ("Clear sky", "clear", Weather(cover=0.0)),
    1: ("Mostly clear", "clear", Weather(cover=0.14)),
    2: ("Partly cloudy", "partly", Weather(cover=0.42)),
    3: ("Overcast", "cloud", Weather(cover=0.96)),
    45: ("Fog", "fog", Weather(cover=0.5, fog=1.0)),
    48: ("Freezing fog", "fog", Weather(cover=0.5, fog=1.0)),
    51: ("Light drizzle", "rain", Weather(cover=0.82, rain=0.16)),
    53: ("Drizzle", "rain", Weather(cover=0.88, rain=0.26)),
    55: ("Heavy drizzle", "rain", Weather(cover=0.92, rain=0.36)),
    56: ("Freezing drizzle", "rain", Weather(cover=0.88, rain=0.22)),
    57: ("Freezing drizzle", "rain", Weather(cover=0.92, rain=0.34)),
    61: ("Light rain", "rain", Weather(cover=0.9, rain=0.42)),
    63: ("Rain", "rain", Weather(cover=0.95, rain=0.68)),
    65: ("Heavy rain", "rain", Weather(cover=1.0, rain=1.0)),
    66: ("Freezing rain", "rain", Weather(cover=0.92, rain=0.5)),
    67: ("Freezing rain", "rain", Weather(cover=1.0, rain=0.9)),
    71: ("Light snow", "snow", Weather(cover=0.85, snow=0.35)),
    73: ("Snow", "snow", Weather(cover=0.92, snow=0.65)),
    75: ("Heavy snow", "snow", Weather(cover=1.0, snow=1.0)),
    77: ("Snow grains", "snow", Weather(cover=0.85, snow=0.3)),
    80: ("Light showers", "rain", Weather(cover=0.7, rain=0.48)),
    81: ("Showers", "rain", Weather(cover=0.8, rain=0.74)),
    82: ("Heavy showers", "rain", Weather(cover=0.95, rain=1.0)),
    85: ("Snow showers", "snow", Weather(cover=0.8, snow=0.5)),
    86: ("Heavy snow showers", "snow", Weather(cover=0.95, snow=0.9)),
    95: ("Thunderstorm", "storm", Weather(cover=1.0, rain=0.85, storm=1.0)),
    96: ("Thunderstorm with hail", "storm", Weather(cover=1.0, rain=1.0, storm=1.0)),
    99: ("Thunderstorm with hail", "storm", Weather(cover=1.0, rain=1.0, storm=1.0)),
}

class WeatherError(Exception):
    """A weather lookup failed; the message is written for the person using the app."""


@dataclass
class Place:
    name: str
    latitude: float
    longitude: float
    country: str = ""
    region: str = ""

    @property
    def label(self) -> str:
        return f"{self.name}, {self.country}" if self.country else self.name


@dataclass
class Day:
    day: date
    code: int
    high: float
    low: float
    rain_chance: int | None = None


@dataclass
class Report:
    place: Place
    units: str
    temperature: float
    feels_like: float
    humidity: int
    wind_speed: float
    wind_direction: float
    code: int
    cloud_cover: float
    high: float
    low: float
    sunrise: datetime  # local wall-clock time at the place
    sunset: datetime
    next_sunrise: datetime
    utc_offset: int  # seconds
    days: list[Day] = field(default_factory=list)
    fetched_at: float = 0.0

    @property
    def description(self) -> str:
        return describe(self.code)

    @property
    def wind_unit(self) -> str:
        return "mph" if self.units == "imperial" else "km/h"

    def local_now(self, now: float | None = None) -> datetime:
        stamp = time.time() if now is None else now
        return datetime.fromtimestamp(stamp, timezone.utc).replace(tzinfo=None) + timedelta(seconds=self.utc_offset)

    def sky(self) -> Weather:
        wind_kmh = self.wind_speed * 1.609 if self.units == "imperial" else self.wind_speed
        return sky_for(self.code, self.cloud_cover, wind_kmh)

    def daylight(self, now: float | None = None) -> Daylight:
        stamp = time.time() if now is None else now
        return daylight_at(self.local_now(stamp), self.sunrise, self.sunset, self.next_sunrise, moon_phase(stamp))


def describe(code: int) -> str:
    return _CODES.get(code, ("Unsettled", "cloud", Weather()))[0]


def icon_for(code: int) -> str:
    return _CODES.get(code, ("", "cloud", Weather()))[1]


def sky_for(code: int, cloud_cover: float | None = None, wind_kmh: float = 6.0) -> Weather:
    base = _CODES.get(code, ("", "cloud", Weather(cover=0.6)))[2]
    cover = base.cover
    if cloud_cover is not None and code in (0, 1, 2, 3):
        # Trust the measured cover for dry skies, but keep it in the band the code implies.
        cover = min(max(cloud_cover / 100.0, cover - 0.14), cover + 0.2)
    return Weather(
        cover=max(0.0, min(1.0, cover)),
        rain=base.rain,
        snow=base.snow,
        fog=base.fog,
        storm=base.storm,
        wind=max(1.0, wind_kmh),
    )


def compass(degrees: float) -> str:
    names = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    return names[int((degrees % 360) / 45.0 + 0.5) % 8]


def moon_phase(stamp: float | None = None) -> float:
    """Fraction through the lunar cycle: 0 new, 0.5 full."""
    stamp = time.time() if stamp is None else stamp
    known_new_moon = 947182440.0  # 2000-01-06 18:14 UTC
    return ((stamp - known_new_moon) / 86400.0 / 29.530588853) % 1.0


def daylight_at(
    now: datetime, sunrise: datetime, sunset: datetime, next_sunrise: datetime, phase: float = 0.5
) -> Daylight:
    if sunrise <= now < sunset:
        span = (sunset - sunrise).total_seconds() or 1.0
        p = (now - sunrise).total_seconds() / span
        return Daylight(elevation=math.sin(math.pi * p), sun_x=p, moon_x=1.0 if p < 0.5 else 0.0, moon_phase=phase)
    if now >= sunset:
        start, end = sunset, next_sunrise
    else:
        start, end = sunset - timedelta(days=1), sunrise
    span = (end - start).total_seconds() or 1.0
    p = min(1.0, max(0.0, (now - start).total_seconds() / span))
    return Daylight(elevation=-math.sin(math.pi * p), sun_x=1.0 if p < 0.5 else 0.0, moon_x=p, moon_phase=phase)


def clock_daylight(now: datetime | None = None) -> Daylight:
    """A sensible sky for the local clock when no place is known yet."""
    now = now or datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sunrise = midnight + timedelta(hours=6, minutes=15)
    sunset = midnight + timedelta(hours=18, minutes=30)
    return daylight_at(now, sunrise, sunset, sunrise + timedelta(days=1), moon_phase())


# -- network -----------------------------------------------------------------


def _get(url: str, params: dict[str, object]) -> dict:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": f"cloudspin/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise WeatherError(f"The weather service answered with an error ({error.code}).") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise WeatherError("Can't reach the weather service. Check your connection.") from error
    except ValueError as error:
        raise WeatherError("The weather service sent something unreadable.") from error


def geocode(name: str) -> Place:
    data = _get(GEOCODE_URL, {"name": name.strip(), "count": 1, "language": "en", "format": "json"})
    results = data.get("results") or []
    if not results:
        raise WeatherError(f"No place called “{name.strip()}” was found. Try a nearby city.")
    hit = results[0]
    return Place(
        name=str(hit.get("name", name)),
        latitude=float(hit["latitude"]),
        longitude=float(hit["longitude"]),
        country=str(hit.get("country_code", "") or ""),
        region=str(hit.get("admin1", "") or ""),
    )


def parse(data: dict, place: Place, units: str = "metric", fetched_at: float | None = None) -> Report:
    try:
        current = data["current"]
        daily = data["daily"]
        days = [
            Day(
                day=date.fromisoformat(daily["time"][i]),
                code=int(daily["weather_code"][i] or 0),
                high=float(daily["temperature_2m_max"][i]),
                low=float(daily["temperature_2m_min"][i]),
                rain_chance=(
                    int(daily["precipitation_probability_max"][i])
                    if daily.get("precipitation_probability_max")
                    and daily["precipitation_probability_max"][i] is not None
                    else None
                ),
            )
            for i in range(len(daily["time"]))
        ]
        sunrise = datetime.fromisoformat(daily["sunrise"][0])
        sunset = datetime.fromisoformat(daily["sunset"][0])
        if len(daily["sunrise"]) > 1:
            next_sunrise = datetime.fromisoformat(daily["sunrise"][1])
        else:
            next_sunrise = sunrise + timedelta(days=1)
        return Report(
            place=place,
            units=units,
            temperature=float(current["temperature_2m"]),
            feels_like=float(current.get("apparent_temperature", current["temperature_2m"])),
            humidity=int(current.get("relative_humidity_2m", 0) or 0),
            wind_speed=float(current.get("wind_speed_10m", 0.0) or 0.0),
            wind_direction=float(current.get("wind_direction_10m", 0.0) or 0.0),
            code=int(current.get("weather_code", 0) or 0),
            cloud_cover=float(current.get("cloud_cover", 0.0) or 0.0),
            high=days[0].high,
            low=days[0].low,
            sunrise=sunrise,
            sunset=sunset,
            next_sunrise=next_sunrise,
            utc_offset=int(data.get("utc_offset_seconds", 0) or 0),
            days=days,
            fetched_at=time.time() if fetched_at is None else fetched_at,
        )
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise WeatherError("The weather service sent something unreadable.") from error


# -- a small disk cache so the sky is right the moment the app opens ---------


def save_cache(path: Path, data: dict, place: Place, units: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"saved_at": time.time(), "units": units, "place": asdict(place), "data": data}
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def load_cache(path: Path, place: Place, units: str, max_age: float = 3 * 3600) -> Report | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        saved_at = float(payload["saved_at"])
        if time.time() - saved_at > max_age or payload.get("units") != units:
            return None
        cached = Place(**payload["place"])
        if abs(cached.latitude - place.latitude) > 0.01 or abs(cached.longitude - place.longitude) > 0.01:
            return None
        return parse(payload["data"], place, units, fetched_at=saved_at)
    except (OSError, ValueError, KeyError, TypeError, WeatherError):
        return None


def fetch(place: Place, units: str = "metric", cache: Path | None = None) -> Report:
    params: dict[str, object] = {
        "latitude": f"{place.latitude:.4f}",
        "longitude": f"{place.longitude:.4f}",
        "current": _CURRENT,
        "daily": _DAILY,
        "timezone": "auto",
        "forecast_days": 7,
    }
    if units == "imperial":
        params["temperature_unit"] = "fahrenheit"
        params["wind_speed_unit"] = "mph"
    data = _get(FORECAST_URL, params)
    report = parse(data, place, units)
    if cache is not None:
        save_cache(cache, data, place, units)
    return report
