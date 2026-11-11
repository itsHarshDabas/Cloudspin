"""Finding music on disk and reading what it says about itself."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from .player import Track

AUDIO_SUFFIXES = {
    ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".m4b", ".aac", ".wav",
    ".wma", ".aiff", ".aif", ".ape", ".wv", ".mka",
}  # fmt: skip
COVER_NAMES = ("cover", "folder", "front", "album", "artwork")
COVER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


def _natural(text: str) -> list:
    parts: list = []
    number = ""
    word = ""
    for ch in text.lower():
        if ch.isdigit():
            if word:
                parts.append(word)
                word = ""
            number += ch
        else:
            if number:
                parts.append(int(number))
                number = ""
            word += ch
    if number:
        parts.append(int(number))
    if word:
        parts.append(word)
    # Keep ints and strings comparable by tagging them.
    return [(0, p) if isinstance(p, int) else (1, p) for p in parts]


def scan(sources: list[str]) -> list[Track]:
    """Collect tracks from files, folders and stream URLs, in a natural order."""
    tracks: list[Track] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path)
        if key not in seen and path.suffix.lower() in AUDIO_SUFFIXES:
            seen.add(key)
            tracks.append(Track(path=key, title=path.stem))

    for source in sources:
        if "://" in source:
            if source not in seen:
                seen.add(source)
                tracks.append(Track(path=source, title=source.split("://", 1)[1].rstrip("/") or source))
            continue
        root = Path(source).expanduser()
        if root.is_file():
            add(root.resolve())
        elif root.is_dir():
            for folder, folders, files in os.walk(root, followlinks=True):
                folders[:] = sorted((d for d in folders if not d.startswith(".")), key=_natural)
                for name in sorted(files, key=_natural):
                    if not name.startswith("."):
                        add(Path(folder, name).resolve())
    return tracks


def _first(value) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value).strip() if value is not None else ""


def read_tags(track: Track) -> bool:
    """Fill in title, artist, album and length from the file. True if anything was read."""
    if track.is_stream:
        return False
    try:
        import mutagen

        audio = mutagen.File(track.path, easy=True)
    except Exception:
        return False
    if audio is None:
        return False
    tags = audio.tags or {}
    try:
        title = _first(tags.get("title"))
        artist = _first(tags.get("artist")) or _first(tags.get("albumartist"))
        album = _first(tags.get("album"))
        genre = _first(tags.get("genre"))
    except Exception:
        title = artist = album = genre = ""
    if title:
        track.title = title
    track.artist = artist or track.artist
    track.album = album or track.album
    track.genre = genre or track.genre
    length = getattr(getattr(audio, "info", None), "length", 0.0) or 0.0
    if length > 0:
        track.duration = float(length)
    return True


def cover_art(path: str) -> bytes | None:
    """The picture that goes with a track: embedded art first, then a cover file beside it."""
    if "://" in path:
        return None
    try:
        import mutagen
        from mutagen.flac import Picture

        audio = mutagen.File(path)
        if audio is not None:
            pictures = getattr(audio, "pictures", None)
            if pictures:
                return bytes(pictures[0].data)
            tags = audio.tags
            if tags is not None:
                for key in tags.keys():
                    if str(key).startswith("APIC"):
                        return bytes(tags[key].data)
                if "covr" in tags and tags["covr"]:
                    return bytes(tags["covr"][0])
                blocks = tags.get("metadata_block_picture")
                if blocks:
                    return bytes(Picture(base64.b64decode(blocks[0])).data)
    except Exception:
        pass
    try:
        folder = Path(path).parent
        for entry in sorted(folder.iterdir()):
            if entry.suffix.lower() in COVER_SUFFIXES and entry.stem.lower() in COVER_NAMES:
                return entry.read_bytes()
    except OSError:
        pass
    return None
