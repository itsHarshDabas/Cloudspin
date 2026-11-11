"""Playlist links: which links count as playlists, and turning what yt-dlp and
Spotify's page say into songs."""

import json

from cloudspin import links
from cloudspin.links import is_collection, tracks_from_spotify_page, tracks_from_ytdlp


def test_which_links_are_playlists():
    assert is_collection("https://www.youtube.com/playlist?list=PL123")
    assert is_collection("https://music.youtube.com/playlist?list=OLAK5uy_abc")
    assert is_collection("https://www.youtube.com/watch?v=abc&list=PL123")
    assert is_collection("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=x")
    assert is_collection("https://open.spotify.com/intl-de/album/4aawyAB9vmqN3uQ7FjRGTy")
    assert is_collection("https://soundcloud.com/someone/sets/a-set")
    assert not is_collection("https://www.youtube.com/watch?v=abc")
    assert not is_collection("https://example.com/stream.mp3")
    assert not is_collection("/home/me/Music/playlist")


def test_youtube_playlists_become_songs():
    info = {
        "_type": "playlist",
        "entries": [
            {"id": "aaa", "url": "https://www.youtube.com/watch?v=aaa", "title": "First", "channel": "Band - Topic", "duration": 201},
            {"id": "bbb", "ie_key": "Youtube", "title": "Second", "uploader": "Someone", "duration": None},
            {"id": "ccc", "url": "https://www.youtube.com/watch?v=ccc", "title": "[Deleted video]"},
            None,
        ],
    }
    tracks = tracks_from_ytdlp(info, "https://www.youtube.com/playlist?list=PL1")
    assert [(t.path, t.title, t.artist, t.duration) for t in tracks] == [
        ("https://www.youtube.com/watch?v=aaa", "First", "Band", 201.0),
        ("https://www.youtube.com/watch?v=bbb", "Second", "Someone", 0.0),
    ]
    assert not tracks[0].pinned  # the site's own title may still improve on this one


def test_a_single_video_is_one_song():
    info = {"id": "x", "webpage_url": "https://www.youtube.com/watch?v=x", "title": "Solo", "uploader": "Me", "duration": 61}
    assert [t.title for t in tracks_from_ytdlp(info, "https://www.youtube.com/watch?v=x")] == ["Solo"]


def spotify_page(track_list):
    data = {"props": {"pageProps": {"state": {"data": {"entity": {"type": "playlist", "trackList": track_list}}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def test_spotify_songs_are_matched_on_youtube():
    page = spotify_page(
        [
            {"uri": "spotify:track:1", "title": "Blue Hour", "subtitle": "Nine Volt Garden, Kestrel", "duration": 229000},
            {"uri": "spotify:track:2", "title": "", "subtitle": "Nobody"},
        ]
    )
    tracks = tracks_from_spotify_page(page)
    assert len(tracks) == 1
    song = tracks[0]
    assert song.path == "ytdl://ytsearch1:Nine Volt Garden, Kestrel - Blue Hour"
    assert (song.title, song.artist, song.duration) == ("Blue Hour", "Nine Volt Garden, Kestrel", 229.0)
    assert song.pinned and song.is_stream  # keep Spotify's name, not the video's


def test_a_spotify_page_without_songs_gives_nothing():
    assert tracks_from_spotify_page("<html>nothing here</html>") == []
    assert tracks_from_spotify_page('<script id="__NEXT_DATA__">{broken</script>') == []


def test_expand_explains_failures(monkeypatch):
    import pytest

    monkeypatch.setattr(links, "_with_ytdlp", lambda link: [])
    with pytest.raises(links.LinkError, match="empty, private"):
        links.expand("https://www.youtube.com/playlist?list=PLnothing")
