"""Everything on screen. Each widget paints its own lines so colour can follow
the sky or the song from one frame to the next."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.cells import cell_len, set_cell_size
from rich.segment import Segment
from textual import events
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget

from .canvas import Canvas, RowText, lay_text, text_style
from .color import RGB, lerp, ramp
from .player import Deck
from .sky import SkyScene
from .spectrum import Spectrum
from .themes import Palette
from .vinyl import Vinyl
from .weather import Report, compass, icon_for

if TYPE_CHECKING:
    from .app import CloudspinApp

INK: RGB = (245, 247, 251)  # text that sits on the sky
INK_SOFT: RGB = (208, 216, 230)
INK_RAIN: RGB = (150, 198, 255)


def seg(text: str, fg: RGB | None, bg: RGB | None, bold: bool = False, italic: bool = False) -> Segment:
    return Segment(text, text_style(fg, bg, bold, italic))


def fit(text: str, width: int) -> str:
    """Crop text to a cell width, ending with an ellipsis when it is cut."""
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    return set_cell_size(text, max(0, width - 1)).rstrip() + "…"


def clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


class Painted(Widget):
    """A widget that redraws its lines only when the state behind them changes."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._rows: list[Strip] = []
        self._seen: object = None

    @property
    def cloud(self) -> CloudspinApp:
        return self.app  # type: ignore[return-value]

    @property
    def pal(self) -> Palette:
        return self.cloud.palette

    def state(self) -> object:
        return None

    def paint(self, w: int, h: int) -> list[list[Segment]]:
        return []

    def sync(self, force: bool = False) -> None:
        w, h = self.size.width, self.size.height
        if w <= 0 or h <= 0:
            return
        key = (w, h, self.pal.key(), self.state())
        if force or key != self._seen:
            self._seen = key
            fill = text_style(None, self.pal.bg)
            lines = self.paint(w, h)
            self._rows = [Strip(line).adjust_cell_length(w, fill) for line in lines[:h]]
            self.refresh()

    def on_resize(self, event: events.Resize) -> None:
        self.sync(force=True)

    def render_line(self, y: int) -> Strip:
        if y < len(self._rows):
            return self._rows[y]
        return Strip.blank(self.size.width, text_style(None, self.pal.bg))


class Pixels(Widget):
    """A widget backed by a half-block pixel canvas, repainting only changed rows."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cv = Canvas()
        self._rows: list[Strip] = []
        self._keys: list[object] = []

    @property
    def cloud(self) -> CloudspinApp:
        return self.app  # type: ignore[return-value]

    def fit_canvas(self) -> bool:
        """Match the canvas to the widget. True when there is something to draw on."""
        w, h = self.size.width, self.size.height
        if w <= 0 or h <= 0:
            return False
        if self.cv.w != w or self.cv.h != h * 2:
            self.cv.resize(w, h * 2)
            self._rows = [Strip.blank(w)] * h
            self._keys = [None] * h
        return True

    def present(self, texts: dict[int, RowText] | None = None) -> None:
        cv = self.cv
        w = cv.w
        rows = cv.h // 2
        first = last = -1
        for y in range(rows):
            text = texts.get(y) if texts else None
            key: object = cv.row_key(y)
            if text:
                key = (key, tuple(sorted(text.items())))
            if key != self._keys[y]:
                self._keys[y] = key
                self._rows[y] = Strip(cv.segments(y, text), w)
                if first < 0:
                    first = y
                last = y
        if first >= 0:
            self.refresh(Region(0, first, w, last - first + 1))

    def render_line(self, y: int) -> Strip:
        if y < len(self._rows):
            return self._rows[y]
        return Strip.blank(self.size.width)


# -- the sky -----------------------------------------------------------------

_DIGITS = {
    "0": "111101101101111",
    "1": "010110010010111",
    "2": "111001111100111",
    "3": "111001111001111",
    "4": "101101111001001",
    "5": "111100111001111",
    "6": "111100111101111",
    "7": "111001001001001",
    "8": "111101111101111",
    "9": "111101111001111",
    "-": "000000111000000",
}

_ICON_TONES: dict[str, RGB] = {
    "Y": (255, 214, 96),
    "O": (255, 170, 72),
    "W": (240, 244, 250),
    "G": (168, 178, 196),
    "D": (118, 126, 146),
    "B": (120, 180, 255),
    "S": (232, 240, 255),
    "L": (255, 232, 120),
    "F": (186, 194, 208),
}

_ICONS: dict[str, tuple[str, ...]] = {
    "clear": ("O......O", "..YYYY..", ".YYYYYY.", ".YYYYYY.", "..YYYY..", "O......O"),
    "partly": ("....YYY.", "..WWYYYY", ".WWWWYY.", "WWWWWWW.", "GGGGGGG.", "........"),
    "cloud": ("........", "..WWW...", ".WWWWWW.", "WWWWWWWW", "GGGGGGGG", "........"),
    "rain": ("..WWW...", ".WWWWWW.", "GGGGGGGG", "........", ".B..B.B.", "B..B.B.."),
    "snow": ("..WWW...", ".WWWWWW.", "GGGGGGGG", "........", ".S..S..S", "..S..S.."),
    "storm": ("..GGG...", ".GGGGGG.", "DDDDDDDD", "...LL...", "..LL....", "...L...."),
    "fog": ("........", "FFFFFF..", "........", "..FFFFFF", "........", "FFFFF..."),
}


class SkyView(Pixels):
    """The sky, with the weather written on it."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.scene = SkyScene()
        self.report: Report | None = None
        self.note = ""  # shown in place of the weather when there is none
        self.hint = ""
        self.caption = ""  # what the sky is doing and why, top right
        self._tier = ""

    def set_report(self, report: Report | None) -> None:
        self.report = report
        self._arrange()

    def on_resize(self, event: events.Resize) -> None:
        self._arrange()
        self.step(0.0)

    def _arrange(self) -> None:
        if not self.fit_canvas():
            return
        w, rows = self.size.width, self.size.height
        has_days = self.report is not None and len(self.report.days) > 1
        if rows >= 17 and w >= 60 and has_days:
            self._tier = "full"
        elif rows >= 11:
            self._tier = "big"
        else:
            self._tier = "small"
        self.scene.set_foreground(8 if self._tier == "full" else 0)
        self.scene.scrim = min(w, 68) if (self.report is not None or self.note) else 0
        self.scene.resize(w, rows * 2)

    def step(self, dt: float) -> None:
        if not self.fit_canvas():
            return
        if self.scene.w != self.cv.w or self.scene.h != self.cv.h:
            self._arrange()
        self.scene.step(dt)
        self.scene.render(self.cv)
        self.present(self._write())

    # -- the words -----------------------------------------------------------

    def _write(self) -> dict[int, RowText]:
        texts: dict[int, RowText] = {}
        w, rows = self.size.width, self.size.height
        x0 = 3 if w >= 50 else 1
        room = w - x0 - 1
        report = self.report
        used = 0
        if report is None:
            if self.note and rows >= 2:
                used = lay_text(texts, x0, 1, fit(self.note, room), INK, True)
                if self.hint and rows >= 4:
                    lay_text(texts, x0, 2, fit(self.hint, room), INK_SOFT)
        elif self._tier == "small":
            used = self._write_small(texts, report, x0, room, rows)
        else:
            used = self._write_big(texts, report, x0, room)
            if self._tier == "full":
                self._write_days(texts, report, x0, w, rows)
        if self.caption and rows >= 2:
            text = fit(self.caption, w - x0 - used - 6)
            if cell_len(text) >= 8:
                lay_text(texts, w - cell_len(text) - 2, 1, text, INK_SOFT)
        return texts

    def _stats(self, report: Report, room: int) -> str:
        parts = [
            f"Wind {round(report.wind_speed)} {report.wind_unit} {compass(report.wind_direction)}",
            f"Humidity {report.humidity}%",
            f"Sunset {report.sunset:%H:%M}" if report.sunrise <= report.local_now() < report.sunset
            else f"Sunrise {report.next_sunrise:%H:%M}",
        ]  # fmt: skip
        while parts and cell_len("   ".join(parts)) > room:
            parts.pop()
        return "   ".join(parts)

    def _write_small(self, texts: dict[int, RowText], report: Report, x0: int, room: int, rows: int) -> int:
        name = fit(report.place.label, room)
        used = lay_text(texts, x0, 1 if rows >= 3 else 0, name, INK, True)
        if rows >= 3:
            line = f"{round(report.temperature)}°  {report.description}"
            lay_text(texts, x0, 2, fit(line, room), INK)
        if rows >= 5:
            lay_text(texts, x0, 3, self._stats(report, room), INK_SOFT)
        return used

    def _write_big(self, texts: dict[int, RowText], report: Report, x0: int, room: int) -> int:
        name = fit(report.place.label, min(room, 30))
        used = lay_text(texts, x0, 1, name, INK, True)
        now = report.local_now()
        when = f"{now:%a} {now.day} {now:%b}  {now:%H:%M}"  # %-d is not portable to Windows
        if used + 3 + len(when) <= min(room, 52):
            used += 3 + lay_text(texts, x0 + used + 3, 1, when, INK_SOFT)
        number = str(round(report.temperature))
        wide = self._digits(number, x0, 6)
        tx = x0 + wide + 3
        text_room = room - wide - 3
        lay_text(texts, tx, 4, fit(report.description, text_room), INK, True)
        lay_text(texts, tx, 5, fit(f"Feels like {round(report.feels_like)}°", text_room), INK_SOFT)
        lay_text(texts, tx, 6, fit(f"High {round(report.high)}°  Low {round(report.low)}°", text_room), INK_SOFT)
        lay_text(texts, x0, 9, self._stats(report, room), INK_SOFT)
        return used

    def _digits(self, number: str, x: int, y: int) -> int:
        """Draw big block numerals onto the sky; returns the width used."""
        cv = self.cv
        cursor = x
        for ch in number:
            bits = _DIGITS.get(ch)
            if bits is None:
                continue
            for row in range(5):
                for col in range(3):
                    if bits[row * 3 + col] == "1":
                        for oy in range(2):
                            for ox in range(2):
                                cv.blend(cursor + col * 2 + ox + 1, y + row * 2 + oy + 1, (0, 0, 0), 96)
            for row in range(5):
                tone = lerp((255, 255, 255), (222, 230, 244), row / 4.0)
                for col in range(3):
                    if bits[row * 3 + col] == "1":
                        for oy in range(2):
                            for ox in range(2):
                                cv.put(cursor + col * 2 + ox, y + row * 2 + oy, tone)
            cursor += 8
        for dx, dy in ((0, 0), (1, 0), (2, 0), (0, 1), (2, 1), (0, 2), (1, 2), (2, 2)):
            cv.put(cursor + dx, y + dy, (255, 255, 255))
        return cursor + 3 - x

    def _write_days(self, texts: dict[int, RowText], report: Report, x0: int, w: int, rows: int) -> None:
        days = report.days[1:]
        pitch = 19
        count = min(len(days), 6, max(1, (w - x0 * 2 + 5) // pitch))
        top = rows - 4
        cv = self.cv
        for n, day in enumerate(days[:count]):
            x = x0 + n * pitch
            art = _ICONS.get(icon_for(day.code), _ICONS["cloud"])
            for iy, line in enumerate(art):
                for ix, mark in enumerate(line):
                    tone = _ICON_TONES.get(mark)
                    if tone is not None:
                        cv.put(x + ix, top * 2 + iy, tone)
            tx = x + 10
            lay_text(texts, tx, top, f"{day.day:%a}", INK, True)
            high = f"{round(day.high)}°"
            lay_text(texts, tx, top + 1, high, INK)
            lay_text(texts, tx + len(high) + 1, top + 1, f"{round(day.low)}°", INK_SOFT)
            if day.rain_chance is not None and day.rain_chance >= 15:
                lay_text(texts, tx, top + 2, f"{day.rain_chance}%", INK_RAIN)


# -- the record --------------------------------------------------------------


class DiscView(Pixels):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.vinyl = Vinyl()
        self._last: object = None

    def on_resize(self, event: events.Resize) -> None:
        self._last = None
        self.step(0.0)

    def step(self, dt: float) -> None:
        if not self.fit_canvas():
            return
        app = self.cloud
        deck = app.deck
        vinyl = self.vinyl
        vinyl.resize(self.cv.w, self.cv.h)
        vinyl.step(dt, deck.playing, deck.current is not None, deck.progress)
        pal = app.palette
        art = app.look.art if app.look is not None else None
        pulse = app.spectrum.energy
        key = (round(vinyl.angle, 3), round(vinyl.reach, 3), pal.key(), int(pulse * 48), id(art))
        if key == self._last:
            return
        self._last = key
        vinyl.render(self.cv, pal.bg, pal.disc, pal.disc2, pal.accent, pulse, art)
        self.present()


# -- now playing -------------------------------------------------------------


class NowPlaying(Painted):
    def state(self) -> object:
        deck = self.cloud.deck
        track = deck.current
        if track is None:
            return (None, len(deck.tracks), self.cloud.silent_reason)
        return (track.path, track.title, track.artist, track.album, self.cloud.silent_reason)

    def paint(self, w: int, h: int) -> list[list[Segment]]:
        pal = self.pal
        deck = self.cloud.deck
        track = deck.current
        badge = self.cloud.silent_reason
        lines: list[list[Segment]] = []
        if track is None:
            title = "Nothing on the platter"
            second = "Press space to start playing" if deck.tracks else "Press o to play a link, a file or a folder"
            third = ""
        else:
            title = track.title
            second = track.artist or ("Internet stream" if track.is_stream else "Unknown artist")
            third = track.album
        room = w - (cell_len(badge) + 3 if badge and w > 50 else 0)
        first = [seg(fit(title, room), pal.text, pal.bg, bold=True)]
        if badge and w > 50:
            gap = w - cell_len(fit(title, room)) - cell_len(badge)
            first += [seg(" " * max(1, gap), None, pal.bg), seg(badge, pal.faint, pal.bg)]
        lines.append(first)
        if h == 2 and third and track is not None and track.artist:
            # Two rows only: the album shares the artist's line.
            name = fit(second, w)
            rest = w - cell_len(name) - 3
            lines.append([seg(name, pal.soft, pal.bg)] + ([seg("   " + fit(third, rest), pal.faint, pal.bg)] if rest > 6 else []))
            return lines
        lines.append([seg(fit(second, w), pal.soft, pal.bg)])
        lines.append([seg(fit(third, w), pal.faint, pal.bg)])
        return lines


class SpectrumView(Widget):
    """Bars that move with the music."""

    _BLOCKS = " ▁▂▃▄▅▆▇█"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._rows: list[Strip] = []
        self._seen: object = None

    @property
    def cloud(self) -> CloudspinApp:
        return self.app  # type: ignore[return-value]

    def on_resize(self, event: events.Resize) -> None:
        self._seen = None
        self.sync()

    def sync(self) -> None:
        w, h = self.size.width, self.size.height
        if w <= 0 or h <= 0:
            return
        app = self.cloud
        spectrum = app.spectrum
        bars = min(64, (w + 1) // 3)
        spectrum.resize(bars)
        pal = app.palette
        eighths = h * 8
        key = (w, h, pal.key(), tuple(int(v * eighths) for v in spectrum.levels), tuple(int(v * eighths) for v in spectrum.peaks))
        if key == self._seen:
            return
        self._seen = key
        bg = pal.bg
        pad = (w - (bars * 3 - 1)) // 2
        blocks = self._BLOCKS
        rows: list[Strip] = []
        heights = [v * eighths for v in spectrum.levels]
        caps = [int(v * eighths) // 8 for v in spectrum.peaks]
        for r in range(h):
            b = h - 1 - r
            tone = ramp(((0.0, pal.accent), (1.0, pal.accent2)), (b + 0.5) / h)
            bar_style = text_style(tone, bg)
            cap_style = text_style(pal.soft if b else pal.faint, bg)
            fill = text_style(None, bg)
            line: list[Segment] = [Segment(" " * pad, fill)] if pad > 0 else []
            for i in range(bars):
                level = int(heights[i]) - b * 8
                if level >= 8:
                    line.append(Segment("██", bar_style))
                elif level > 0:
                    line.append(Segment(blocks[level] * 2, bar_style))
                elif caps[i] == b:
                    line.append(Segment("▁▁", cap_style))
                else:
                    line.append(Segment("  ", fill))
                if i < bars - 1:
                    line.append(Segment(" ", fill))
            rows.append(Strip(line).adjust_cell_length(w, fill))
        self._rows = rows
        self.refresh()

    def render_line(self, y: int) -> Strip:
        if y < len(self._rows):
            return self._rows[y]
        return Strip.blank(self.size.width, text_style(None, self.cloud.palette.bg))


class Seekbar(Painted):
    """Where the song is. Click or drag to move through it."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._drag = False
        self._bar = (0, 1)

    def state(self) -> object:
        deck = self.cloud.deck
        if deck.current is None:
            return None
        width = max(1, self._bar[1])
        return (int(deck.position), int(deck.duration), int(deck.progress * width * 2))

    def paint(self, w: int, h: int) -> list[list[Segment]]:
        pal = self.pal
        deck = self.cloud.deck
        loaded = deck.current is not None
        left = clock(deck.position) if loaded else "0:00"
        right = clock(deck.duration) if loaded and deck.duration > 0 else "0:00" if not loaded else "live"
        start = len(left) + 2
        width = max(1, w - start - len(right) - 2)
        self._bar = (start, width)
        line = [seg(left + "  ", pal.soft, pal.bg)]
        spot = deck.progress * width if loaded and deck.duration > 0 else 0.0
        filled = min(width - 1, int(spot))
        for i in range(width):
            if i < filled:
                tone = ramp(((0.0, pal.accent), (1.0, pal.accent2)), i / max(1, width - 1))
                line.append(seg("━", tone, pal.bg))
            elif i == filled and loaded:
                line.append(seg("●", pal.text, pal.bg))
            else:
                line.append(seg("━", pal.lifted, pal.bg))
        line.append(seg("  " + right, pal.soft, pal.bg))
        return [line]

    def _seek(self, x: int) -> None:
        start, width = self._bar
        self.cloud.deck.seek_to((x - start) / max(1, width - 1))
        self.sync(force=True)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._drag = True
        self.capture_mouse()
        self._seek(event.x)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._drag:
            self._seek(event.x)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._drag:
            self._drag = False
            self.release_mouse()


class Controls(Painted):
    """Shuffle, previous, play, next, repeat, and the volume."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._hover = ""
        self._zones: list[tuple[int, int, str]] = []
        self._volume = (0, 1)

    def state(self) -> object:
        deck = self.cloud.deck
        return (deck.playing, deck.shuffle, deck.repeat, deck.volume, self._hover)

    def paint(self, w: int, h: int) -> list[list[Segment]]:
        pal = self.pal
        deck = self.cloud.deck
        bg = pal.bg
        pills = [
            ("shuffle", " ⇄ ", deck.shuffle, False),
            ("previous", " ◀◀ ", False, False),
            ("toggle", "  ▐ ▌  " if deck.playing else "   ▶   ", False, True),
            ("next", " ▶▶ ", False, False),
            ("repeat", " ↻1 " if deck.repeat == "one" else " ↻ ", deck.repeat != "off", False),
        ]
        pills_width = sum(len(text) + 2 for _, text, _, _ in pills) + len(pills) - 1
        # The volume line takes whatever room the buttons leave, and steps aside when there is none.
        volume_cells = max(0, min(10, w - pills_width - 12))
        if volume_cells < 4:
            volume_cells = 0  # no room for the line: just the number
        show_volume = w >= pills_width + 10
        volume_width = volume_cells + 8 if show_volume else 0
        x = max(0, (w - pills_width) // 2)
        if show_volume and x + pills_width > w - volume_width - 2:
            x = max(0, w - volume_width - 2 - pills_width)
        line: list[Segment] = [seg(" " * x, None, bg)]
        zones = []
        for name, text, lit, primary in pills:
            hovered = self._hover == name
            if primary:
                face = lerp(pal.accent, (255, 255, 255), 0.22 if hovered else 0.0)
                ink = lerp(pal.bg, (0, 0, 0), 0.35)
            else:
                face = pal.lifted if hovered else pal.raised
                ink = pal.accent if lit else pal.text if hovered else pal.soft
            line.append(seg("▐", face, bg))
            line.append(seg(text, ink, face, bold=primary or lit))
            line.append(seg("▌", face, bg))
            zones.append((x, x + len(text) + 2, name))
            x += len(text) + 2
            line.append(seg(" ", None, bg))
            x += 1
        self._zones = zones
        if show_volume:
            gap = w - volume_width - x
            line.append(seg(" " * max(0, gap), None, bg))
            filled = round(deck.volume / 100 * volume_cells)
            start = w - volume_width
            line.append(seg("Vol ", pal.faint, bg))
            line.append(seg("━" * filled, pal.accent if self._hover != "volume" else pal.text, bg))
            line.append(seg("━" * (volume_cells - filled), pal.lifted, bg))
            line.append(seg(f"{deck.volume:>4}", pal.soft, bg))
            if volume_cells:
                self._volume = (start + 4, volume_cells)
                self._zones.append((start + 4, start + 4 + volume_cells, "volume"))
        return [line]

    def _zone(self, x: int) -> str:
        for start, end, name in self._zones:
            if start <= x < end:
                return name
        return ""

    def on_mouse_move(self, event: events.MouseMove) -> None:
        zone = self._zone(event.x)
        if zone != self._hover:
            self._hover = zone
            self.sync()

    def on_leave(self, event: events.Leave) -> None:
        if self._hover:
            self._hover = ""
            self.sync()

    def on_click(self, event: events.Click) -> None:
        zone = self._zone(event.x)
        app = self.cloud
        if zone == "volume":
            start, cells = self._volume
            app.set_volume(round((event.x - start + 1) / cells * 100))
        elif zone == "toggle":
            app.action_toggle_play()
        elif zone == "next":
            app.action_next_track()
        elif zone == "previous":
            app.action_previous_track()
        elif zone == "shuffle":
            app.action_shuffle()
        elif zone == "repeat":
            app.action_repeat()
        self.sync()


class QueueView(Painted):
    """What plays after this."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._hover = -1
        self._shown: list[int] = []

    def state(self) -> object:
        deck = self.cloud.deck
        rows = max(0, self.size.height - 2)
        coming = deck.upcoming(rows)
        return (
            deck.index,
            deck.shuffle,
            deck.repeat,
            len(deck.tracks),
            self._hover,
            tuple((i, deck.tracks[i].title, deck.tracks[i].artist, int(deck.tracks[i].duration)) for i in coming),
        )

    def paint(self, w: int, h: int) -> list[list[Segment]]:
        pal = self.pal
        deck = self.cloud.deck
        bg = pal.bg
        total = len(deck.tracks)
        count = f"{total} song{'' if total == 1 else 's'}" if total else ""
        head = [seg("Up next", pal.text, bg, bold=True)]
        if count and w > 22:
            head += [seg(" " * (w - 7 - len(count)), None, bg), seg(count, pal.faint, bg)]
        lines = [head, [seg("", None, bg)]]
        coming = deck.upcoming(max(0, h - 2))
        self._shown = coming
        if not coming:
            message = "The queue ends here" if total else "No music loaded"
            lines.append([seg(fit(message, w), pal.faint, bg)])
            return lines
        for row, index in enumerate(coming):
            track = deck.tracks[index]
            hovered = row == self._hover
            face = pal.raised if hovered else bg
            time = clock(track.duration) if track.duration else ""
            room = w - (len(time) + 2 if time else 0)
            title = fit(track.title, room)
            line = [seg(title, pal.text if hovered else pal.soft, face)]
            left = room - cell_len(title)
            if track.artist and left > 8:
                artist = fit(track.artist, left - 2)
                line.append(seg("  " + artist, pal.faint, face))
                left -= cell_len(artist) + 2
            line.append(seg(" " * max(0, left), None, face))
            if time:
                line.append(seg("  " + time, pal.faint, face))
            lines.append(line)
        return lines

    def on_mouse_move(self, event: events.MouseMove) -> None:
        row = event.y - 2
        row = row if 0 <= row < len(self._shown) else -1
        if row != self._hover:
            self._hover = row
            self.sync()

    def on_leave(self, event: events.Leave) -> None:
        if self._hover != -1:
            self._hover = -1
            self.sync()

    def on_click(self, event: events.Click) -> None:
        row = event.y - 2
        if 0 <= row < len(self._shown):
            self.cloud.deck.play(self._shown[row])


class KeyHints(Painted):
    """The keys worth knowing, or a short message when something just happened."""

    HINTS = (
        # Most useful first: narrow terminals only have room for the start of this list.
        ("space", "play"),
        ("n p", "skip"),
        ("o", "link"),
        ("?", "help"),
        ("q", "quit"),
        ("w", "sky"),
        ("t", "colours"),
        ("c", "city"),
        ("← →", "seek"),
        ("+ −", "volume"),
        ("s", "shuffle"),
        ("r", "repeat"),
    )

    def state(self) -> object:
        return self.cloud.message

    def paint(self, w: int, h: int) -> list[list[Segment]]:
        pal = self.pal
        bg = pal.bg
        message = self.cloud.message
        if message:
            return [[seg("  " + fit(message, w - 4), pal.text, bg)]]
        line: list[Segment] = [seg("  ", None, bg)]
        used = 2
        for keys, what in self.HINTS:
            need = len(keys) + 1 + len(what) + 3
            if used + need > w:
                break
            line += [seg(keys, pal.soft, bg, bold=True), seg(" " + what + "   ", pal.faint, bg)]
            used += need
        return [line]
