"""Tiny synthetic 'songs' for testing the listener without real music."""

from __future__ import annotations

import math
import random
import wave
from array import array
from pathlib import Path

RATE = 8000


def heavy(seconds: float = 12.0, bpm: float = 140.0) -> array:
    """Loud and bass-heavy: a pounding kick, a clipped sub bass and hats."""
    rng = random.Random(3)
    beat = 60.0 / bpm
    out = array("h")
    for n in range(int(seconds * RATE)):
        t = n / RATE
        since = t % beat
        kick = math.sin(math.tau * (48 + 70 * math.exp(-since * 30)) * since) * math.exp(-since * 9)
        sub = max(-0.6, min(0.6, 1.6 * math.sin(math.tau * 52 * t)))
        off = (t + beat / 2) % beat
        hat = (rng.random() * 2 - 1) * math.exp(-off * 60) * 0.35
        out.append(int(max(-1.0, min(1.0, 0.75 * kick + 0.55 * sub + hat)) * 32000))
    return out


def gentle(seconds: float = 12.0) -> array:
    """Quiet and slow: a soft pad that swells."""
    out = array("h")
    for n in range(int(seconds * RATE)):
        t = n / RATE
        swell = 0.6 + 0.4 * math.sin(math.tau * 0.12 * t)
        tone = math.sin(math.tau * 220 * t) + 0.5 * math.sin(math.tau * 330 * t)
        out.append(int(0.035 * swell * tone * 32000))
    return out


def bright(seconds: float = 12.0, bpm: float = 124.0) -> array:
    """Loud, busy and light on bass: plucked high notes."""
    rng = random.Random(5)
    step = 60.0 / bpm / 2
    notes = (660, 880, 990, 1320)
    out = array("h")
    for n in range(int(seconds * RATE)):
        t = n / RATE
        since = t % step
        note = notes[int(t / step) % len(notes)]
        pluck = math.sin(math.tau * note * since) * math.exp(-since * 14)
        fizz = (rng.random() * 2 - 1) * math.exp(-since * 50) * 0.3
        out.append(int(max(-1.0, min(1.0, 0.8 * pluck + fizz)) * 30000))
    return out


def write_wav(path: Path, samples: array) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(samples.tobytes())
    return path
