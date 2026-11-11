"""Playback: an mpv backend driven over its JSON IPC socket, a silent demo
backend, and the ``Deck`` that turns either into a queue with shuffle and repeat.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Track:
    path: str
    title: str
    artist: str = ""
    album: str = ""
    duration: float = 0.0
    genre: str = ""

    @property
    def is_stream(self) -> bool:
        return "://" in self.path


class BackendError(Exception):
    """The audio backend could not start; the message is written for the user."""


class DemoBackend:
    """Keeps time without making a sound. Used for ``--demo`` and when mpv is missing."""

    name = "demo"
    silent = True

    def __init__(self) -> None:
        self.position = 0.0
        self.duration = 0.0
        self.media_title = ""
        self.alive = True
        self._paused = True
        self._ended = False
        self._last = time.monotonic()

    def load(self, track: Track) -> None:
        self.position = 0.0
        self.duration = track.duration or 180.0
        self._paused = False
        self._ended = False
        self._last = time.monotonic()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._last = time.monotonic()

    def seek(self, seconds: float) -> None:
        self.position = max(0.0, min(seconds, max(0.0, self.duration - 0.2)))

    def set_volume(self, volume: int) -> None:
        pass

    def poll(self) -> None:
        now = time.monotonic()
        if not self._paused and self.duration:
            self.position += now - self._last
            if self.position >= self.duration:
                self.position = self.duration
                self._paused = True
                self._ended = True
        self._last = now

    def take_ended(self) -> bool:
        ended, self._ended = self._ended, False
        return ended

    def take_error(self) -> str:
        return ""

    def close(self) -> None:
        self.alive = False


class _SocketLink:
    """mpv's control channel on Linux and macOS: a unix socket."""

    def __init__(self, path: str) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._sock.connect(path)
        except OSError:
            self._sock.close()
            raise

    def send(self, data: bytes) -> None:
        self._sock.sendall(data)

    def receive(self) -> bytes:
        """Block until data arrives; empty bytes means mpv hung up."""
        return self._sock.recv(65536)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class _PipeLink:
    """mpv's control channel on Windows: a named pipe.

    A blocking read on a pipe handle would hold up writes from other threads,
    so this only reads what PeekNamedPipe says is already waiting.
    """

    def __init__(self, path: str) -> None:
        import msvcrt

        self._file = open(path, "r+b", buffering=0)
        self._handle = msvcrt.get_osfhandle(self._file.fileno())
        self._closed = False

    def send(self, data: bytes) -> None:
        self._file.write(data)

    def receive(self) -> bytes:
        import _winapi

        while not self._closed:
            waiting = _winapi.PeekNamedPipe(self._handle, 0)[0]
            if waiting:
                return self._file.read(waiting) or b""
            time.sleep(0.02)
        return b""

    def close(self) -> None:
        self._closed = True
        try:
            self._file.close()
        except OSError:
            pass


class MpvBackend:
    """Plays audio by running mpv in the background and talking to its IPC socket."""

    name = "mpv"
    silent = False

    def __init__(
        self,
        volume: int = 70,
        binary: str | None = None,
        ytdlp: str | None = None,
        extra_args: tuple[str, ...] = (),
    ) -> None:
        self.position = 0.0
        self.duration = 0.0
        self.media_title = ""
        self.media_artist = ""
        self.alive = False
        self._ended = False
        self._error = ""
        self._lock = threading.Lock()
        self._sock: _SocketLink | _PipeLink | None = None
        self._dir = ""
        self._process: subprocess.Popen | None = None

        binary = binary or shutil.which("mpv")
        if not binary:
            raise BackendError("mpv isn't installed, so there's no sound yet. Install mpv and start Cloudspin again.")
        self._dir = tempfile.mkdtemp(prefix="cloudspin-")
        if sys.platform == "win32":
            path = rf"\\.\pipe\cloudspin-{os.getpid()}-{Path(self._dir).name}"
        else:
            path = str(Path(self._dir) / "mpv.sock")
        options = list(extra_args)
        if ytdlp:
            # Point mpv at the yt-dlp that was installed with Cloudspin.
            options.append(f"--script-opts=ytdl_hook-ytdl_path={ytdlp}")
        try:
            self._process = subprocess.Popen(
                [
                    binary,
                    "--idle=yes",
                    "--no-video",
                    "--no-terminal",
                    "--audio-display=no",
                    "--keep-open=no",
                    f"--volume={int(volume)}",
                    f"--input-ipc-server={path}",
                    *options,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            self._cleanup()
            raise BackendError(f"mpv couldn't be started ({error.strerror or error}).") from error
        self._connect(path)
        self.alive = True
        threading.Thread(target=self._read_loop, name="cloudspin-mpv", daemon=True).start()
        for index, prop in enumerate(("time-pos", "duration", "media-title", "metadata"), start=1):
            self._send(["observe_property", index, prop])

    def _connect(self, path: str) -> None:
        deadline = time.monotonic() + 5.0
        last: OSError | None = None
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                break
            try:
                self._sock = _PipeLink(path) if sys.platform == "win32" else _SocketLink(path)
                return
            except OSError as error:
                last = error
                time.sleep(0.05)
        self.close()
        raise BackendError("mpv started but didn't open its control socket.") from last

    def _send(self, command: list) -> None:
        sock = self._sock
        if sock is None:
            return
        data = (json.dumps({"command": command}) + "\n").encode("utf-8")
        try:
            with self._lock:
                sock.send(data)
        except OSError:
            self.alive = False

    def _read_loop(self) -> None:
        sock = self._sock
        buffer = b""
        while sock is not None:
            try:
                chunk = sock.receive()
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    self._handle(json.loads(line))
                except (ValueError, TypeError):
                    continue
        self.alive = False

    def _handle(self, message: dict) -> None:
        event = message.get("event")
        if event == "property-change":
            name, value = message.get("name"), message.get("data")
            if name == "time-pos" and isinstance(value, (int, float)):
                self.position = float(value)
            elif name == "duration" and isinstance(value, (int, float)):
                self.duration = float(value)
            elif name == "media-title" and isinstance(value, str):
                self.media_title = value
            elif name == "metadata" and isinstance(value, dict):
                tags = {str(k).lower(): v for k, v in value.items()}
                artist = tags.get("artist") or tags.get("uploader") or tags.get("icy-name") or ""
                self.media_artist = str(artist)
        elif event == "start-file":
            self._ended = False
        elif event == "end-file":
            reason = message.get("reason")
            if reason == "eof":
                self._ended = True
            elif reason == "error":
                self._error = str(message.get("file_error") or "it could not be opened")

    def load(self, track: Track) -> None:
        self.position = 0.0
        self.duration = track.duration
        self.media_title = ""
        self.media_artist = ""
        self._ended = False
        self._error = ""
        self._send(["loadfile", track.path, "replace"])
        self._send(["set_property", "pause", False])

    def set_paused(self, paused: bool) -> None:
        self._send(["set_property", "pause", bool(paused)])

    def seek(self, seconds: float) -> None:
        self.position = max(0.0, seconds)
        self._send(["seek", round(max(0.0, seconds), 2), "absolute"])

    def set_volume(self, volume: int) -> None:
        self._send(["set_property", "volume", int(volume)])

    def poll(self) -> None:
        if self._process is not None and self._process.poll() is not None:
            self.alive = False

    def take_ended(self) -> bool:
        ended, self._ended = self._ended, False
        return ended

    def take_error(self) -> str:
        error, self._error = self._error, ""
        return error

    def _cleanup(self) -> None:
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = ""

    def close(self) -> None:
        self.alive = False
        if self._sock is not None:
            try:
                self._sock.send(b'{"command": ["quit"]}\n')
            except OSError:
                pass
            self._sock.close()
            self._sock = None
        if self._process is not None:
            try:
                self._process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        self._cleanup()


REPEAT_MODES = ("off", "all", "one")


class Deck:
    """A queue of tracks on top of a backend."""

    def __init__(self, backend, tracks: list[Track] | None = None, volume: int = 70, seed: int | None = None) -> None:
        self.backend = backend
        self.tracks: list[Track] = list(tracks or [])
        self.index = -1
        self.paused = True
        self.shuffle = False
        self.repeat = "off"
        self.volume = max(0, min(100, int(volume)))
        self.changes = 0  # bumps whenever a different track starts
        self.failure = ""  # why the last track could not be played, until read
        self._rng = random.Random(seed)
        self._order: list[int] = list(range(len(self.tracks)))
        self._history: list[int] = []

    # -- state ---------------------------------------------------------------

    @property
    def current(self) -> Track | None:
        return self.tracks[self.index] if 0 <= self.index < len(self.tracks) else None

    @property
    def playing(self) -> bool:
        return self.current is not None and not self.paused

    @property
    def position(self) -> float:
        return self.backend.position if self.current else 0.0

    @property
    def duration(self) -> float:
        track = self.current
        if track is None:
            return 0.0
        return self.backend.duration or track.duration

    @property
    def progress(self) -> float:
        duration = self.duration
        return max(0.0, min(1.0, self.position / duration)) if duration > 0 else 0.0

    def upcoming(self, count: int) -> list[int]:
        """Indexes of the tracks that will play next, in order."""
        if not self.tracks:
            return []
        if self.index < 0:
            return self._order[:count]
        at = self._order.index(self.index)
        rest = self._order[at + 1 :]
        if self.repeat == "all":
            rest += self._order[: at + 1]
        return rest[:count]

    def set_tracks(self, tracks: list[Track]) -> None:
        self.tracks = list(tracks)
        self.index = -1
        self.paused = True
        self._history.clear()
        self._reorder()

    def add(self, tracks: list[Track]) -> int:
        """Append tracks to the queue; returns the index of the first new one."""
        first = len(self.tracks)
        self.tracks.extend(tracks)
        self._order.extend(range(first, len(self.tracks)))
        return first

    def _reorder(self) -> None:
        order = list(range(len(self.tracks)))
        if self.shuffle:
            self._rng.shuffle(order)
            if self.index in order:
                order.remove(self.index)
                order.insert(0, self.index)
        self._order = order

    # -- transport -----------------------------------------------------------

    def play(self, index: int, remember: bool = True) -> None:
        if not 0 <= index < len(self.tracks):
            return
        if remember and self.index >= 0 and self.index != index:
            self._history.append(self.index)
            del self._history[:-200]
        self.index = index
        self.paused = False
        self.changes += 1
        self.backend.load(self.tracks[index])

    def toggle(self) -> None:
        if self.current is None:
            if self.tracks:
                self.play(self._order[0])
            return
        self.paused = not self.paused
        self.backend.set_paused(self.paused)

    def next(self, automatic: bool = False) -> None:
        if not self.tracks:
            return
        if self.index < 0:
            self.play(self._order[0])
            return
        if automatic and self.repeat == "one":
            self.play(self.index, remember=False)
            return
        at = self._order.index(self.index)
        if at + 1 < len(self._order):
            self.play(self._order[at + 1])
        elif self.repeat == "all" or not automatic:
            if self.shuffle:
                self._reorder()
                first = self._order[1] if len(self._order) > 1 else self._order[0]
                self.play(first)
            else:
                self.play(self._order[0])
        else:
            # The queue ran out: stop at the end of the last track.
            self.paused = True
            self.backend.set_paused(True)

    def previous(self) -> None:
        if self.current is None:
            return
        if self.position > 3.0 or not self._history:
            self.backend.seek(0.0)
            return
        self.play(self._history.pop(), remember=False)

    def seek_by(self, seconds: float) -> None:
        if self.current is not None:
            limit = max(0.0, self.duration - 0.5) if self.duration else float("inf")
            self.backend.seek(max(0.0, min(self.position + seconds, limit)))

    def seek_to(self, fraction: float) -> None:
        if self.current is not None and self.duration > 0:
            self.backend.seek(max(0.0, min(1.0, fraction)) * max(0.0, self.duration - 0.5))

    def set_volume(self, volume: int) -> None:
        self.volume = max(0, min(100, int(volume)))
        self.backend.set_volume(self.volume)

    def toggle_shuffle(self) -> None:
        self.shuffle = not self.shuffle
        self._reorder()

    def cycle_repeat(self) -> None:
        self.repeat = REPEAT_MODES[(REPEAT_MODES.index(self.repeat) + 1) % len(REPEAT_MODES)]

    def tick(self) -> None:
        self.backend.poll()
        error = self.backend.take_error()
        if error and self.current is not None:
            # Stay on the broken track instead of racing through the queue.
            self.failure = error
            self.paused = True
        elif self.backend.take_ended() and self.current is not None:
            self.next(automatic=True)

    def close(self) -> None:
        self.backend.close()


DEMO_TRACKS = [
    Track("demo://1", "Petrichor", "Low Tide Radio", "Weather Report for Nobody", 214, "Lo-fi"),
    Track("demo://2", "Paper Lanterns", "Marigold Arcade", "Rooftop Season", 187, "Indie pop"),
    Track("demo://3", "Static Bloom", "Kestrel & the Wires", "Small Hours", 201, "Drift phonk"),
    Track("demo://4", "Monsoon Window", "Low Tide Radio", "Weather Report for Nobody", 243, "Ambient"),
    Track("demo://5", "Blue Hour", "Nine Volt Garden", "Slow Shutter", 229, "Synthwave"),
    Track("demo://6", "Cumulus", "Kestrel & the Wires", "Small Hours", 195, "Dream pop"),
    Track("demo://7", "First Light", "Marigold Arcade", "Rooftop Season", 176, "New age"),
    Track("demo://8", "Sleigh Tracks", "Nine Volt Garden", "Slow Shutter", 258, "Holiday"),
]
