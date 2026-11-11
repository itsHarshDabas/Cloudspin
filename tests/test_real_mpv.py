"""Playback against a real mpv, with the sound thrown away.

Skipped when mpv isn't installed. On Windows CI, CLOUDSPIN_TEST_DOWNLOAD=1 makes
this download mpv first, which also checks the downloader end to end.
"""

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402

from cloudspin import mpvget  # noqa: E402
from cloudspin.player import Deck, MpvBackend, Track  # noqa: E402


@pytest.fixture(scope="module")
def mpv_binary():
    binary = mpvget.find_mpv()
    if binary is None and mpvget.WINDOWS and os.environ.get("CLOUDSPIN_TEST_DOWNLOAD"):
        binary = mpvget.download_mpv()
        assert Path(binary).exists() and mpvget.find_mpv() == binary
    if binary is None:
        pytest.skip("mpv is not installed")
    return binary


def wait_for(check, seconds=15.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


def test_real_mpv_plays_seeks_pauses_and_moves_on(mpv_binary, tmp_path):
    first, second = tmp_path / "first song.wav", tmp_path / "second.wav"
    synth.write_wav(first, synth.gentle()[: 8000 * 4])
    synth.write_wav(second, synth.gentle()[: 8000 * 4])
    backend = MpvBackend(volume=40, binary=mpv_binary, extra_args=("--ao=null", "--no-config"))
    deck = Deck(backend, [Track(str(first), "first"), Track(str(second), "second")])
    try:
        deck.play(0)
        assert wait_for(lambda: 3.5 < backend.duration < 4.5), "mpv should report the length"
        assert wait_for(lambda: backend.position > 0.3), "time should move while playing"
        deck.toggle()
        time.sleep(0.4)
        held = backend.position
        time.sleep(0.5)
        assert abs(backend.position - held) < 0.15, "time should stop while paused"
        deck.toggle()
        deck.seek_to(0.8)
        assert wait_for(lambda: backend.position > 2.5), "seeking should jump ahead"
        assert wait_for(lambda: (deck.tick(), deck.index == 1)[1]), "the next song should start at the end"
        assert backend.alive
    finally:
        deck.close()


def test_real_mpv_reports_a_file_it_cannot_play(mpv_binary, tmp_path):
    backend = MpvBackend(binary=mpv_binary, extra_args=("--ao=null", "--no-config"))
    deck = Deck(backend, [Track(str(tmp_path / "missing.mp3"), "missing")])
    try:
        deck.play(0)
        assert wait_for(lambda: (deck.tick(), deck.failure != "")[1])
    finally:
        deck.close()


def test_ytdlp_ships_with_cloudspin():
    assert mpvget.find_ytdlp() is not None


def test_install_hint_names_a_command():
    assert "mpv" in mpvget.install_hint()
