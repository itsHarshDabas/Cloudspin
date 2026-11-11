import shutil

import pytest
import synth

from cloudspin import mood


@pytest.mark.parametrize(
    "genre, scene",
    [
        ("Drift Phonk", "storm"),
        ("Metal", "storm"),
        ("lofi hip hop", "evening"),
        ("Ambient", "evening"),
        ("Dream Pop", "fog"),
        ("Synthwave", "night"),
        ("K-Pop", "day"),
        ("Hip-Hop/Rap", "day"),
        ("Christmas", "snow"),
        ("Blues", "rain"),
        ("New Age", "dawn"),
    ],
)
def test_genres_pick_a_sky(genre, scene):
    assert mood.from_words("Untitled", genre=genre).scene == scene


def test_titles_only_trust_weather_words():
    assert mood.from_words("Midnight City").scene == "night"
    assert mood.from_words("Rainy Days").scene == "rain"
    assert mood.from_words("Thunderstruck").scene == "storm"
    assert mood.from_words("Pop the Trunk") is None
    assert mood.from_words("Start Again") is None, "'star' must not match inside 'start'"


def test_your_rules_win():
    rules = {"kordhell": "storm", "sunday": "dawn", "broken": "not-a-scene"}
    assert mood.from_words("Track 1", artist="Kordhell", genre="Jazz", rules=rules).scene == "storm"
    assert mood.from_words("Sunday Best", rules=rules).scene == "dawn"
    assert mood.from_words("Broken", rules=rules) is None


def test_every_rule_points_at_a_real_scene():
    for scene, _, _ in mood._GENRE_RULES + mood._TITLE_RULES:
        assert scene in mood.SCENES


def test_listening_tells_heavy_from_gentle():
    heavy = mood.measure(synth.heavy())
    gentle = mood.measure(synth.gentle())
    bright = mood.measure(synth.bright())
    assert heavy.intensity > bright.intensity > gentle.intensity
    assert heavy.bass > 0.6 > bright.bass
    assert 120 <= heavy.tempo <= 160
    assert mood.from_features(heavy).scene == "storm"
    assert mood.from_features(gentle).scene in ("night", "evening")
    assert mood.from_features(bright).scene == "day"


def test_silence_and_scraps_are_left_alone():
    from array import array

    assert mood.measure(array("h", [0] * 40000)) is None
    assert mood.measure(array("h", [100] * 500)) is None
    assert mood.listen("https://example.com/stream") is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_listen_decodes_a_real_file(tmp_path):
    path = synth.write_wav(tmp_path / "heavy.wav", synth.heavy(12))
    features = mood.listen(str(path))
    assert features is not None and features.bass > 0.6
    assert mood.listen(str(tmp_path / "nothing.wav")) is None
