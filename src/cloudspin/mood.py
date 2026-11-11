"""Which sky suits a song.

In order of trust:

1. your own ``[moods]`` rules from the config file,
2. how the song sounds: 30 seconds are decoded (by mpv, or ffmpeg for files)
   and measured for rhythm, brightness, bass weight and loudness,
3. words in the genre tag, title and album ("phonk", "lofi", "rain", ...),
   which also give an instant first guess while the listening happens.

If none of them says anything, the sky goes back to showing the real weather.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
import tempfile
from array import array
from dataclasses import dataclass
from pathlib import Path

from .sky import Daylight, Weather


@dataclass(frozen=True)
class Scene:
    key: str
    label: str
    weather: Weather
    daylight: Daylight


SCENES: dict[str, Scene] = {
    scene.key: scene
    for scene in (
        Scene("storm", "Thunderstorm", Weather(1.0, rain=0.9, storm=1.0, wind=24), Daylight(-0.85, 1.0, 0.4)),
        Scene("rain", "Rain", Weather(0.95, rain=0.7, wind=14), Daylight(0.2, 0.8, 0.0)),
        Scene("evening", "Evening", Weather(0.3, wind=5), Daylight(0.09, 0.9, 0.0)),
        Scene("night", "Starry night", Weather(0.08, wind=3), Daylight(-0.9, 1.0, 0.38)),
        Scene("day", "Sunny day", Weather(0.22, wind=9), Daylight(0.95, 0.42, 1.0)),
        Scene("dawn", "Dawn", Weather(0.3, wind=4), Daylight(0.07, 0.08, 1.0)),
        Scene("snow", "Snow", Weather(0.92, snow=0.7, wind=5), Daylight(0.3, 0.75, 0.0)),
        Scene("fog", "Fog", Weather(0.5, fog=1.0, wind=2), Daylight(0.25, 0.2, 1.0)),
        Scene("overcast", "Overcast", Weather(0.96, wind=12), Daylight(0.6, 0.5, 1.0)),
    )
}


@dataclass(frozen=True)
class Mood:
    scene: str  # a key of SCENES
    why: str  # short, for the caption on the sky


# A trailing * matches any ending ("melanchol*" matches "melancholy").
_GENRES: list[tuple[str, tuple[str, ...]]] = [
    ("storm", ("phonk", "hoodtrap", "hood trap", "trap metal", "rage", "bass boosted", "metal", "metalcore", "deathcore", "hardstyle", "hardcore", "drill", "dubstep",
               "drum and bass", "drum & bass", "dnb", "industrial", "rage", "grindcore", "punk",
               "breakcore", "gabber", "riddim", "hard rock", "thrash", "screamo")),
    ("rain", ("blues", "emo", "sad", "melanchol*", "grunge", "slowcore", "doom")),
    ("snow", ("christmas", "xmas", "holiday", "carol*", "winter")),
    ("fog", ("shoegaze", "dream pop", "dreampop", "post-rock", "post rock", "darkwave", "drone",
             "ethereal", "witch house")),
    ("night", ("synthwave", "retrowave", "vaporwave", "r&b", "rnb", "soul", "trip hop", "trip-hop",
               "downtempo", "deep house", "techno", "trap", "nocturne", "chillwave", "future garage")),
    ("evening", ("lofi", "lo-fi", "lo fi", "chill", "chillout", "chillhop", "ambient", "acoustic", "piano",
                 "classical", "jazz", "bossa nova", "ballad", "singer-songwriter", "folk", "meditat*",
                 "easy listening", "soft rock", "instrumental", "soundtrack", "slowed")),
    ("dawn", ("gospel", "worship", "new age", "devotional", "bhajan")),
    ("day", ("pop", "dance", "edm", "house", "disco", "funk", "reggae", "latin", "afrobeat*", "k-pop",
             "j-pop", "ska", "country", "indie", "rock", "hip hop", "hip-hop", "rap", "bollywood",
             "punjabi", "electronic")),
]  # fmt: skip

# Only words that plainly name a sky are trusted in titles and album names.
_TITLES: list[tuple[str, tuple[str, ...]]] = [
    ("storm", ("phonk", "hoodtrap", "hood trap", "bass boosted", "thunder*", "storm*", "lightning", "hurricane", "tempest")),
    ("rain", ("rain*", "monsoon", "petrichor", "drizzle", "downpour", "tears", "umbrella")),
    ("snow", ("snow*", "winter", "christmas", "xmas", "frost*", "blizzard", "icicle*")),
    ("fog", ("fog*", "mist*", "haze", "hazy", "smoke")),
    ("night", ("night*", "midnight", "moon*", "stars", "starlight", "nocturne", "insomnia", "3am", "2am")),
    ("evening", ("sunset*", "evening*", "dusk", "twilight", "golden hour", "lofi", "lo-fi", "slowed")),
    ("dawn", ("sunrise", "morning*", "dawn", "daybreak")),
    ("day", ("summer*", "sunshine", "sunny", "daylight", "sunlight", "sunflower*")),
]  # fmt: skip


def _pattern(word: str) -> re.Pattern[str]:
    stem = word.rstrip("*")
    tail = r"[a-z0-9]*" if word.endswith("*") else ""
    return re.compile(r"(?<![a-z0-9])" + re.escape(stem) + tail + r"(?![a-z0-9])")


_GENRE_RULES = [(scene, word, _pattern(word)) for scene, words in _GENRES for word in words]
_TITLE_RULES = [(scene, word, _pattern(word)) for scene, words in _TITLES for word in words]


def from_rules(
    title: str, artist: str = "", album: str = "", genre: str = "", rules: dict[str, str] | None = None
) -> Mood | None:
    """Your own ``[moods]`` rules. These always win, even over what the song sounds like."""
    everything = f"{genre} {title} {artist} {album}".lower()
    for word, scene in (rules or {}).items():
        if scene in SCENES and word.strip() and _pattern(word.strip().lower()).search(everything):
            return Mood(scene, f"your rule for “{word.strip()}”")
    return None


def from_words(
    title: str, artist: str = "", album: str = "", genre: str = "", rules: dict[str, str] | None = None
) -> Mood | None:
    """Guess a sky from what the song is called and how it is tagged."""
    genre_text = genre.lower()
    name_text = f"{title} {album}".lower()
    ruled = from_rules(title, artist, album, genre, rules)
    if ruled is not None:
        return ruled
    if genre_text.strip():
        # Rules run in order, so "dream pop" is caught as fog before "pop" can claim it.
        for scene, word, pattern in _GENRE_RULES:
            if pattern.search(genre_text):
                return Mood(scene, f"tagged {genre.strip()}")
    for scene, word, pattern in _TITLE_RULES:
        found = pattern.search(name_text)
        if found:
            return Mood(scene, f"“{found.group(0)}” in the name")
    return None


# -- listening ---------------------------------------------------------------


@dataclass(frozen=True)
class Features:
    loudness: float  # RMS level in dBFS, about -35 (quiet) to -8 (crushed)
    tempo: float  # beats per minute, a rough estimate
    onsets: float  # sharp attacks per second
    bass: float  # share of energy below ~150 Hz, 0..1
    brightness: float  # zero crossings per sample, 0..0.5

    @property
    def intensity(self) -> float:
        """How hard the song hits, 0..1. Rhythm counts for more than volume,
        because nearly everything is mastered loud."""
        loud = min(1.0, max(0.0, (self.loudness + 28.0) / 18.0))
        busy = min(1.0, self.onsets / 6.0)
        bright = min(1.0, self.brightness / 0.10)
        fast = min(1.0, max(0.0, (self.tempo - 70.0) / 90.0))
        return 0.25 * loud + 0.35 * busy + 0.2 * bright + 0.2 * fast


def measure(samples, rate: int = 8000) -> Features | None:
    """Measure mono 16-bit samples. Needs at least a few seconds of sound."""
    frame = max(1, rate // 100)
    frames = len(samples) // frame
    if frames < 300:
        return None
    k = 1.0 - math.exp(-math.tau * 150.0 / rate)
    low = 0.0
    low_energy = 0.0
    total = 0.0
    crossings = 0
    positive = True
    energies: list[float] = []
    for f in range(frames):
        acc = 0.0
        for x in samples[f * frame : (f + 1) * frame]:
            acc += x * x
            low += k * (x - low)
            low_energy += low * low
            if (x >= 0) != positive:
                positive = not positive
                crossings += 1
        energies.append(acc / frame)
        total += acc
    count = frames * frame
    mean_square = total / count
    if mean_square < 1.0:
        return None  # silence
    loudness = 10.0 * math.log10(mean_square / (32768.0 * 32768.0))

    # Attacks: sudden rises in level, measured on lightly smoothed frames so a
    # steady tone's ripple doesn't count as a drum hit.
    smooth = [(a + b + c) / 3.0 for a, b, c in zip(energies, energies[1:], energies[2:])]
    logs = [math.log(e + 1.0) for e in smooth]
    flux = [0.0] + [max(0.0, b - a) for a, b in zip(logs, logs[1:])]
    mean_flux = sum(flux) / len(flux)
    spread = math.sqrt(sum((v - mean_flux) ** 2 for v in flux) / len(flux))
    gate = max(0.3, mean_flux + 1.2 * spread)
    peaks = sum(
        1 for i in range(1, len(flux) - 1) if flux[i] > gate and flux[i] >= flux[i - 1] and flux[i] > flux[i + 1]
    )
    onsets = peaks / (len(flux) / 100.0)

    # Tempo: the shortest lag at which the attack pattern lines up with itself.
    tempo = 0.0
    if onsets >= 0.4:
        centred = [v - mean_flux for v in flux]
        scores = {
            lag: sum(a * b for a, b in zip(centred, centred[lag:])) / (len(centred) - lag)
            for lag in range(30, 101)  # 200 down to 60 beats per minute
        }
        best = max(scores.values())
        if best > 0.0:
            lag = next(lag for lag in sorted(scores) if scores[lag] >= 0.85 * best)
            tempo = 6000.0 / lag

    return Features(
        loudness=loudness,
        tempo=tempo,
        onsets=onsets,
        bass=min(1.0, low_energy / total) if total else 0.0,
        brightness=crossings / count,
    )


SAMPLE_RATE = 8000
_ENOUGH = SAMPLE_RATE * 2 * 8  # eight seconds is the least worth measuring


def _decode_with_mpv(mpv: str, path: str, ytdlp: str | None, start: int, timeout: float) -> bytes:
    """Have mpv write 30 seconds of the song as raw samples. Works for links too."""
    with tempfile.TemporaryDirectory(prefix="cloudspin-listen-") as folder:
        clip = Path(folder) / "clip.raw"
        command = [
            mpv, "--no-config", "--no-terminal", "--no-video", "--ytdl-format=bestaudio/best",
            f"--start={start}", "--length=30", "--audio-channels=mono", f"--audio-samplerate={SAMPLE_RATE}",
            "--oac=pcm_s16le", "--of=s16le", f"--o={clip}",
        ]  # fmt: skip
        if ytdlp:
            command.append(f"--script-opts=ytdl_hook-ytdl_path={ytdlp}")
        try:
            subprocess.run(
                [*command, "--", path],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout,
            )  # fmt: skip
            return clip.read_bytes()
        except (OSError, subprocess.SubprocessError):
            return b""


def _decode_with_ffmpeg(ffmpeg: str, path: str, start: int) -> bytes:
    try:
        done = subprocess.run(
            [ffmpeg, "-v", "error", "-nostdin", "-ss", str(start), "-t", "30", "-i", path,
             "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=25,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return b""
    return done.stdout


def listen(
    path: str,
    mpv: str | None = None,
    ytdlp: str | None = None,
    ffmpeg: str | None | bool = None,
) -> Features | None:
    """Decode half a minute from inside a song and measure it.

    mpv does the decoding when it is given, which covers links as well as
    files. ffmpeg is the fallback for files; pass ``ffmpeg=False`` to rule it out.
    """
    stream = "://" in path
    if ffmpeg is None:
        ffmpeg = shutil.which("ffmpeg")
    raw = b""
    for start in (40, 0):
        if mpv:
            raw = _decode_with_mpv(mpv, path, ytdlp, start, 75 if stream else 30)
        if len(raw) < _ENOUGH and ffmpeg and not stream:
            raw = _decode_with_ffmpeg(str(ffmpeg), path, start)
        if len(raw) >= _ENOUGH:
            break
    if len(raw) < _ENOUGH:
        return None
    samples = array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return measure(samples, SAMPLE_RATE)


def from_features(features: Features) -> Mood:
    """Turn measurements into a sky. Busy, bright and bass-heavy storms; slow and soft settles."""
    intensity = features.intensity
    if intensity >= 0.62:
        if features.bass >= 0.40:
            return Mood("storm", "hard-hitting and heavy on bass")
        return Mood("day", "bright and energetic")
    if intensity >= 0.45:
        if features.brightness < 0.06 and features.bass >= 0.35:
            return Mood("rain", "dark and steady")
        return Mood("day", "upbeat")
    if intensity >= 0.27:
        return Mood("evening", "calm")
    return Mood("night", "quiet and slow")
