"""Render Cloudspin to a PNG without a terminal.

Runs the app headless with the silent demo deck and a canned forecast, then
draws every cell with Pillow, so the picture matches what a truecolor terminal
shows. Used for the README images and for checking the layout by eye.

    python scripts/screenshot.py docs/storm.png --song 2
    python scripts/screenshot.py out.png --size 100x30 --sky weather --colors cover
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.segment import Segment  # noqa: E402

from cloudspin import weather  # noqa: E402
from cloudspin.app import CloudspinApp  # noqa: E402
from cloudspin.config import Config  # noqa: E402
from cloudspin.player import DEMO_TRACKS, Deck, DemoBackend, Track  # noqa: E402

CELL_W, CELL_H = 10, 20
FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/TTF", "/Library/Fonts")
EIGHTHS = "▁▂▃▄▅▆▇█"


def _font(bold: bool) -> ImageFont.FreeTypeFont:
    name = "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"
    for folder in FONT_DIRS:
        path = Path(folder) / name
        if path.exists():
            return ImageFont.truetype(str(path), 16)
    raise SystemExit("screenshot.py needs the DejaVu Sans Mono font installed")


def capture(app: CloudspinApp) -> list[list[Segment]]:
    width, height = app.size
    console = Console(
        width=width, height=height, file=io.StringIO(), force_terminal=True,
        color_system="truecolor", record=True, legacy_windows=False, safe_box=False,
    )  # fmt: skip
    console.print(app.screen._compositor.render_update(full=True, screen_stack=app._background_screens, simplify=False))
    cells = [segment for segment in console._record_buffer if not segment.control]
    lines = list(Segment.split_and_crop_lines(cells, width, pad=False))
    return lines[:height]


def draw(lines: list[list[Segment]], width: int, default_bg=(0, 0, 0)) -> Image.Image:
    image = Image.new("RGB", (width * CELL_W, len(lines) * CELL_H), default_bg)
    pen = ImageDraw.Draw(image)
    fonts = {False: _font(False), True: _font(True)}
    for row, line in enumerate(lines):
        x = 0
        y = row * CELL_H
        for segment in line:
            style = segment.style
            fg = style.color.get_truecolor() if style and style.color else (230, 230, 230)
            bg = style.bgcolor.get_truecolor() if style and style.bgcolor else default_bg
            bold = bool(style and style.bold)
            for ch in segment.text:
                if ch in "\r\n":
                    continue
                px = x * CELL_W
                pen.rectangle((px, y, px + CELL_W - 1, y + CELL_H - 1), fill=tuple(bg))
                if ch == "▀":
                    pen.rectangle((px, y, px + CELL_W - 1, y + CELL_H // 2 - 1), fill=tuple(fg))
                elif ch == "▐":
                    pen.rectangle((px + CELL_W // 2, y, px + CELL_W - 1, y + CELL_H - 1), fill=tuple(fg))
                elif ch == "▌":
                    pen.rectangle((px, y, px + CELL_W // 2 - 1, y + CELL_H - 1), fill=tuple(fg))
                elif ch in EIGHTHS:
                    tall = (EIGHTHS.index(ch) + 1) * CELL_H // 8
                    pen.rectangle((px, y + CELL_H - tall, px + CELL_W - 1, y + CELL_H - 1), fill=tuple(fg))
                elif ch == "━":
                    pen.rectangle((px, y + CELL_H // 2 - 1, px + CELL_W - 1, y + CELL_H // 2), fill=tuple(fg))
                elif ch != " ":
                    pen.text((px, y), ch, font=fonts[bold], fill=tuple(fg))
                x += 1
    return image


async def shoot(args: argparse.Namespace) -> None:
    width, height = (int(v) for v in args.size.lower().split("x"))
    config = Config(path=None)
    config.sky = args.sky
    config.colors = args.colors
    config.units = args.units
    deck = Deck(DemoBackend(), [Track(**vars(t)) for t in DEMO_TRACKS], seed=4)
    app = CloudspinApp(config, deck, silent_reason="Silent demo")
    async with app.run_test(size=(width, height)) as pilot:
        if not args.no_city:
            data = json.loads((ROOT / "tests" / "fixtures" / "forecast.json").read_text())
            place = weather.Place("New Delhi", 28.61, 77.21, "IN")
            app.place = place
            app._weather_due = time.monotonic() + 1e9
            app._set_report(weather.parse(data, place, config.units))
        if args.song >= 0:
            deck.play(args.song)
            deck.backend.seek(args.at)
            if args.paused:
                deck.toggle()
        for key in args.press:
            await pilot.press(key)
        await pilot.pause(args.wait)
        lines = capture(app)
    image = draw(lines, width)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out)
    print(f"wrote {args.out} ({image.width}x{image.height})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out")
    parser.add_argument("--size", default="132x40", help="columns x rows")
    parser.add_argument("--sky", default="song")
    parser.add_argument("--colors", default="sky")
    parser.add_argument("--units", default="metric")
    parser.add_argument("--song", type=int, default=0, help="demo song to play, -1 for none")
    parser.add_argument("--at", type=float, default=71.0, help="seconds into the song")
    parser.add_argument("--wait", type=float, default=3.0, help="seconds to let the scene settle")
    parser.add_argument("--paused", action="store_true")
    parser.add_argument("--no-city", action="store_true")
    parser.add_argument("--press", nargs="*", default=[], help="keys to press before the shot")
    asyncio.run(shoot(parser.parse_args()))


if __name__ == "__main__":
    main()
