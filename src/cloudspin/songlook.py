"""The look of a song: a background and two accents pulled from its cover art.

Songs without art still get a look of their own, derived from the album or
title, so the deck changes colour whenever the music does.
"""

from __future__ import annotations

import colorsys
import hashlib
import io
from dataclasses import dataclass

from .color import RGB

THUMB = 24  # the cover is kept as a THUMB x THUMB swatch for the record label


@dataclass(frozen=True)
class SongLook:
    background: RGB
    accent: RGB
    accent2: RGB
    art: bytes | None = None  # THUMB * THUMB * 3 RGB bytes


def _rgb(h: float, s: float, v: float) -> RGB:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return (int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5))


def _hsv(c: RGB) -> tuple[float, float, float]:
    return colorsys.rgb_to_hsv(c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)


def _hue_gap(a: float, b: float) -> float:
    d = abs(a - b) % 1.0
    return min(d, 1.0 - d)


def _as_accent(c: RGB, floor: float = 0.84) -> RGB:
    """Keep a colour's hue but make it bright and calm enough to read on a dark deck."""
    h, s, v = _hsv(c)
    return _rgb(h, min(max(s, 0.36), 0.80), min(max(v, floor), 0.98))


def from_text(text: str) -> SongLook:
    digest = hashlib.sha1(text.encode("utf-8", "replace")).digest()
    hue = digest[0] / 256.0 + digest[1] / 65536.0
    turn = 0.08 + (digest[2] / 255.0) * 0.10
    return SongLook(
        background=_rgb(hue, 0.50, 0.125),
        accent=_rgb(hue, 0.52, 0.96),
        accent2=_rgb(hue + turn, 0.58, 0.92),
    )


def from_art(data: bytes) -> SongLook | None:
    """Read a cover image and pick colours from it. None if it can't be decoded."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image.draft("RGB", (160, 160))
        image = image.convert("RGB")
        art = image.resize((THUMB, THUMB), Image.Resampling.LANCZOS).tobytes()
        small = image.resize((40, 40), Image.Resampling.BILINEAR)
        quantized = small.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
        palette = quantized.getpalette() or []
        counts = quantized.getcolors() or []
    except Exception:
        return None
    total = float(sum(count for count, _ in counts)) or 1.0
    swatches: list[tuple[float, RGB]] = []
    for count, index in counts:
        rgb = tuple(palette[index * 3 : index * 3 + 3])
        if len(rgb) == 3:
            swatches.append((count / total, (int(rgb[0]), int(rgb[1]), int(rgb[2]))))
    if not swatches:
        return None

    def vivid(entry: tuple[float, RGB]) -> float:
        share, colour = entry
        _, s, v = _hsv(colour)
        if s < 0.16 or v < 0.16:
            return 0.0
        return (share**0.35) * s * (0.35 + v)

    dominant = max(swatches, key=lambda entry: entry[0])[1]
    ranked = sorted(swatches, key=vivid, reverse=True)
    if vivid(ranked[0]) <= 0.0:
        # A black-and-white cover: stay monochrome.
        return SongLook(background=(14, 14, 17), accent=(228, 230, 236), accent2=(152, 158, 172), art=art)
    first = ranked[0][1]
    hue = _hsv(first)[0]
    second = next(
        (colour for entry in ranked[1:] if vivid(entry) > 0.0 and _hue_gap(_hsv((colour := entry[1]))[0], hue) >= 0.07),
        None,
    )
    accent = _as_accent(first)
    accent2 = _as_accent(second, 0.78) if second is not None else _rgb(hue + 0.09, 0.58, 0.90)
    dh, ds, dv = _hsv(dominant)
    if ds < 0.14:
        dh, ds = hue, 0.22
    background = _rgb(dh, min(ds, 0.62) * 0.9, 0.125)
    return SongLook(background=background, accent=accent, accent2=accent2, art=art)


def look_for(title: str, artist: str, album: str, art: bytes | None) -> SongLook:
    if art:
        look = from_art(art)
        if look is not None:
            return look
    return from_text(f"{artist}\n{album}" if (artist or album) else title)
