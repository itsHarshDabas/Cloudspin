import json
import sys
import time
from pathlib import Path

import pytest

from cloudspin.player import BackendError, Deck, DemoBackend, MpvBackend, Track

FAKE_MPV = str(Path(__file__).parent / "fake_mpv.py")


def tracks(n=4):
    return [Track(f"demo://{i}", f"Song {i}", duration=100) for i in range(n)]


def test_nothing_plays_until_asked():
    deck = Deck(DemoBackend(), tracks())
    assert deck.current is None and not deck.playing
    deck.toggle()
    assert deck.index == 0 and deck.playing
    deck.toggle()
    assert not deck.playing


def test_next_previous_and_history():
    deck = Deck(DemoBackend(), tracks())
    deck.play(0)
    deck.next()
    deck.next()
    assert deck.index == 2
    deck.previous()
    assert deck.index == 1
    deck.backend.position = 10
    deck.previous()
    assert deck.index == 1 and deck.position == 0, "a song that is under way restarts first"


def test_queue_end_stops_unless_repeating():
    deck = Deck(DemoBackend(), tracks(2))
    deck.play(1)
    deck.backend.position = 100
    deck.backend._ended = True
    deck.tick()
    assert deck.index == 1 and not deck.playing
    deck.cycle_repeat()
    assert deck.repeat == "all"
    deck.play(1)
    deck.backend._ended = True
    deck.tick()
    assert deck.index == 0 and deck.playing
    deck.cycle_repeat()
    assert deck.repeat == "one"
    deck.backend._ended = True
    deck.tick()
    assert deck.index == 0 and deck.playing


def test_shuffle_plays_everything_once_and_keeps_the_current_song():
    deck = Deck(DemoBackend(), tracks(8), seed=1)
    deck.play(3)
    deck.toggle_shuffle()
    assert deck.index == 3
    heard = [3]
    for _ in range(7):
        deck.next()
        heard.append(deck.index)
    assert sorted(heard) == list(range(8)) and heard != list(range(3, 8)) + [0, 1, 2]
    deck.toggle_shuffle()
    assert deck.upcoming(2) == [(deck.index + 1) % 8, (deck.index + 2) % 8][: len(deck.upcoming(2))]


def test_upcoming_wraps_only_when_repeating():
    deck = Deck(DemoBackend(), tracks(3))
    deck.play(1)
    assert deck.upcoming(5) == [2]
    deck.repeat = "all"
    assert deck.upcoming(5) == [2, 0, 1]


def test_seek_and_volume_stay_in_range():
    deck = Deck(DemoBackend(), tracks())
    deck.play(0)
    deck.seek_by(-50)
    assert deck.position == 0
    deck.seek_to(2.0)
    assert 99 <= deck.position <= 100
    deck.set_volume(140)
    assert deck.volume == 100


def test_adding_songs_extends_the_queue():
    deck = Deck(DemoBackend(), tracks(2))
    first = deck.add([Track("demo://new", "New")])
    deck.play(first)
    assert deck.current.title == "New" and deck.upcoming(3) == []


def test_demo_backend_keeps_time_and_ends(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    deck = Deck(DemoBackend(), [Track("demo://a", "A", duration=10), Track("demo://b", "B", duration=10)])
    deck.play(0)
    clock[0] += 4
    deck.tick()
    assert deck.position == pytest.approx(4)
    deck.toggle()
    clock[0] += 50
    deck.tick()
    assert deck.position == pytest.approx(4), "time stands still while paused"
    deck.toggle()
    clock[0] += 7
    deck.tick()
    assert deck.index == 1, "the next song starts when one ends"


def wait_for(check, seconds=3.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def mpv():
    backend = MpvBackend(volume=55, binary=FAKE_MPV)
    log = Path(backend._dir) / "commands.log"
    yield backend, lambda: [json.loads(line) for line in log.read_text().splitlines()]
    backend.close()


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in mpv speaks over a unix socket")
def test_mpv_backend_speaks_the_protocol(mpv):
    backend, commands = mpv
    deck = Deck(backend, [Track("/music/one.flac", "one"), Track("/music/two.flac", "two")], volume=55)
    deck.play(0)
    assert wait_for(lambda: backend.duration == 123.5 and backend.position == 1.25)
    assert backend.media_title == "A Title From The Stream" and backend.media_artist == "Some Band"
    deck.seek_by(30)
    deck.toggle()
    deck.set_volume(80)
    assert wait_for(lambda: len(commands()) >= 9)
    sent = commands()
    assert ["observe_property", 1, "time-pos"] in sent
    assert ["loadfile", "/music/one.flac", "replace"] in sent
    assert ["seek", 31.25, "absolute"] in sent
    assert ["set_property", "pause", True] in sent
    assert ["set_property", "volume", 80] in sent
    assert wait_for(lambda: backend.position == 31.25)


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in mpv speaks over a unix socket")
def test_mpv_backend_moves_on_at_the_end_and_stops_on_errors(mpv):
    backend, _ = mpv
    deck = Deck(backend, [Track("/music/short.mp3", "short"), Track("https://example.com/missing", "link"), Track("/c.mp3", "c")])
    deck.play(0)
    assert wait_for(lambda: (deck.tick(), deck.index == 1)[1]), "eof should start the next song"
    assert wait_for(lambda: (deck.tick(), deck.failure != "")[1])
    assert deck.failure == "loading failed" and deck.index == 1 and not deck.playing


def test_missing_mpv_is_explained(monkeypatch):
    monkeypatch.setattr("cloudspin.player.shutil.which", lambda name: None)
    with pytest.raises(BackendError, match="mpv isn't installed"):
        MpvBackend()
