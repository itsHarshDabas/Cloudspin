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
    parser.add_argument("music", nargs="*", help="files, folders, links or playlist links to play (default: your music folder)")
    parser.add_argument("--city", help="city for the weather, for example --city \"New Delhi\"")
    parser.add_argument("--sky", metavar="MODE", help="song, weather, or a scene: storm, rain, evening, night, day, dawn, snow, fog, overcast")
    parser.add_argument("--colors", metavar="THEME", help="sky, cover, amber, orchid, lagoon, moss, rose, mono, or one of your own")
    parser.add_argument("--units", choices=("metric", "imperial"), help="°C and km/h, or °F and mph")
    parser.add_argument("--fps", type=int, metavar="N", help="animation frames per second (4-60, default 24)")
    parser.add_argument("--listen", metavar="SONG", help="measure one file or link, print what was heard and which sky it gets, then exit")
    parser.add_argument("--demo", action="store_true", help="run a silent demo with made-up songs")
    parser.add_argument("--version", action="version", version=f"cloudspin {__version__}")
    return parser


def _fetch_mpv(mpvget) -> str | None:
    """First run on Windows: download mpv, showing progress on the plain terminal."""
    print("Cloudspin plays sound through mpv. Downloading it once (about 30 MB)...")

    def progress(done: int, total: int) -> None:
        if total:
            print(f"\r  {done * 100 // total:3d}%", end="", flush=True)

    try:
        binary = mpvget.download_mpv(progress)
    except mpvget.MpvInstallError as error:
        print(f"\n{error}\nStarting without sound. You can install mpv yourself from https://mpv.io")
        return None
    print("\r  done.")
    return binary


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Imported late so --help and --version stay instant.
    from . import library, links, mood, mpvget, weather
    from .app import CloudspinApp
    from .config import Config
    from .player import DEMO_TRACKS, BackendError, Deck, DemoBackend, MpvBackend
    from .themes import load_themes

    config = Config.load()
    if args.listen:
        binary = mpvget.find_mpv()
        features = mood.listen(args.listen, binary, mpvget.find_ytdlp())
        if features is None:
            print("Couldn't listen to that. It needs mpv (or ffmpeg for files) and at least 8 seconds of sound.")
            return 1
        chosen = mood.from_features(features)
        print(f"loudness   {features.loudness:6.1f} dBFS")
        print(f"tempo      {features.tempo:6.0f} bpm (rough)")
        print(f"attacks    {features.onsets:6.2f} per second")
        print(f"bass       {features.bass:6.2f} share of energy")
        print(f"brightness {features.brightness:6.3f}")
        print(f"intensity  {features.intensity:6.2f}")
        print(f"sky        {mood.SCENES[chosen.scene].label} ({chosen.why})")
        return 0
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
        tracks = []
        for source in sources:
            if links.is_collection(source):
                print("Reading the playlist...")
                try:
                    tracks += links.expand(source)
                except links.LinkError as error:
                    print(f"  {error}")
            else:
                tracks += library.scan([source])
        try:
            binary = mpvget.find_mpv()
            if binary is None and mpvget.WINDOWS:
                binary = _fetch_mpv(mpvget)
            if binary is None:
                raise BackendError(f"mpv isn't installed, so there's no sound. Get it with: {mpvget.install_hint()}")
            backend = MpvBackend(volume=config.volume, binary=binary, ytdlp=mpvget.find_ytdlp())
        except BackendError as error:
            backend = DemoBackend()
            silent_reason = "No sound"
            notice = str(error)
        if not tracks:
            where = ", ".join(str(Path(s).expanduser()) if "://" not in s else s for s in sources)
            if backend.silent:
                tracks = [type(t)(**vars(t)) for t in DEMO_TRACKS]
                silent_reason = "Silent demo"
                notice = notice or f"No music found in {where}. Showing a silent demo; start with: cloudspin <folder>"
            else:
                notice = f"No music found in {where}. Press o to play a link, a file or a folder"

    deck = Deck(backend, tracks, volume=config.volume)
    app = CloudspinApp(config, deck, silent_reason=silent_reason, notice=notice)
    try:
        app.run()
    finally:
        deck.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
