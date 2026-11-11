"""Checks against the real internet. Only run when CLOUDSPIN_TEST_NETWORK=1,
and allowed to fail in CI, because other people's servers decide the outcome."""

import os

import pytest

from cloudspin import links, mood, mpvget

pytestmark = pytest.mark.skipif(not os.environ.get("CLOUDSPIN_TEST_NETWORK"), reason="network tests are opt-in")


def test_spotify_playlist_lists_its_songs():
    tracks = links.expand("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
    print("spotify:", len(tracks), [(t.artist, t.title, t.duration) for t in tracks[:3]])
    assert len(tracks) >= 10 and all(t.title and t.pinned for t in tracks)
    assert sum(1 for t in tracks if t.artist) >= len(tracks) // 2


def test_ytdlp_reads_a_list_of_results():
    tracks = links._with_ytdlp("ytsearch3:creative commons lofi music")
    print("youtube:", [(t.path, t.title, t.artist, t.duration) for t in tracks])
    assert len(tracks) == 3 and all("://" in t.path for t in tracks)


def test_listening_to_a_link():
    binary = mpvget.find_mpv()
    if binary is None:
        pytest.skip("mpv is not installed")
    features = mood.listen("ytdl://ytsearch1:creative commons lofi music", binary, mpvget.find_ytdlp())
    print("heard:", features, features and mood.from_features(features))
    assert features is not None
