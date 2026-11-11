"""Turning a playlist link into songs.

YouTube, YouTube Music, SoundCloud and the many other sites yt-dlp knows are
read with yt-dlp, and their songs play straight from the site.

Spotify is different: its audio is locked to its own apps, so nothing outside
them can play it. Cloudspin reads the *list* of songs from the public playlist
page and plays each one from the closest match on YouTube.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request

from .player import Track

LIMIT = 500
_SPOTIFY = re.compile(r"open\.spotify\.com/(?:intl-[a-z-]+/)?(?:embed/)?(playlist|album|track)/([A-Za-z0-9]+)")
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


class LinkError(Exception):
    """A playlist could not be read; the message is written for the user."""


def is_collection(link: str) -> bool:
    """Does this link point at many songs rather than one?"""
    if "://" not in link:
        return False
    lowered = link.lower()
    if "open.spotify.com/" in lowered:
        return True
    return any(mark in lowered for mark in ("list=", "/playlist", "/album/", "/sets/", "/browse/"))


def expand(link: str) -> list[Track]:
    """Every song behind a playlist link. Raises LinkError with a readable reason."""
    if _SPOTIFY.search(link):
        tracks = _spotify(link)
    else:
        tracks = _with_ytdlp(link)
    if not tracks:
        raise LinkError("That playlist is empty, private, or couldn't be read.")
    return tracks[:LIMIT]


# -- yt-dlp ------------------------------------------------------------------


def _with_ytdlp(link: str) -> list[Track]:
    try:
        import yt_dlp
    except ImportError as error:
        raise LinkError("Reading playlists needs yt-dlp, which should have been installed with Cloudspin.") from error
    options = {"extract_flat": "in_playlist", "quiet": True, "no_warnings": True, "skip_download": True, "playlistend": LIMIT}
    try:
        with yt_dlp.YoutubeDL(options) as reader:
            info = reader.extract_info(link, download=False)
    except Exception as error:  # yt-dlp raises many kinds; all mean "couldn't read it"
        reason = str(error).replace("ERROR: ", "").strip().splitlines()[0][:140] if str(error).strip() else "unknown error"
        raise LinkError(f"That playlist couldn't be read ({reason}).") from error
    return tracks_from_ytdlp(info or {}, link)


def tracks_from_ytdlp(info: dict, link: str) -> list[Track]:
    entries = info.get("entries")
    if entries is None:
        entries = [info]
    tracks = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url") or entry.get("webpage_url") or ""
        if "://" not in url:
            if entry.get("id") and "youtu" in f"{entry.get('ie_key', '')}{link}".lower():
                url = f"https://www.youtube.com/watch?v={entry['id']}"
            else:
                continue
        title = str(entry.get("title") or "").strip()
        if not title or title in ("[Deleted video]", "[Private video]"):
            continue
        artist = str(entry.get("artist") or entry.get("channel") or entry.get("uploader") or "")
        artist = artist.removesuffix(" - Topic").strip()
        tracks.append(Track(path=url, title=title, artist=artist, duration=float(entry.get("duration") or 0.0)))
    return tracks


# -- Spotify -----------------------------------------------------------------


def _find_track_list(node) -> list | None:
    if isinstance(node, dict):
        found = node.get("trackList")
        if isinstance(found, list) and found:
            return found
        if node.get("type") == "track" and node.get("title") and "artists" in node:
            return [node]
        for value in node.values():
            deeper = _find_track_list(value)
            if deeper:
                return deeper
    elif isinstance(node, list):
        for value in node:
            deeper = _find_track_list(value)
            if deeper:
                return deeper
    return None


def tracks_from_spotify_page(page: str) -> list[Track]:
    """Pull the song list out of a Spotify embed page."""
    match = _NEXT_DATA.search(page)
    if not match:
        return []
    try:
        data = json.loads(html.unescape(match.group(1)) if "&quot;" in match.group(1) else match.group(1))
    except ValueError:
        return []
    tracks = []
    for item in _find_track_list(data) or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        artist = item.get("subtitle")
        if not artist and isinstance(item.get("artists"), list):
            artist = ", ".join(str(a.get("name", "")) for a in item["artists"] if isinstance(a, dict))
        artist = str(artist or "").replace(" ", " ").strip()
        if not title:
            continue
        # Spotify itself can't be played from here, so each song is found on YouTube.
        search = f"{artist} - {title}" if artist else title
        tracks.append(
            Track(
                path=f"ytdl://ytsearch1:{search}",
                title=title,
                artist=artist,
                duration=float(item.get("duration") or 0) / 1000.0,
                pinned=True,
            )
        )
    return tracks


def _spotify(link: str) -> list[Track]:
    kind, ident = _SPOTIFY.search(link).groups()  # type: ignore[union-attr]
    request = urllib.request.Request(
        f"https://open.spotify.com/embed/{kind}/{ident}", headers={"User-Agent": _BROWSER, "Accept-Language": "en"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            page = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise LinkError("Spotify says that playlist doesn't exist or is private.") from error
        raise LinkError(f"Spotify answered with an error ({error.code}).") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise LinkError("Can't reach Spotify. Check your connection.") from error
    tracks = tracks_from_spotify_page(page)
    if not tracks:
        raise LinkError("Spotify's page didn't list any songs. The playlist may be private.")
    return tracks
