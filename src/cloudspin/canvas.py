"""A truecolor pixel canvas drawn with half-block characters.

Every terminal cell holds two stacked pixels: the upper one is the foreground
of a ``▀`` and the lower one is the background. Pixels live in three flat
bytearrays so whole rows and columns can be copied or recoloured in C.
"""

from __future__ import annotations

import math

from rich.cells import cell_len
from rich.color import Color
from rich.segment import Segment
from rich.style import Style

from .color import RGB

_PIXEL_STYLES: dict[int, Style] = {}
_TEXT_STYLES: dict[tuple, Style] = {}
_CACHE_LIMIT = 60_000

#: Per-row text overlay: cell x -> (character, packed fg, bold) or None for
#: the trailing half of a double-width character.
RowText = dict[int, tuple[str, int, bool] | None]


def pack(c: RGB) -> int:
    return (c[0] << 16) | (c[1] << 8) | c[2]


def text_style(
    fg: RGB | None,
    bg: RGB | None,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
) -> Style:
    key = (fg, bg, bold, italic, underline)
    style = _TEXT_STYLES.get(key)
    if style is None:
        if len(_TEXT_STYLES) > _CACHE_LIMIT:
            _TEXT_STYLES.clear()
        style = Style(
            color=Color.from_rgb(*fg) if fg is not None else None,
            bgcolor=Color.from_rgb(*bg) if bg is not None else None,
            bold=bold or None,
            italic=italic or None,
            underline=underline or None,
        )
        _TEXT_STYLES[key] = style
    return style


def _pixel_style(fg: int, bg: int) -> Style:
    key = (fg << 24) | bg
    style = _PIXEL_STYLES.get(key)
    if style is None:
        if len(_PIXEL_STYLES) > _CACHE_LIMIT:
            _PIXEL_STYLES.clear()
        style = Style(
            color=Color.from_rgb(fg >> 16, (fg >> 8) & 255, fg & 255),
            bgcolor=Color.from_rgb(bg >> 16, (bg >> 8) & 255, bg & 255),
        )
        _PIXEL_STYLES[key] = style
    return style


def _fill_style(bg: int) -> Style:
    key = -1 - bg
    style = _PIXEL_STYLES.get(key)
    if style is None:
        if len(_PIXEL_STYLES) > _CACHE_LIMIT:
            _PIXEL_STYLES.clear()
        style = Style(bgcolor=Color.from_rgb(bg >> 16, (bg >> 8) & 255, bg & 255))
        _PIXEL_STYLES[key] = style
    return style


def lay_text(rows: dict[int, RowText], x: int, row: int, text: str, fg: RGB, bold: bool = False) -> int:
    """Place text into an overlay map; returns the cell width that was used."""
    target = rows.setdefault(row, {})
    packed = pack(fg)
    start = x
    for ch in text:
        width = cell_len(ch)
        if width <= 0:
            continue
        target[x] = (ch, packed, bold)
        if width == 2:
            target[x + 1] = None
        x += width
    return x - start


class Canvas:
    __slots__ = ("w", "h", "r", "g", "b")

    def __init__(self, w: int = 0, h: int = 0) -> None:
        self.w = 0
        self.h = 0
        self.r = bytearray()
        self.g = bytearray()
        self.b = bytearray()
        self.resize(w, h)

    def resize(self, w: int, h: int) -> None:
        self.w = max(0, w)
        self.h = max(0, h)
        n = self.w * self.h
        self.r = bytearray(n)
        self.g = bytearray(n)
        self.b = bytearray(n)

    # -- whole-buffer operations -------------------------------------------

    def fill(self, c: RGB) -> None:
        n = self.w * self.h
        self.r[:] = bytes((c[0],)) * n
        self.g[:] = bytes((c[1],)) * n
        self.b[:] = bytes((c[2],)) * n

    def blit(self, other: Canvas) -> None:
        self.r[:] = other.r
        self.g[:] = other.g
        self.b[:] = other.b

    def fill_rows(self, colors: list[RGB]) -> None:
        """Paint one colour per pixel row, top to bottom."""
        w = self.w
        for y, c in enumerate(colors[: self.h]):
            i = y * w
            self.r[i : i + w] = bytes((c[0],)) * w
            self.g[i : i + w] = bytes((c[1],)) * w
            self.b[i : i + w] = bytes((c[2],)) * w

    def lighten(self, amount: int) -> None:
        """Add a flat amount of white to every pixel (lightning)."""
        if amount <= 0:
            return
        table = bytes(min(255, v + amount) for v in range(256))
        self.r[:] = self.r.translate(table)
        self.g[:] = self.g.translate(table)
        self.b[:] = self.b.translate(table)

    def remap_column(self, x: int, y_end: int, table: bytes) -> None:
        """Run the pixels of column x, rows [0, y_end), through a lookup table."""
        if not (0 <= x < self.w) or y_end <= 0:
            return
        end = min(y_end, self.h) * self.w
        w = self.w
        self.r[x:end:w] = self.r[x:end:w].translate(table)
        self.g[x:end:w] = self.g[x:end:w].translate(table)
        self.b[x:end:w] = self.b[x:end:w].translate(table)

    def fill_column(self, x: int, y_start: int, c: RGB) -> None:
        """Fill column x from row y_start down to the bottom edge."""
        if not (0 <= x < self.w):
            return
        y_start = max(0, y_start)
        n = self.h - y_start
        if n <= 0:
            return
        start = y_start * self.w + x
        w = self.w
        self.r[start::w] = bytes((c[0],)) * n
        self.g[start::w] = bytes((c[1],)) * n
        self.b[start::w] = bytes((c[2],)) * n

    # -- single pixels -----------------------------------------------------

    def put(self, x: int, y: int, c: RGB) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            i = y * self.w + x
            self.r[i] = c[0]
            self.g[i] = c[1]
            self.b[i] = c[2]

    def blend(self, x: int, y: int, c: RGB, a: int) -> None:
        """Alpha-blend a colour onto a pixel; ``a`` runs 0..256."""
        if a > 0 and 0 <= x < self.w and 0 <= y < self.h:
            i = y * self.w + x
            v = self.r[i]
            self.r[i] = v + ((c[0] - v) * a >> 8)
            v = self.g[i]
            self.g[i] = v + ((c[1] - v) * a >> 8)
            v = self.b[i]
            self.b[i] = v + ((c[2] - v) * a >> 8)

    def add(self, x: int, y: int, c: RGB, a: int) -> None:
        """Additively blend light onto a pixel; ``a`` runs 0..256."""
        if a > 0 and 0 <= x < self.w and 0 <= y < self.h:
            i = y * self.w + x
            v = self.r[i] + (c[0] * a >> 8)
            self.r[i] = 255 if v > 255 else v
            v = self.g[i] + (c[1] * a >> 8)
            self.g[i] = 255 if v > 255 else v
            v = self.b[i] + (c[2] * a >> 8)
            self.b[i] = 255 if v > 255 else v

    # -- shapes ------------------------------------------------------------

    def disc(self, cx: float, cy: float, radius: float, c: RGB, a: int = 256) -> None:
        """Anti-aliased filled circle."""
        y0 = max(0, int(cy - radius - 1))
        y1 = min(self.h - 1, int(cy + radius + 2))
        x0 = max(0, int(cx - radius - 1))
        x1 = min(self.w - 1, int(cx + radius + 2))
        for y in range(y0, y1 + 1):
            dy = y - cy
            for x in range(x0, x1 + 1):
                cover = radius + 0.5 - math.hypot(x - cx, dy)
                if cover > 0.0:
                    self.blend(x, y, c, a if cover >= 1.0 else int(a * cover))

    def glow(self, cx: float, cy: float, radius: float, c: RGB, strength: float) -> None:
        """Additive radial glow with a quadratic falloff."""
        if radius <= 0 or strength <= 0:
            return
        y0 = max(0, int(cy - radius))
        y1 = min(self.h - 1, int(cy + radius + 1))
        x0 = max(0, int(cx - radius))
        x1 = min(self.w - 1, int(cx + radius + 1))
        inv = 1.0 / radius
        peak = strength * 256.0
        for y in range(y0, y1 + 1):
            dy = y - cy
            for x in range(x0, x1 + 1):
                f = 1.0 - math.hypot(x - cx, dy) * inv
                if f > 0.0:
                    self.add(x, y, c, int(peak * f * f))

    def line(
        self, x0: float, y0: float, x1: float, y1: float, c: RGB, a: int = 256, width: float = 1.0
    ) -> None:
        """Anti-aliased line with round caps."""
        half = width / 2.0
        pad = half + 1.0
        bx0 = max(0, int(min(x0, x1) - pad))
        bx1 = min(self.w - 1, int(max(x0, x1) + pad + 1))
        by0 = max(0, int(min(y0, y1) - pad))
        by1 = min(self.h - 1, int(max(y0, y1) + pad + 1))
        dx = x1 - x0
        dy = y1 - y0
        length_sq = dx * dx + dy * dy
        for y in range(by0, by1 + 1):
            for x in range(bx0, bx1 + 1):
                if length_sq > 0.0:
                    t = ((x - x0) * dx + (y - y0) * dy) / length_sq
                    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
                else:
                    t = 0.0
                cover = half + 0.5 - math.hypot(x - (x0 + dx * t), y - (y0 + dy * t))
                if cover > 0.0:
                    self.blend(x, y, c, a if cover >= 1.0 else int(a * cover))

    # -- output ------------------------------------------------------------

    def row_key(self, row: int) -> bytes:
        """The raw pixels behind one text row, for change detection."""
        i = row * 2 * self.w
        j = i + 2 * self.w
        return bytes(self.r[i:j]) + bytes(self.g[i:j]) + bytes(self.b[i:j])

    def segments(self, row: int, text: RowText | None = None) -> list[Segment]:
        """Render one text row (two pixel rows) as Rich segments."""
        w = self.w
        top = row * 2 * w
        bottom = top + w
        r, g, b = self.r, self.g, self.b
        has_bottom = bottom + w <= len(r)
        out: list[Segment] = []
        run_key = None
        run_len = 0
        run_style: Style | None = None
        run_char = " "
        for x in range(w):
            i = top + x
            fg = (r[i] << 16) | (g[i] << 8) | b[i]
            if has_bottom:
                j = bottom + x
                bg = (r[j] << 16) | (g[j] << 8) | b[j]
            else:
                bg = fg
            if text is not None and x in text:
                if run_len:
                    out.append(Segment(run_char * run_len, run_style))
                    run_len = 0
                    run_key = None
                cell = text[x]
                if cell is not None:
                    ch, tfg, bold = cell
                    ur = ((fg >> 16) + (bg >> 16)) >> 1
                    ug = (((fg >> 8) & 255) + ((bg >> 8) & 255)) >> 1
                    ub = ((fg & 255) + (bg & 255)) >> 1
                    light = (ur * 3 + ug * 6 + ub) // 10
                    if light > 150:
                        # Words stay readable when a bright cloud drifts behind them.
                        ur, ug, ub = ur * 150 // light, ug * 150 // light, ub * 150 // light
                    under = (ur, ug, ub)
                    out.append(
                        Segment(ch, text_style((tfg >> 16, (tfg >> 8) & 255, tfg & 255), under, bold))
                    )
                continue
            key = (fg << 24) | bg
            if key == run_key:
                run_len += 1
                continue
            if run_len:
                out.append(Segment(run_char * run_len, run_style))
            run_key = key
            run_len = 1
            if fg == bg:
                run_char = " "
                run_style = _fill_style(bg)
            else:
                run_char = "▀"
                run_style = _pixel_style(fg, bg)
        if run_len:
            out.append(Segment(run_char * run_len, run_style))
        return out
