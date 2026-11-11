"""Settings in ``~/.config/cloudspin/config.toml``.

The file is yours to edit. Cloudspin only rewrites the handful of values it
changes from inside the app (city, colours, sky, volume) and keeps your
``[themes]`` and ``[moods]`` tables as they are.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def config_path() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(root) / "cloudspin" / "config.toml"


def cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(root) / "cloudspin"


@dataclass
class Config:
    city: str = ""
    latitude: float | None = None
    longitude: float | None = None
    country: str = ""
    units: str = "metric"  # or "imperial"
    colors: str = "sky"  # "sky", "cover", or a theme name
    sky: str = "song"  # "song", "weather", or a scene name
    music: str = "~/Music"
    volume: int = 70
    fps: int = 24
    listen: bool = True  # decode a little of each song to choose its sky
    themes: dict[str, dict[str, str]] = field(default_factory=dict)
    moods: dict[str, str] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or config_path()
        config = cls(path=path)
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
            return config
        for key in ("city", "country", "units", "colors", "sky", "music"):
            if isinstance(data.get(key), str):
                setattr(config, key, data[key])
        for key in ("latitude", "longitude"):
            if isinstance(data.get(key), (int, float)) and not isinstance(data.get(key), bool):
                setattr(config, key, float(data[key]))
        for key, lo, hi in (("volume", 0, 100), ("fps", 4, 60)):
            if isinstance(data.get(key), int) and not isinstance(data.get(key), bool):
                setattr(config, key, max(lo, min(hi, data[key])))
        if isinstance(data.get("listen"), bool):
            config.listen = data["listen"]
        if config.units not in ("metric", "imperial"):
            config.units = "metric"
        themes = data.get("themes")
        if isinstance(themes, dict):
            config.themes = {
                str(name): {str(k): str(v) for k, v in table.items() if isinstance(v, str)}
                for name, table in themes.items()
                if isinstance(table, dict)
            }
        moods = data.get("moods")
        if isinstance(moods, dict):
            config.moods = {str(word): str(scene) for word, scene in moods.items() if isinstance(scene, str)}
        return config

    def save(self) -> None:
        if self.path is None:
            return

        def quote(text: str) -> str:
            return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'

        lines = [
            "# Cloudspin settings. Edit freely; see the README for every option.",
            f"city = {quote(self.city)}",
        ]
        if self.latitude is not None and self.longitude is not None:
            lines += [f"latitude = {self.latitude:.4f}", f"longitude = {self.longitude:.4f}"]
        lines += [
            f"country = {quote(self.country)}",
            f"units = {quote(self.units)}",
            f"colors = {quote(self.colors)}",
            f"sky = {quote(self.sky)}",
            f"music = {quote(self.music)}",
            f"volume = {self.volume}",
            f"fps = {self.fps}",
            f"listen = {str(self.listen).lower()}",
        ]
        if self.moods:
            lines += ["", "[moods]"]
            lines += [f"{quote(word)} = {quote(scene)}" for word, scene in self.moods.items()]
        for name, table in self.themes.items():
            lines += ["", f"[themes.{quote(name)}]"]
            lines += [f"{key} = {quote(value)}" for key, value in table.items()]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass
