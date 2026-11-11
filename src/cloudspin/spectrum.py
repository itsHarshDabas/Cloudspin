"""The bars beside the record.

This is a generative spectrum, not an FFT of the audio: it is driven by the
play state, the volume and a tempo picked per song, so it moves like music
without needing access to the sound card.
"""

from __future__ import annotations

import math
import random


class Spectrum:
    def __init__(self) -> None:
        self.levels: list[float] = []
        self.peaks: list[float] = []
        self.energy = 0.0
        self._t = 0.0
        self._tempo = 110.0
        self._phases: list[tuple[float, float, float]] = []
        self._rng = random.Random(1)
        self._noise: list[float] = []

    def resize(self, bars: int) -> None:
        bars = max(0, bars)
        if bars == len(self.levels):
            return
        self.levels = [0.0] * bars
        self.peaks = [0.0] * bars
        self._noise = [0.0] * bars
        self._seed_phases()

    def retune(self, seed: str, tempo: float | None = None) -> None:
        """Give a song its own motion."""
        self._rng = random.Random(seed)
        self._tempo = tempo if tempo and 50 <= tempo <= 220 else self._rng.uniform(84, 132)
        self._seed_phases()

    def _seed_phases(self) -> None:
        rng = self._rng
        self._phases = [
            (rng.uniform(0, math.tau), rng.uniform(0.7, 2.6), rng.uniform(0, math.tau)) for _ in self.levels
        ]

    def step(self, dt: float, playing: bool, volume: float) -> None:
        bars = len(self.levels)
        if not bars:
            self.energy *= 0.8
            return
        self._t += dt
        t = self._t
        beat = (t * self._tempo / 60.0) % 1.0
        kick = math.exp(-5.0 * beat)
        offbeat = math.exp(-7.0 * ((beat + 0.5) % 1.0))
        gain = (0.35 + 0.65 * min(1.0, volume)) if playing else 0.0
        rng = self._rng
        total = 0.0
        for i in range(bars):
            u = i / max(1, bars - 1)
            a, rate, b = self._phases[i]
            self._noise[i] += (rng.random() - self._noise[i]) * min(1.0, dt * 9.0)
            shape = 0.92 - 0.55 * u**0.8
            body = 0.5 + 0.5 * math.sin(t * rate + a) * math.sin(t * 0.37 * rate + b)
            low = kick * max(0.0, 1.0 - u * 3.2)
            mid = offbeat * max(0.0, 1.0 - abs(u - 0.45) * 3.0) * 0.6
            target = gain * min(1.0, shape * (0.26 + 0.62 * body + 0.34 * self._noise[i]) + 0.6 * low + mid * 0.55)
            level = self.levels[i]
            if target > level:
                level += (target - level) * min(1.0, dt * 22.0)
            else:
                level += (target - level) * min(1.0, dt * 6.5)
            self.levels[i] = level
            peak = self.peaks[i] - dt * 0.38
            self.peaks[i] = level if level > peak else max(0.0, peak)
            if u < 0.3:
                total += level
        low_bars = max(1, int(bars * 0.3) + 1)
        self.energy += (min(1.0, total / low_bars * 1.3) - self.energy) * min(1.0, dt * 14.0)

    @property
    def still(self) -> bool:
        return self.energy < 0.004 and all(p < 0.004 for p in self.peaks)
