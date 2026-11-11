"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloudspin",
        description="A terminal music player under a living sky.",
        epilog="Settings live in ~/.config/cloudspin/config.toml. Press ? inside the app for the keys.",
    )
    parser.add_argument("music", nargs="*", help="files, folders or stream URLs to play (default: your music folder)")
    parser.add_argument("--city", help="city for the weather, for example --city \"New Delhi\"")
    parser.add_argument("--sky", metavar="MODE", help="song, weather, or a scene: storm, rain, evening, night, day, dawn, snow, fog, overcast")
    parser.add_argument("--colors", metavar="THEME", help="sky, cover, amber, orchid, lagoon, moss, rose, mono, or one of your own")
    parser.add_argument("--units", choices=("metric", "imperial"), help="°C and km/h, or °F and mph")
    parser.add_argument("--fps", type=int, metavar="N", help="animation frames per second (4-60, default 24)")
    parser.add_argument("--demo", action="store_true", help="run a silent demo with made-up songs")
    parser.add_argument("--version", action="version", version=f"cloudspin {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Imported late so --help and --version stay instant.
    from . import library, mood, weather
    from .app import CloudspinApp
    from .config import Config
    from .player import DEMO_TRACKS, BackendError, Deck, DemoBackend, MpvBackend
    from .themes import load_themes

    config = Config.load()
    if args.units:
        config.units = args.units
    if args.fps:
        config.fps = max(4, min(60, args.fps))
    if args.sky:
        if args.sky not in ("song", "weather") and args.sky not in mood.SCENES:
            print(f"cloudspin: unknown sky “{args.sky}”. Try: song, weather, {', '.join(mood.SCENES)}", file=sys.stderr)
            return 2
        config.sky = args.sky
    if args.colors:
        themes = load_themes(config.themes)
        if args.colors not in themes:
            print(f"cloudspin: unknown theme “{args.colors}”. Try: {', '.join(themes)}", file=sys.stderr)
            return 2
        config.colors = args.colors
    if args.city:
        try:
            place = weather.geocode(args.city)
        except weather.WeatherError as error:
            print(f"cloudspin: {error}", file=sys.stderr)
            return 1
        config.city, config.country = place.name, place.country
        config.latitude, config.longitude = place.latitude, place.longitude
        config.save()

    notice = ""
    silent_reason = ""
    if args.demo:
        tracks = [type(t)(**vars(t)) for t in DEMO_TRACKS]
        backend = DemoBackend()
        silent_reason = "Silent demo"
    else:
        sources = args.music or [config.music]
        tracks = library.scan(sources)
        if not tracks:
            where = ", ".join(str(Path(s).expanduser()) if "://" not in s else s for s in sources)
            tracks = [type(t)(**vars(t)) for t in DEMO_TRACKS]
            backend = DemoBackend()
            silent_reason = "Silent demo"
            notice = f"No music found in {where}. Showing a silent demo; start with: cloudspin <folder>"
        else:
            try:
                backend = MpvBackend(volume=config.volume)
            except BackendError as error:
                backend = DemoBackend()
                silent_reason = "No sound"
                notice = str(error)

    deck = Deck(backend, tracks, volume=config.volume)
    app = CloudspinApp(config, deck, silent_reason=silent_reason, notice=notice)
    try:
        app.run()
    finally:
        deck.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
