"""The whole app, driven headless: it has to boot at any size, survive every
key, and keep the sky and colours in step with the song."""

import json
import time
import wave
from pathlib import Path

import pytest

from cloudspin import weather
from cloudspin.app import CloudspinApp
from cloudspin.config import Config
from cloudspin.player import DEMO_TRACKS, Deck, DemoBackend, Track
from cloudspin.songlook import from_art, from_text
from cloudspin.themes import load_themes

FIXTURE = Path(__file__).parent / "fixtures" / "forecast.json"


def make_app(sky="song", colors="sky", tracks=None) -> CloudspinApp:
    config = Config(path=None)
    config.sky, config.colors = sky, colors
    songs = [Track(**vars(t)) for t in (DEMO_TRACKS if tracks is None else tracks)]
    return CloudspinApp(config, Deck(DemoBackend(), songs, seed=1))


def give_weather(app: CloudspinApp) -> None:
    place = weather.Place("New Delhi", 28.61, 77.21, "IN")
    app.place = place
    app._weather_due = time.monotonic() + 1e9  # never touch the network in tests
    app._set_report(weather.parse(json.loads(FIXTURE.read_text()), place))


def frame(app: CloudspinApp) -> str:
    """Everything on screen, as plain text."""
    strips = app.screen._compositor.render_strips()
    return "\n".join(strip.text for strip in strips)


@pytest.mark.parametrize("size", [(40, 12), (60, 18), (80, 24), (100, 30), (132, 40), (220, 60)])
async def test_boots_and_paints_at_any_size(size):
    app = make_app()
    async with app.run_test(size=size) as pilot:
        give_weather(app)
        app.deck.play(0)
        await pilot.pause(0.3)
        text = frame(app)
        assert "Petrichor" in text
        assert len(text.splitlines()) == size[1]


async def test_weather_is_written_on_the_sky():
    app = make_app(sky="weather")
    async with app.run_test(size=(132, 40)) as pilot:
        give_weather(app)
        await pilot.pause(0.3)
        text = frame(app)
        for expected in ("New Delhi, IN", "Partly cloudy", "Feels like 26°", "Humidity 46%", "Thu", "Sun"):
            assert expected in text


async def test_without_a_city_it_says_how_to_set_one():
    app = make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.2)
        assert "Press c to choose your city" in frame(app)


async def test_sky_follows_the_song():
    app = make_app()
    async with app.run_test(size=(132, 40)) as pilot:
        give_weather(app)
        app.deck.play(2)  # tagged "Drift phonk"
        await pilot.pause(0.6)
        assert app.mood is not None and app.mood.scene == "storm"
        assert app._sky.scene.target.storm == 1.0
        assert "Thunderstorm" in frame(app)
        app.deck.play(0)  # tagged "Lo-fi"
        await pilot.pause(0.6)
        assert app.mood.scene == "evening"
        assert app._sky.scene.target.storm == 0.0


async def test_keys_drive_the_deck():
    app = make_app()
    async with app.run_test(size=(132, 40)) as pilot:
        deck = app.deck
        await pilot.press("space")
        assert deck.playing and deck.index == 0
        await pilot.press("n")
        assert deck.index == 1
        await pilot.press("space")
        assert not deck.playing
        await pilot.press("plus", "plus", "minus")
        assert deck.volume == 75
        await pilot.press("s")
        assert deck.shuffle
        await pilot.press("r", "r")
        assert deck.repeat == "one"
        deck.backend.seek(50)
        await pilot.press("right")
        assert deck.position >= 55
        await pilot.press("p")
        assert deck.position < 5


async def test_every_sky_and_theme_can_be_cycled_through():
    app = make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        give_weather(app)
        app.deck.play(1)
        seen_skies, seen_themes = set(), set()
        for _ in range(12):
            await pilot.press("w")
            await pilot.pause(0.05)
            seen_skies.add(app.config.sky)
        for _ in range(len(app.themes) + 1):
            await pilot.press("t")
            await pilot.pause(0.05)
            seen_themes.add(app.config.colors)
        assert {"song", "weather", "storm", "snow", "fog", "night"} <= seen_skies
        assert seen_themes == set(app.themes)


async def test_dialogs_open_and_close():
    app = make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("question_mark")
        await pilot.pause(0.1)
        assert "Keys" in frame(app)
        await pilot.press("escape")
        await pilot.press("c")
        await pilot.pause(0.1)
        assert "Where should the weather come from?" in frame(app)
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert len(app.screen_stack) == 1
        # Typing in a dialog must not trigger the player's keys.
        await pilot.press("o")
        await pilot.press("n", "s", "q")
        await pilot.pause(0.1)
        assert app.deck.index == -1 and not app.deck.shuffle and len(app.screen_stack) == 2


async def test_opening_a_path_adds_it_to_the_queue_and_plays(tmp_path):
    song = tmp_path / "Rainy Window.wav"
    with wave.open(str(song), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\0\0" * 8000)
    app = make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        before = len(app.deck.tracks)
        app._open(str(song))
        await pilot.pause(0.5)
        assert len(app.deck.tracks) == before + 1
        assert app.deck.current.title == "Rainy Window" and app.deck.playing
        assert app.mood is not None and app.mood.scene == "rain"  # "rainy" in the name


async def test_links_say_so_when_there_is_no_sound_backend():
    app = make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        before = len(app.deck.tracks)
        app._open("https://example.com/stream.mp3")
        await pilot.pause(0.1)
        assert len(app.deck.tracks) == before
        assert "mpv" in app.message


async def test_nonsense_in_the_link_box_is_explained():
    app = make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        app._open("not a link at all")
        await pilot.pause(0.1)
        assert "no music was found" in app.message


async def test_cover_theme_takes_colour_from_the_song():
    app = make_app(colors="cover")
    async with app.run_test(size=(100, 30)) as pilot:
        app.deck.play(0)
        await pilot.pause(1.6)
        look = from_text("Low Tide Radio\nWeather Report for Nobody")
        assert app.look == look
        # The palette eases toward the song's look rather than snapping.
        assert all(abs(a - b) <= 8 for a, b in zip(app.palette.bg, look.background))


def test_cover_art_gives_a_look():
    from io import BytesIO

    from PIL import Image

    image = Image.new("RGB", (200, 200), (200, 30, 50))
    image.paste((20, 40, 160), (0, 100, 200, 200))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    look = from_art(buffer.getvalue())
    assert look is not None and look.art is not None and len(look.art) == 24 * 24 * 3
    assert max(look.background) < 60  # dark enough to sit text on
    assert max(look.accent) > 200  # bright enough to read
    assert from_art(b"not an image") is None


def test_custom_themes_load_and_bad_ones_are_skipped():
    themes = load_themes(
        {
            "mine": {"accent": "#ff8800", "background": "#101010"},
            "broken": {"accent": "orange-ish"},
            "sky": {"accent": "#ffffff"},  # can't replace the live themes
        }
    )
    assert themes["mine"].accent == (255, 136, 0) and themes["mine"].accent2 == (255, 136, 0)
    assert "broken" not in themes and themes["sky"].accent is None


def test_config_round_trips(tmp_path):
    path = tmp_path / "cloudspin" / "config.toml"
    config = Config(path=path)
    config.city, config.latitude, config.longitude = 'Sao "Paulo"', -23.55, -46.63
    config.sky, config.colors, config.volume = "storm", "mine", 42
    config.moods = {"kordhell": "storm"}
    config.themes = {"mine": {"accent": "#ff8800"}}
    config.save()
    loaded = Config.load(path)
    assert (loaded.city, loaded.latitude, loaded.sky, loaded.colors, loaded.volume) == ('Sao "Paulo"', -23.55, "storm", "mine", 42)
    assert loaded.moods == {"kordhell": "storm"} and loaded.themes == {"mine": {"accent": "#ff8800"}}


def test_config_survives_a_broken_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("this is = not [valid toml")
    assert Config.load(path).sky == "song"
    path.write_text('volume = 900\nunits = "kelvin"\nsky = 7\n')
    loaded = Config.load(path)
    assert loaded.volume == 100 and loaded.units == "metric" and loaded.sky == "song"


async def test_the_mouse_works_on_buttons_progress_and_queue():
    from cloudspin.widgets import Controls, QueueView, Seekbar

    app = make_app()
    async with app.run_test(size=(132, 40)) as pilot:
        deck = app.deck
        controls = app.query_one(Controls)
        zones = {name: (start + end) // 2 for start, end, name in controls._zones}
        await pilot.click(Controls, offset=(zones["toggle"], 0))
        assert deck.playing and deck.index == 0
        await pilot.click(Controls, offset=(zones["next"], 0))
        assert deck.index == 1
        await pilot.click(Controls, offset=(zones["shuffle"], 0))
        assert deck.shuffle
        await pilot.click(Controls, offset=(zones["shuffle"], 0))
        await pilot.click(Controls, offset=(zones["toggle"], 0))
        assert not deck.playing

        bar = app.query_one(Seekbar)
        start, width = bar._bar
        await pilot.click(Seekbar, offset=(start + width // 2, 0))
        assert 0.4 < deck.progress < 0.6

        await pilot.pause(0.1)
        queue = app.query_one(QueueView)
        third = queue._shown[2]
        await pilot.click(QueueView, offset=(3, 2 + 2))
        assert deck.index == third
