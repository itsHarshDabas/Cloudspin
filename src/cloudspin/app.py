"""The app: one clock that moves the sky, the record and the music together."""

from __future__ import annotations

import json
import math
import queue
import threading
import time

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical

from . import library, links, mood, weather
from .config import Config, cache_dir
from pathlib import Path

from .player import Deck, Track
from .screens import Help, city_prompt, link_prompt
from .sky import Daylight, Weather
from .songlook import SongLook, look_for
from .spectrum import Spectrum
from .themes import Palette, load_themes
from .vinyl import Vinyl
from .widgets import Controls, DiscView, KeyHints, NowPlaying, QueueView, Seekbar, SkyView, SpectrumView

WEATHER_EVERY = 600.0  # seconds between forecast refreshes


class CloudspinApp(App[None]):
    TITLE = "Cloudspin"
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen { layout: vertical; overflow: hidden; }
    #sky { height: 1fr; min-height: 3; }
    #deck { height: 14; }
    #disc { width: 40; height: 100%; }
    #center { width: 1fr; height: 100%; padding: 1 3 0 2; }
    NowPlaying { height: 3; }
    SpectrumView { height: 1fr; min-height: 1; margin-top: 1; }
    Seekbar { height: 1; margin-top: 1; }
    Controls { height: 1; margin-top: 1; margin-bottom: 1; }
    #queue { width: 38; height: 100%; padding: 1 3 0 1; }
    #deck.short NowPlaying { height: 2; }
    #deck.short Controls { margin-bottom: 0; }
    #deck.tiny #center { padding-top: 0; }
    #deck.tiny SpectrumView { margin-top: 0; }
    #deck.tiny Seekbar { margin-top: 0; }
    #deck.tiny #queue { padding-top: 0; }
    KeyHints { height: 1; }
    """

    BINDINGS = [
        Binding("space", "toggle_play", "Play or pause"),
        Binding("n", "next_track", "Next"),
        Binding("p", "previous_track", "Previous"),
        Binding("right", "seek(5)", "Ahead"),
        Binding("left", "seek(-5)", "Back"),
        Binding("shift+right", "seek(30)", "Ahead 30s"),
        Binding("shift+left", "seek(-30)", "Back 30s"),
        Binding("plus,equals_sign", "volume(5)", "Louder"),
        Binding("minus,underscore", "volume(-5)", "Quieter"),
        Binding("s", "shuffle", "Shuffle"),
        Binding("r", "repeat", "Repeat"),
        Binding("w", "cycle_sky", "Sky"),
        Binding("t", "cycle_colors", "Colours"),
        Binding("o", "link", "Open a link"),
        Binding("c", "city", "City"),
        Binding("u", "units", "Units"),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, config: Config, deck: Deck, silent_reason: str = "", notice: str = "") -> None:
        super().__init__()
        self.config = config
        self.deck = deck
        self.silent_reason = silent_reason
        self.themes = load_themes(config.themes)
        if config.colors not in self.themes:
            config.colors = "sky"
        if config.sky not in ("song", "weather") and config.sky not in mood.SCENES:
            config.sky = "song"
        self.palette = Palette()
        self.spectrum = Spectrum()
        self.look: SongLook | None = None
        self.mood: mood.Mood | None = None
        self.report: weather.Report | None = None
        self.place: weather.Place | None = None
        if config.city and config.latitude is not None and config.longitude is not None:
            self.place = weather.Place(config.city, config.latitude, config.longitude, config.country)
        self.message = ""
        self._message_until = 0.0
        self._startup_notice = notice
        self._inbox: queue.SimpleQueue[tuple] = queue.SimpleQueue()
        self._looks: dict[str, SongLook] = {}
        self._moods: dict[str, mood.Mood | None] = {}
        self._tempos: dict[str, float] = {}
        self._studying: set[str] = set()
        self._seen_change = deck.changes
        self._last = time.monotonic()
        self._frame = 0
        self._sky_dt = 0.0
        self._palette_ready = False
        self._screen_bg: tuple | None = None
        self._weather_due = 0.0
        self._weather_busy = False
        self._closing = False
        self._heard: set[str] = set()  # songs whose sky came from listening, not from words
        self._features: dict[str, list[float]] = {}
        self._features_lock = threading.Lock()
        try:
            saved = json.loads((cache_dir() / "listened.json").read_text(encoding="utf-8"))
            self._features = {str(k): [float(x) for x in v] for k, v in saved.items() if isinstance(v, list) and len(v) == 5}
        except (OSError, ValueError, TypeError):
            pass
        self._is_up = False

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield SkyView(id="sky")
        with Horizontal(id="deck"):
            yield DiscView(id="disc")
            with Vertical(id="center"):
                yield NowPlaying()
                yield SpectrumView()
                yield Seekbar()
                yield Controls()
            yield QueueView(id="queue")
        yield KeyHints()

    def on_mount(self) -> None:
        self._sky = self.query_one(SkyView)
        self._disc = self.query_one(DiscView)
        self._bars = self.query_one(SpectrumView)
        self._painted = [
            self.query_one(NowPlaying),
            self.query_one(Seekbar),
            self.query_one(Controls),
            self.query_one(QueueView),
            self.query_one(KeyHints),
        ]
        self._is_up = True
        self._arrange()
        if self.place is not None:
            cached = weather.load_cache(cache_dir() / "weather.json", self.place, self.config.units)
            if cached is not None:
                self._set_report(cached)
        else:
            self._sky.note = "Press c to choose your city"
            self._sky.hint = "The forecast, sunrise and sunset will follow it."
        if self._startup_notice:
            self.flash(self._startup_notice, 7.0)
        threading.Thread(target=self._read_all_tags, name="cloudspin-tags", daemon=True).start()
        self.set_interval(1.0 / max(4, self.config.fps), self._tick)
        self._tick()

    def on_resize(self) -> None:
        if self._is_up:
            self._arrange()

    def _arrange(self) -> None:
        width, height = self.size.width, self.size.height
        deck_rows = max(8, min(20, round(height * 0.42)))
        if height < 22:
            deck_rows = max(6, height // 2)
        deck = self.query_one("#deck")
        deck.styles.height = deck_rows
        deck.set_class(deck_rows < 14, "short")
        deck.set_class(deck_rows < 10, "tiny")
        disc_width = Vinyl.width_for(deck_rows * 2)
        self._disc.display = width >= 56
        self._disc.styles.width = disc_width
        self.query_one("#queue").display = width >= disc_width + 38 + 44

    # -- the clock -----------------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.25, now - self._last)
        self._last = now
        self._frame += 1
        self._drain()
        deck = self.deck
        deck.tick()
        if deck.changes != self._seen_change:
            self._seen_change = deck.changes
            self._track_changed()
        if deck.failure:
            self.flash(f"Couldn't play this one: {deck.failure}. Links to YouTube and similar sites need yt-dlp.", 8.0)
            deck.failure = ""
        self._follow_stream()
        if self.message and now >= self._message_until:
            self.message = ""
        if self.place is not None and now >= self._weather_due and not self._weather_busy:
            self._refresh_weather()

        sky = self._sky
        sky_weather, daylight, caption = self._sky_now()
        sky.scene.set_weather(sky_weather)
        sky.scene.set_daylight(daylight)
        sky.caption = caption
        self._sky_dt += dt
        if self._frame % 2 == 1:
            sky.step(self._sky_dt)
            self._sky_dt = 0.0
        self._ease_palette(dt)

        self.spectrum.step(dt, deck.playing, deck.volume / 100.0)
        self._disc.step(dt)
        self._bars.sync()
        for widget in self._painted:
            widget.sync()

    def _sky_now(self) -> tuple[Weather, Daylight, str]:
        mode = self.config.sky
        if mode == "song" and self.mood is not None and self.deck.current is not None:
            scene = mood.SCENES[self.mood.scene]
            return scene.weather, scene.daylight, f"{scene.label}, {self.mood.why}"
        if mode in mood.SCENES:
            scene = mood.SCENES[mode]
            return scene.weather, scene.daylight, f"{scene.label}, pinned"
        if self.report is not None:
            caption = "Live sky" if mode == "weather" else ""
            return self.report.sky(), self.report.daylight(), caption
        return Weather(cover=0.18), weather.clock_daylight(), ""

    def _ease_palette(self, dt: float) -> None:
        scene = self._sky.scene
        theme = self.themes.get(self.config.colors) or self.themes["sky"]
        first, second = scene.accents
        look = self.look
        if theme.name == "cover" and look is not None and self.deck.current is not None:
            target = Palette(look.background, look.accent, look.accent2, look.accent, look.accent2)
        elif theme.accent is not None:
            second = theme.accent2 or theme.accent
            target = Palette(
                theme.background or scene.natural_ground,
                theme.accent,
                second,
                theme.disc or theme.accent,
                theme.disc2 or second,
            )
        else:
            target = Palette(scene.natural_ground, first, second, first, second)
        if not self._palette_ready:
            self.palette = target
            self._palette_ready = True
        else:
            self.palette.ease_to(target, 1.0 - math.exp(-dt / 0.45))
        scene.ground_override = self.palette.bg
        if self.palette.bg != self._screen_bg:
            self._screen_bg = self.palette.bg
            self.screen_stack[0].styles.background = Color(*self.palette.bg)

    # -- songs ---------------------------------------------------------------

    def _track_changed(self) -> None:
        track = self.deck.current
        if track is None:
            return
        self.spectrum.retune(track.path, self._tempos.get(track.path))
        path = track.path
        if path in self._looks:
            self.look = self._looks[path]
        if path in self._moods:
            self.mood = self._moods[path]
        if path not in self._studying and (path not in self._looks or path not in self._moods):
            self._studying.add(path)
            threading.Thread(target=self._study, args=(track,), name="cloudspin-study", daemon=True).start()

    def _follow_stream(self) -> None:
        """Links only learn their real name once they start playing."""
        track = self.deck.current
        if track is None or not track.is_stream or track.pinned:
            return
        backend = self.deck.backend
        title = getattr(backend, "media_title", "")
        artist = getattr(backend, "media_artist", "")
        if (not title or title == track.title or title in track.path) and (not artist or artist == track.artist):
            return
        if title and title not in track.path:
            track.title = title
        if artist:
            track.artist = artist
        look = look_for(track.title, track.artist, track.album, None)
        guess = mood.from_words(track.title, track.artist, track.album, track.genre, self.config.moods)
        self._looks[track.path] = self.look = look
        if track.path not in self._heard:
            self._moods[track.path] = self.mood = guess

    def _open(self, target: str | None) -> None:
        if not target:
            return
        target = target.strip()
        if "://" in target:
            if self.deck.backend.silent:
                self.flash("Links need mpv for sound, and mpv isn't installed.", 7.0)
                return
            if links.is_collection(target):
                self.flash("Reading the playlist…", 30.0)

                def work() -> None:
                    try:
                        self._inbox.put(("playlist", links.expand(target), ""))
                    except links.LinkError as error:
                        self._inbox.put(("playlist", [], str(error)))

                threading.Thread(target=work, name="cloudspin-playlist", daemon=True).start()
                return
            tracks = [Track(path=target, title=target.split("://", 1)[1].rstrip("/") or target)]
        else:
            tracks = library.scan([target]) if Path(target).expanduser().exists() else []
            if not tracks:
                self.flash("That isn't a link, and no music was found at that path.", 6.0)
                return
        first = self.deck.add(tracks)
        self.deck.play(first)
        if len(tracks) > 1:
            self.flash(f"Added {len(tracks)} songs")
            threading.Thread(target=self._read_all_tags, name="cloudspin-tags", daemon=True).start()
        else:
            self.flash("Opening the link…" if tracks[0].is_stream else "Added to the queue")

    def _study(self, track) -> None:
        """Runs off the main thread: read the song's tags, cover and character."""
        path = track.path
        try:
            library.read_tags(track)
            art = library.cover_art(path)
            self._inbox.put(("look", path, look_for(track.title, track.artist, track.album, art)))
            ruled = mood.from_rules(track.title, track.artist, track.album, track.genre, self.config.moods)
            if ruled is not None:
                self._inbox.put(("mood", path, ruled, "sound"))  # your rules outrank everything
                return
            # Words give an instant first guess; the sound of the song has the final say.
            guess = mood.from_words(track.title, track.artist, track.album, track.genre)
            self._inbox.put(("mood", path, guess, "words"))
            features = self._listen(track) if self.config.listen else None
            if features is not None:
                self._inbox.put(("tempo", path, features.tempo))
                self._inbox.put(("mood", path, mood.from_features(features), "sound"))
        except Exception:  # a broken file must never take the app down
            self._inbox.put(("mood", path, None, "words"))
        finally:
            self._inbox.put(("studied", path))

    def _listen(self, track) -> mood.Features | None:
        """Measure a song, remembering the answer so it is only ever done once."""
        path = track.path
        key = path
        if not track.is_stream:
            try:
                stat = Path(path).stat()
                key = f"{path}|{stat.st_size}|{int(stat.st_mtime)}"
            except OSError:
                return None
        known = self._features.get(key)
        if known is not None:
            return mood.Features(*known)
        backend = self.deck.backend
        if track.is_stream and backend.silent:
            return None
        features = mood.listen(path, getattr(backend, "binary", None), getattr(backend, "ytdlp", None))
        if features is None:
            return None
        with self._features_lock:
            self._features[key] = [features.loudness, features.tempo, features.onsets, features.bass, features.brightness]
            while len(self._features) > 4000:
                self._features.pop(next(iter(self._features)))
            try:
                target = cache_dir() / "listened.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(self._features), encoding="utf-8")
            except OSError:
                pass
        return features

    def _read_all_tags(self) -> None:
        for track in list(self.deck.tracks):
            if self._closing:
                return
            if not track.artist and not track.is_stream:
                library.read_tags(track)

    def _drain(self) -> None:
        current = self.deck.current.path if self.deck.current else None
        while True:
            try:
                item = self._inbox.get_nowait()
            except queue.Empty:
                return
            kind = item[0]
            if kind == "look":
                self._looks[item[1]] = item[2]
                if item[1] == current:
                    self.look = item[2]
            elif kind == "mood":
                path, guess, source = item[1], item[2], item[3]
                if source == "sound":
                    self._heard.add(path)
                elif path in self._heard:
                    continue  # a guess from words never replaces what was heard
                self._moods[path] = guess
                if path == current:
                    self.mood = guess
            elif kind == "studied":
                self._studying.discard(item[1])
            elif kind == "playlist":
                tracks, problem = item[1], item[2]
                if problem:
                    self.flash(problem, 8.0)
                else:
                    first = self.deck.add(tracks)
                    self.deck.play(first)
                    self.flash(f"Added {len(tracks)} songs from the playlist", 5.0)
            elif kind == "tempo":
                self._tempos[item[1]] = item[2]
                if item[1] == current:
                    self.spectrum.retune(item[1], item[2])
            elif kind == "report":
                self._weather_busy = False
                self._set_report(item[1])
            elif kind == "place":
                self._weather_busy = False
                place, report = item[1], item[2]
                self.place = place
                self.config.city = place.name
                self.config.country = place.country
                self.config.latitude = place.latitude
                self.config.longitude = place.longitude
                self.config.save()
                self._set_report(report)
                self.flash(f"Weather now follows {place.label}")
            elif kind == "weather_error":
                self._weather_busy = False
                self.flash(item[1], 6.0)
                if self.report is None and self.place is None:
                    self._sky.note = "Press c to choose your city"

    # -- weather -------------------------------------------------------------

    def _set_report(self, report: weather.Report) -> None:
        self.report = report
        self._sky.note = ""
        self._sky.hint = ""
        self._sky.set_report(report)

    def _refresh_weather(self) -> None:
        place = self.place
        if place is None:
            return
        self._weather_busy = True
        self._weather_due = time.monotonic() + WEATHER_EVERY
        units = self.config.units

        def work() -> None:
            try:
                report = weather.fetch(place, units, cache_dir() / "weather.json")
            except weather.WeatherError as error:
                self._inbox.put(("weather_error", str(error)))
            else:
                self._inbox.put(("report", report))

        threading.Thread(target=work, name="cloudspin-weather", daemon=True).start()

    def _find_city(self, name: str | None) -> None:
        if not name:
            return
        self.flash(f"Looking for {name}…", 10.0)
        self._weather_busy = True
        self._weather_due = time.monotonic() + WEATHER_EVERY
        units = self.config.units

        def work() -> None:
            try:
                place = weather.geocode(name)
                report = weather.fetch(place, units, cache_dir() / "weather.json")
            except weather.WeatherError as error:
                self._inbox.put(("weather_error", str(error)))
            else:
                self._inbox.put(("place", place, report))

        threading.Thread(target=work, name="cloudspin-city", daemon=True).start()

    # -- actions -------------------------------------------------------------

    def flash(self, message: str, seconds: float = 3.0) -> None:
        self.message = message
        self._message_until = time.monotonic() + seconds

    def set_volume(self, volume: int) -> None:
        self.deck.set_volume(volume)
        self.config.volume = self.deck.volume

    def action_toggle_play(self) -> None:
        self.deck.toggle()

    def action_next_track(self) -> None:
        self.deck.next()

    def action_previous_track(self) -> None:
        self.deck.previous()

    def action_seek(self, seconds: int) -> None:
        self.deck.seek_by(seconds)

    def action_volume(self, step: int) -> None:
        self.set_volume(self.deck.volume + step)

    def action_shuffle(self) -> None:
        self.deck.toggle_shuffle()
        self.flash("Shuffle is on" if self.deck.shuffle else "Shuffle is off")

    def action_repeat(self) -> None:
        self.deck.cycle_repeat()
        self.flash({"off": "Repeat is off", "all": "Repeating the whole queue", "one": "Repeating this song"}[self.deck.repeat])

    def action_cycle_sky(self) -> None:
        modes = ["song", "weather", *mood.SCENES]
        mode = modes[(modes.index(self.config.sky) + 1) % len(modes)]
        self.config.sky = mode
        self.config.save()
        if mode == "song":
            self.flash("The sky follows the song")
        elif mode == "weather":
            self.flash("The sky follows the weather" if self.place else "The sky follows the weather. Press c to choose your city")
        else:
            self.flash(f"The sky is pinned to {mood.SCENES[mode].label.lower()}")

    def action_cycle_colors(self) -> None:
        names = list(self.themes)
        name = names[(names.index(self.config.colors) + 1) % len(names)]
        self.config.colors = name
        self.config.save()
        label = self.themes[name].label
        self.flash(label if name in ("sky", "cover") else f"Colours: {label}")

    def action_city(self) -> None:
        self.push_screen(city_prompt(self.config.city), self._find_city)

    def action_link(self) -> None:
        self.push_screen(link_prompt(), self._open)

    def action_units(self) -> None:
        self.config.units = "imperial" if self.config.units == "metric" else "metric"
        self.config.save()
        self.flash("Showing °F and mph" if self.config.units == "imperial" else "Showing °C and km/h")
        if self.place is not None:
            self._refresh_weather()

    def action_help(self) -> None:
        self.push_screen(Help())

    def on_mouse_scroll_up(self) -> None:
        self.set_volume(self.deck.volume + 2)

    def on_mouse_scroll_down(self) -> None:
        self.set_volume(self.deck.volume - 2)

    def on_unmount(self) -> None:
        self._closing = True
        self.config.volume = self.deck.volume
        self.config.save()
        self.deck.close()
