"""Small RGB helpers. Colours are plain ``(r, g, b)`` tuples of 0-255 ints."""

from __future__ import annotations

from collections.abc import Sequence

RGB = tuple[int, int, int]

WHITE: RGB = (255, 255, 255)
BLACK: RGB = (0, 0, 0)


def parse(value: str) -> RGB:
    """Parse ``#rgb`` or ``#rrggbb`` into an RGB tuple."""
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"not a hex colour: {value!r}")
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        raise ValueError(f"not a hex colour: {value!r}") from None


def to_hex(c: RGB) -> str:
    return f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def lerp(a: RGB, b: RGB, t: float) -> RGB:
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return (
        int(a[0] + (b[0] - a[0]) * t + 0.5),
        int(a[1] + (b[1] - a[1]) * t + 0.5),
        int(a[2] + (b[2] - a[2]) * t + 0.5),
    )


def scale(c: RGB, k: float) -> RGB:
    return (
        min(255, max(0, int(c[0] * k + 0.5))),
        min(255, max(0, int(c[1] * k + 0.5))),
        min(255, max(0, int(c[2] * k + 0.5))),
    )


def ramp(stops: Sequence[tuple[float, RGB]], t: float) -> RGB:
    """Piecewise-linear gradient through ``(position, colour)`` stops."""
    if t <= stops[0][0]:
        return stops[0][1]
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if t <= p1:
            return lerp(c0, c1, (t - p0) / (p1 - p0) if p1 > p0 else 0.0)
    return stops[-1][1]


def smoothstep(lo: float, hi: float, v: float) -> float:
    if hi == lo:
        return 0.0 if v < lo else 1.0
    t = clamp((v - lo) / (hi - lo))
    return t * t * (3.0 - 2.0 * t)


def approach(current: RGB, target: RGB, k: float) -> RGB:
    """Move a colour toward a target; snaps once it is a rounding step away."""
    if current == target:
        return current
    out = []
    for a, b in zip(current, target):
        step = (b - a) * k
        if abs(step) < 1.0:
            step = 1.0 if b > a else -1.0 if b < a else 0.0
        out.append(int(a + step))
    return (out[0], out[1], out[2])
