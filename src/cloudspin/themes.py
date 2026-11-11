"""Colour for everything that isn't the sky.

Two themes are alive: ``sky`` takes its colours from whatever the sky is doing,
and ``cover`` takes them from the playing song's cover art. The rest are fixed
palettes, and you can add your own under ``[themes.<name>]`` in the config.
"""

from __future__ import annotations

from dataclasses import dataclass

from .color import RGB, approach, lerp, parse


@dataclass(frozen=True)
class Theme:
    name: str
    label: str
    accent: RGB | None = None
    accent2: RGB | None = None
    background: RGB | None = None
    disc: RGB | None = None  # record label, defaults to the accent
    disc2: RGB | None = None


BUILT_IN: dict[str, Theme] = {
    theme.name: theme
    for theme in (
        Theme("sky", "Colours follow the sky"),
        Theme("cover", "Colours follow the cover art"),
        Theme("amber", "Amber", (255, 184, 82), (255, 112, 84), (20, 14, 10)),
        Theme("orchid", "Orchid", (214, 150, 255), (255, 120, 190), (17, 11, 24)),
        Theme("lagoon", "Lagoon", (84, 214, 206), (96, 160, 255), (8, 17, 24)),
        Theme("moss", "Moss", (170, 220, 104), (72, 196, 150), (10, 17, 13)),
        Theme("rose", "Rose", (255, 140, 150), (255, 196, 140), (22, 12, 15)),
        Theme("mono", "Mono", (232, 232, 232), (140, 140, 140), (12, 12, 12)),
    )
}


def load_themes(custom: dict[str, dict[str, str]]) -> dict[str, Theme]:
    """Built-in themes plus any valid ones from the config file."""
    themes = dict(BUILT_IN)
    for name, table in custom.items():
        if name in ("sky", "cover"):
            continue
        try:
            colours = {
                key: parse(table[key])
                for key in ("accent", "accent2", "background", "disc", "disc2")
                if key in table
            }
        except ValueError:
            continue
        if "accent" not in colours:
            continue
        colours.setdefault("accent2", colours["accent"])
        themes[name] = Theme(name=name, label=name.capitalize(), **colours)
    return themes


@dataclass
class Palette:
    """The colours every widget paints with, resolved for this moment."""

    bg: RGB = (10, 14, 24)
    accent: RGB = (255, 202, 98)
    accent2: RGB = (112, 182, 255)
    disc: RGB = (255, 202, 98)
    disc2: RGB = (112, 182, 255)

    @property
    def text(self) -> RGB:
        return (238, 241, 246)

    @property
    def soft(self) -> RGB:
        return lerp(self.bg, self.text, 0.62)

    @property
    def faint(self) -> RGB:
        return lerp(self.bg, self.text, 0.34)

    @property
    def raised(self) -> RGB:
        return lerp(self.bg, self.text, 0.085)

    @property
    def lifted(self) -> RGB:
        return lerp(self.bg, self.text, 0.16)

    def key(self) -> tuple:
        return (self.bg, self.accent, self.accent2, self.disc, self.disc2)

    def ease_to(self, target: Palette, k: float) -> None:
        self.bg = approach(self.bg, target.bg, k)
        self.accent = approach(self.accent, target.accent, k)
        self.accent2 = approach(self.accent2, target.accent2, k)
        self.disc = approach(self.disc, target.disc, k)
        self.disc2 = approach(self.disc2, target.disc2, k)
