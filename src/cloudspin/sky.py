"""The living sky: a small procedural landscape that follows weather and time.

``SkyScene`` knows nothing about Textual or the network. Feed it a ``Weather``
(how cloudy, how wet, how windy) and a ``Daylight`` (where the sun or moon is),
call ``step`` to advance the animation and ``render`` to paint a ``Canvas``.
Every weather value is eased toward its target, so scenes melt into each other
instead of cutting.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .canvas import Canvas
from .color import RGB, approach, clamp, lerp, ramp, scale, smoothstep


@dataclass
class Weather:
    """What the sky should be doing. All amounts run 0..1."""

    cover: float = 0.12  # how much of the sky is cloud
    rain: float = 0.0
    snow: float = 0.0
    fog: float = 0.0
    storm: float = 0.0
    wind: float = 6.0  # km/h; clouds drift left to right


@dataclass
class Daylight:
    """Where the lights are. ``elevation`` is 1 at solar noon, 0 at the
    horizon and -1 at the middle of the night."""

    elevation: float = 0.8
    sun_x: float = 0.4  # 0..1 along the sun's arc
    moon_x: float = 0.0  # 0..1 along the moon's arc
    moon_phase: float = 0.5  # 0 new, 0.5 full


# (elevation, zenith, mid-sky, horizon)
_CLEAR_SKY: list[tuple[float, RGB, RGB, RGB]] = [
    (-0.30, (4, 6, 18), (9, 14, 36), (20, 30, 62)),
    (-0.12, (12, 14, 40), (40, 34, 80), (128, 70, 104)),
    (0.00, (32, 38, 90), (146, 84, 128), (255, 150, 92)),
    (0.14, (52, 100, 178), (136, 168, 214), (255, 208, 154)),
    (0.40, (34, 100, 198), (82, 154, 232), (166, 210, 247)),
]
_GREY_DAY: tuple[RGB, RGB, RGB] = ((88, 98, 114), (122, 132, 148), (164, 172, 184))
_GREY_NIGHT: tuple[RGB, RGB, RGB] = ((9, 10, 15), (17, 19, 27), (29, 32, 43))

# (elevation, lit side, shadow side)
_CLOUD_TONES: list[tuple[float, RGB, RGB]] = [
    (-0.30, (58, 68, 98), (20, 24, 42)),
    (0.00, (255, 212, 168), (146, 94, 132)),
    (0.30, (255, 255, 255), (170, 192, 224)),
]

_SUN_TONES: list[tuple[float, RGB]] = [
    (0.0, (255, 110, 58)),
    (0.15, (255, 190, 104)),
    (0.5, (255, 244, 216)),
]


def _triple(a: tuple[RGB, RGB, RGB], b: tuple[RGB, RGB, RGB], t: float) -> tuple[RGB, RGB, RGB]:
    return (lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t))


def clear_sky(elevation: float) -> tuple[RGB, RGB, RGB]:
    keys = _CLEAR_SKY
    if elevation <= keys[0][0]:
        return keys[0][1:]
    for lo, hi in zip(keys, keys[1:]):
        if elevation <= hi[0]:
            return _triple(lo[1:], hi[1:], (elevation - lo[0]) / (hi[0] - lo[0]))
    return keys[-1][1:]


def _cloud_tones(elevation: float) -> tuple[RGB, RGB]:
    keys = _CLOUD_TONES
    if elevation <= keys[0][0]:
        return keys[0][1], keys[0][2]
    for lo, hi in zip(keys, keys[1:]):
        if elevation <= hi[0]:
            t = (elevation - lo[0]) / (hi[0] - lo[0])
            return lerp(lo[1], hi[1], t), lerp(lo[2], hi[2], t)
    return keys[-1][1], keys[-1][2]


class _Cloud:
    __slots__ = ("w", "h", "alpha", "x", "y", "speed", "near", "opacity", "leaving")

    def __init__(self, rng: random.Random, wide: int, tall: int, near: bool) -> None:
        self.w = wide
        self.h = tall
        self.near = near
        self.x = 0.0
        self.y = 0
        self.speed = rng.uniform(0.8, 1.2) * (1.0 if near else 0.55)
        self.opacity = 0.0
        self.leaving = False
        puffs = []
        count = max(3, round(wide / max(2.5, tall * 0.55)))
        for k in range(count):
            u = (k + 0.5) / count
            crown = 1.0 - abs(2.0 * u - 1.0) ** 1.6
            radius = max(2.0, tall * (0.34 + 0.34 * crown * rng.uniform(0.75, 1.0)))
            cx = wide * u + rng.uniform(-1.0, 1.0)
            cy = tall - 1 - radius * 0.42
            puffs.append((cx, cy, radius))
        alpha = bytearray(wide * tall)
        for py in range(tall):
            for px in range(wide):
                best = 0.0
                for cx, cy, radius in puffs:
                    v = (radius - math.hypot(px - cx, (py - cy) * 1.15)) / max(1.3, radius * 0.45)
                    if v > best:
                        best = v
                # A low elliptical body keeps the puffs joined along the base.
                bx = (px - (wide - 1) / 2.0) / max(1.0, wide / 2.0 - 1.0)
                by = (py - (tall - 1)) / max(1.0, tall * 0.4)
                best = max(best, (1.0 - bx * bx - by * by) * 2.5)
                if best > 0.0:
                    alpha[py * wide + px] = int(min(1.0, best * 1.2) * 255)
        self.alpha = bytes(alpha)


class SkyScene:
    def __init__(self, seed: int = 11) -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        self.w = 0
        self.h = 0
        self.t = 0.0
        self.foreground = 0  # extra flat ground, in pixels, for overlays to sit on
        self.ground_override: RGB | None = None
        self.scrim = 0  # columns on the left to darken behind text

        self.target = Weather()
        self.now = Weather()
        self.light_target = Daylight()
        self.light = Daylight()
        self._settled = False

        self.horizon = 0
        self.ground: RGB = (10, 14, 24)
        self.natural_ground: RGB = (10, 14, 24)
        self.accents: tuple[RGB, RGB] = ((255, 202, 98), (112, 182, 255))
        self._tones: tuple[RGB, RGB, RGB] = clear_sky(0.8)
        self._tones_key: tuple | None = None
        self._bg = Canvas()

        self._far: list[float] = []
        self._mid: list[float] = []
        self._near: list[float] = []
        self._stars: list[tuple[int, int, float, float, float, RGB]] = []
        self._clouds: list[_Cloud] = []
        self._cloud_clock = 0.0
        self._drops: list[list[float]] = []
        self._splashes: list[list[float]] = []
        self._flakes: list[list[float]] = []
        self._fog: list[tuple[float, float, float, float, float]] = []
        self._flash = 0.0
        self._bolt: list[tuple[float, float]] = []
        self._bolt_ttl = 0.0
        self._strike_in = 2.5
        self._aftershock = 0.0
        self._meteor: list[float] | None = None
        self._meteor_in = 9.0
        self._scrim_tables: dict[int, bytes] = {}
        self._cloud_fade: list[int] = []

    # -- inputs --------------------------------------------------------------

    def set_weather(self, weather: Weather) -> None:
        self.target = weather

    def set_daylight(self, light: Daylight) -> None:
        self.light_target = light

    def resize(self, w: int, h: int) -> None:
        if (w, h) == (self.w, self.h):
            return
        self.w, self.h = w, h
        self._bg.resize(w, h)
        self._tones_key = None
        self._build_land()
        self._build_stars()
        self._clouds.clear()
        self._drops.clear()
        self._flakes.clear()
        self._splashes.clear()
        self._build_fog()
        self._settled = False

    def set_foreground(self, pixels: int) -> None:
        if pixels != self.foreground:
            self.foreground = pixels
            if self.w:
                self._build_land()
                self._build_stars()

    # -- construction --------------------------------------------------------

    def _build_land(self) -> None:
        w, h = self.w, self.h
        rng = random.Random(self.seed * 7 + 1)
        band = clamp(h * 0.30, 5, 18)
        base = float(self.foreground)
        p = [rng.uniform(0, math.tau) for _ in range(8)]
        span = max(40.0, w * 0.55)
        far, mid, near = [], [], []
        for x in range(w):
            ridge = 0.62 * (1.0 - abs(math.sin(x * math.pi / span + p[0]))) + 0.38 * (
                1.0 - abs(math.sin(x * math.pi / (span * 0.37) + p[1]))
            )
            far.append(base + band * (0.40 + 0.60 * ridge))
            roll = 0.5 + 0.32 * math.sin(x * math.tau / (span * 0.9) + p[2]) + 0.18 * math.sin(
                x * math.tau / (span * 0.31) + p[3]
            )
            mid.append(base + band * (0.26 + 0.26 * roll))
            low = 0.5 + 0.5 * math.sin(x * math.tau / (span * 1.3) + p[4])
            near.append(max(2.0, base + band * (0.10 + 0.08 * low)))
        self._far, self._mid, self._near = far, mid, near
        self.horizon = int(h - base - band * 0.42)

    def _build_stars(self) -> None:
        rng = random.Random(self.seed * 13 + 5)
        limit = max(1, self.horizon)
        count = int(self.w * limit / 34)
        stars = []
        for _ in range(count):
            tint = rng.choice(((255, 255, 255), (255, 255, 255), (200, 216, 255), (255, 236, 210)))
            stars.append(
                (
                    rng.randrange(max(1, self.w)),
                    rng.randrange(limit),
                    rng.uniform(0.25, 1.0) ** 1.8,
                    rng.uniform(0.6, 2.4),
                    rng.uniform(0, math.tau),
                    tint,
                )
            )
        self._stars = stars

    def _build_fog(self) -> None:
        rng = random.Random(self.seed * 3 + 9)
        bands = []
        for k in range(5):
            y = self.h * (0.30 + 0.10 * k) + rng.uniform(-1, 1)
            bands.append((y, rng.uniform(3.5, 6.5), rng.uniform(0.02, 0.06), rng.uniform(0, 9), rng.uniform(0.12, 0.4)))
        self._fog = bands

    def _spawn_cloud(self, anywhere: bool) -> _Cloud:
        rng = self.rng
        wet = max(self.now.rain, self.now.snow, self.now.storm)
        near = rng.random() < 0.55
        big = self.now.cover > 0.7
        if near:
            wide = rng.randint(18, 34 if not big else 52)
        else:
            wide = rng.randint(11, 22 if not big else 36)
        wide = max(8, min(wide, max(10, self.w // 2)))
        tall = max(4, min(int(wide * rng.uniform(0.24, 0.34)), max(4, self.horizon // 2)))
        cloud = _Cloud(rng, wide, tall, near)
        sky = max(6, self.horizon)
        top = 0.02 if wet > 0.3 else 0.08
        depth = 0.34 if wet > 0.3 else 0.56
        cloud.y = int(sky * (top + depth * rng.random() ** (1.4 if big else 1.0))) - (1 if near else 0)
        cloud.x = rng.uniform(-wide, self.w) if anywhere else float(-wide - rng.uniform(0, 12))
        return cloud

    # -- animation -----------------------------------------------------------

    def step(self, dt: float) -> None:
        if not self.w or not self.h:
            return
        self.t += dt
        now, target = self.now, self.target
        instant = not self._settled
        k = 1.0 if instant else 1.0 - math.exp(-dt / 1.5)
        now.cover += (target.cover - now.cover) * k
        now.rain += (target.rain - now.rain) * k
        now.snow += (target.snow - now.snow) * k
        now.fog += (target.fog - now.fog) * k
        now.storm += (target.storm - now.storm) * k
        now.wind += (target.wind - now.wind) * k
        light, goal = self.light, self.light_target
        kl = 1.0 if instant else 1.0 - math.exp(-dt / 1.1)
        light.elevation += (goal.elevation - light.elevation) * kl
        light.sun_x += (goal.sun_x - light.sun_x) * kl
        light.moon_x += (goal.moon_x - light.moon_x) * kl
        light.moon_phase = goal.moon_phase

        self._update_tones(instant, dt)
        self._step_clouds(dt, instant)
        self._step_rain(dt)
        self._step_snow(dt)
        self._step_storm(dt)
        self._step_meteor(dt)
        self._settled = True

    def _update_tones(self, instant: bool, dt: float) -> None:
        now, elev = self.now, self.light.elevation
        dayness = smoothstep(-0.15, 0.25, elev)
        tones = clear_sky(elev)
        grey = _triple(_GREY_NIGHT, _GREY_DAY, dayness)
        wet = max(now.rain, now.storm, now.snow * 0.8)
        overcast = max(smoothstep(0.35, 0.95, now.cover) * 0.92, min(1.0, wet * 1.4) * 0.92)
        tones = _triple(tones, grey, overcast)
        if now.snow > 0.01:
            pale = _triple(((30, 34, 46),) * 3, ((150, 160, 176), (176, 184, 198), (204, 210, 220)), dayness)
            tones = _triple(tones, pale, now.snow * 0.6)
        if now.fog > 0.01:
            flat = lerp((34, 38, 48), (172, 178, 188), dayness)
            tones = _triple(tones, (flat, flat, flat), now.fog * 0.72)
        dark = 1.0 - 0.42 * now.storm - 0.14 * now.rain * (1.0 - now.storm)
        if dark < 0.999:
            tones = (scale(tones[0], dark), scale(tones[1], dark), scale(tones[2], dark))
        self._tones = tones

        self.natural_ground = lerp((9, 11, 17), scale(lerp(tones[0], tones[2], 0.3), 0.2), 0.75)
        self.ground = self.ground_override if self.ground_override is not None else self.natural_ground

        if now.storm > 0.5:
            want = ((184, 164, 255), (255, 238, 150))
        elif now.snow > 0.3:
            want = ((206, 228, 255), (140, 190, 255))
        elif now.rain > 0.25:
            want = ((112, 172, 238), (92, 216, 222))
        elif now.fog > 0.5:
            want = ((196, 202, 214), (146, 156, 180))
        else:
            night = ((150, 172, 255), (196, 146, 255))
            gold = ((255, 156, 92), (244, 114, 172))
            day = ((255, 202, 98), (112, 182, 255))
            if elev < 0:
                t = smoothstep(-0.25, 0.0, elev)
                want = (lerp(night[0], gold[0], t), lerp(night[1], gold[1], t))
            else:
                t = smoothstep(0.0, 0.3, elev)
                want = (lerp(gold[0], day[0], t), lerp(gold[1], day[1], t))
            if now.cover > 0.7:
                t = (now.cover - 0.7) / 0.3 * 0.55
                want = (lerp(want[0], (164, 184, 214), t), lerp(want[1], (122, 152, 204), t))
        if instant:
            self.accents = want
        else:
            ka = 1.0 - math.exp(-dt / 0.9)
            self.accents = (approach(self.accents[0], want[0], ka), approach(self.accents[1], want[1], ka))

    def _step_clouds(self, dt: float, instant: bool) -> None:
        clouds = self._clouds
        want = int(round(self.now.cover * self.w / 6.5))
        if self.now.cover > 0.05:
            want = max(1, want)
        alive = [c for c in clouds if not c.leaving]
        if instant:
            clouds.clear()
            for _ in range(want):
                cloud = self._spawn_cloud(anywhere=True)
                cloud.opacity = 1.0
                clouds.append(cloud)
        else:
            self._cloud_clock -= dt
            if self._cloud_clock <= 0.0:
                self._cloud_clock = 0.12
                if len(alive) < want:
                    clouds.append(self._spawn_cloud(anywhere=True))
                elif len(alive) > want:
                    self.rng.choice(alive).leaving = True
        drift = (0.35 + abs(self.now.wind) / 11.0) * (1.0 if self.now.wind >= 0 else -1.0)
        drift = max(-6.0, min(6.0, drift))
        fade = dt / 1.6
        for cloud in clouds:
            cloud.x += drift * cloud.speed * dt
            if cloud.leaving:
                cloud.opacity -= fade
            elif cloud.opacity < 1.0:
                cloud.opacity = min(1.0, cloud.opacity + fade)
            if drift >= 0 and cloud.x > self.w + 2:
                cloud.x = float(-cloud.w - self.rng.uniform(0, 10))
            elif drift < 0 and cloud.x < -cloud.w - 2:
                cloud.x = float(self.w + self.rng.uniform(0, 10))
        if any(c.leaving and c.opacity <= 0.0 for c in clouds):
            self._clouds = [c for c in clouds if not (c.leaving and c.opacity <= 0.0)]
        # Paint far clouds first.
        self._clouds.sort(key=lambda c: c.near)

    def _ground_at(self, x: float) -> float:
        near = self._near
        if not near:
            return float(self.h)
        return self.h - near[min(len(near) - 1, max(0, int(x)))]

    def _step_rain(self, dt: float) -> None:
        rng = self.rng
        amount = max(self.now.rain, 0.0)
        want = int(self.w * (0.2 + 0.95 * amount)) if amount > 0.03 else 0
        drops = self._drops
        while len(drops) < want:
            near = rng.random() < 0.6
            drops.append(
                [
                    rng.uniform(-self.w * 0.2, self.w * 1.2),
                    rng.uniform(-self.h, self.h),
                    rng.uniform(46, 68) if near else rng.uniform(26, 38),
                    1.0 if near else 0.0,
                ]
            )
        slant = max(-0.45, min(0.45, self.now.wind / 55.0))
        keep = []
        for drop in drops:
            drop[1] += drop[2] * dt
            drop[0] += drop[2] * slant * dt
            if drop[1] >= self._ground_at(drop[0]):
                if drop[3] and 0 <= drop[0] < self.w and len(self._splashes) < 80:
                    self._splashes.append([drop[0], self._ground_at(drop[0]) - 1, 0.0])
                if len(keep) + 1 > want:
                    continue  # the rain is easing off
                drop[0] = rng.uniform(-self.w * 0.2, self.w * 1.2)
                drop[1] = rng.uniform(-6, 0)
            keep.append(drop)
        self._drops = keep
        if self._splashes:
            for splash in self._splashes:
                splash[2] += dt
            self._splashes = [s for s in self._splashes if s[2] < 0.22]

    def _step_snow(self, dt: float) -> None:
        rng = self.rng
        amount = self.now.snow
        want = int(self.w * (0.25 + 0.9 * amount)) if amount > 0.03 else 0
        flakes = self._flakes
        while len(flakes) < want:
            depth = rng.random()
            flakes.append(
                [
                    rng.uniform(0, self.w),
                    rng.uniform(-self.h, self.h),
                    3.5 + 6.0 * depth,
                    rng.uniform(0.6, 2.2),
                    rng.uniform(0.6, 1.6),
                    rng.uniform(0, math.tau),
                    depth,
                ]
            )
        push = self.now.wind / 9.0
        keep = []
        for flake in flakes:
            flake[1] += flake[2] * dt
            flake[0] = (flake[0] + push * (0.4 + flake[6]) * dt) % max(1, self.w)
            if flake[1] >= self._ground_at(flake[0]):
                if len(keep) + 1 > want:
                    continue
                flake[1] = rng.uniform(-4, 0)
                flake[0] = rng.uniform(0, self.w)
            keep.append(flake)
        self._flakes = keep

    def _step_storm(self, dt: float) -> None:
        if self._flash > 0.0:
            self._flash *= math.exp(-dt / 0.16)
            if self._flash < 0.02:
                self._flash = 0.0
        if self._bolt_ttl > 0.0:
            self._bolt_ttl -= dt
        if self._aftershock > 0.0:
            self._aftershock -= dt
            if self._aftershock <= 0.0:
                self._flash = max(self._flash, 0.7)
        if self.now.storm < 0.5:
            return
        self._strike_in -= dt
        if self._strike_in <= 0.0:
            rng = self.rng
            self._strike_in = rng.uniform(2.8, 8.0)
            self._flash = 1.0
            if rng.random() < 0.65:
                self._aftershock = rng.uniform(0.09, 0.16)
            x = rng.uniform(self.w * 0.3, self.w * 0.95)
            y = max(2.0, self.horizon * 0.22)
            path = [(x, y)]
            end = self.h - self._far[min(self.w - 1, max(0, int(x)))] + 1
            while y < end:
                y += rng.uniform(1.5, 3.2)
                x += rng.uniform(-2.4, 2.4)
                path.append((x, min(y, end)))
            self._bolt = path
            self._bolt_ttl = 0.2

    def _step_meteor(self, dt: float) -> None:
        if self._meteor is not None:
            m = self._meteor
            m[4] += dt
            m[0] += m[2] * dt
            m[1] += m[3] * dt
            if m[4] > 0.9:
                self._meteor = None
            return
        if self.light.elevation > -0.2 or self.now.cover > 0.45:
            return
        self._meteor_in -= dt
        if self._meteor_in <= 0.0:
            rng = self.rng
            self._meteor_in = rng.uniform(14, 34)
            direction = rng.choice((-1.0, 1.0))
            self._meteor = [
                rng.uniform(self.w * 0.35, self.w * 0.9),
                rng.uniform(1, max(2, self.horizon * 0.4)),
                direction * rng.uniform(34, 52),
                rng.uniform(9, 16),
                0.0,
            ]

    # -- painting ------------------------------------------------------------

    def _paint_backdrop(self) -> None:
        key = (self._tones, self.horizon)
        if key == self._tones_key:
            return
        self._tones_key = key
        top, mid, low = self._tones
        span = max(1, self.horizon)
        stops = ((0.0, top), (0.55, mid), (1.0, low))
        self._bg.fill_rows([ramp(stops, min(1.0, y / span)) for y in range(self.h)])

    def render(self, cv: Canvas) -> None:
        w, h = self.w, self.h
        if cv.w != w or cv.h != h or not w or not h:
            return
        now, light = self.now, self.light
        elev = light.elevation
        self._paint_backdrop()
        cv.blit(self._bg)
        if self._flash > 0.0:
            cv.lighten(int(self._flash * 150))

        clearness = 1.0 - min(1.0, max(now.cover * 0.9, now.fog * 0.85, now.rain, now.snow * 0.9))
        night = clamp((-elev - 0.02) / 0.22)
        if night > 0.0 and clearness > 0.05:
            self._paint_stars(cv, night * clearness)
        if self._meteor is not None:
            self._paint_meteor(cv)
        self._paint_sun_and_moon(cv, clearness)
        self._paint_clouds(cv)
        if self._bolt_ttl > 0.0:
            self._paint_bolt(cv)
        if self.scrim > 0:
            self._paint_scrim(cv)
        self._paint_land(cv)
        if self._drops:
            self._paint_rain(cv)
        if self._flakes:
            self._paint_snow(cv)
        if now.fog > 0.02:
            self._paint_fog(cv)

    def _paint_stars(self, cv: Canvas, amount: float) -> None:
        t = self.t
        add = cv.add
        for x, y, base, speed, phase, tint in self._stars:
            glint = base * (0.62 + 0.38 * math.sin(t * speed + phase)) * amount
            if glint > 0.04:
                add(x, y, tint, int(glint * 256))
                if glint > 0.72:
                    a = int((glint - 0.6) * 150)
                    add(x - 1, y, tint, a)
                    add(x + 1, y, tint, a)

    def _paint_meteor(self, cv: Canvas) -> None:
        x, y, vx, vy, age = self._meteor  # type: ignore[misc]
        fade = 1.0 - age / 0.9
        speed = math.hypot(vx, vy)
        ux, uy = vx / speed, vy / speed
        for k in range(9):
            px, py = x - ux * k, y - uy * k
            if py < self.horizon:
                cv.add(int(px), int(py), (255, 250, 236), int(256 * fade * (1.0 - k / 9.0) ** 1.5))

    def _paint_sun_and_moon(self, cv: Canvas, clearness: float) -> None:
        w, light = self.w, self.light
        elev = light.elevation
        horizon = self.horizon
        arc = max(4.0, horizon - 3.0)
        radius = clamp(self.h * 0.085, 2.2, 5.5)
        left, right = w * 0.44, w * 0.93
        veil = 0.18 + 0.82 * clearness
        if elev > -0.14:
            sx = left + (right - left) * clamp(light.sun_x)
            sy = horizon - max(elev, -0.14) * arc
            tone = ramp(_SUN_TONES, max(0.0, elev))
            warm = 1.0 - smoothstep(0.0, 0.35, elev)
            cv.glow(sx, sy, radius * (4.2 + 3.2 * warm), tone, (0.30 + 0.25 * warm) * veil)
            cv.glow(sx, sy, radius * 2.1, tone, 0.55 * veil)
            cv.disc(sx, sy, radius, lerp(tone, (255, 255, 244), 0.45), int(256 * veil))
        if elev < 0.1:
            mx = left + (right - left) * clamp(light.moon_x)
            my = horizon - max(-elev, -0.1) * arc
            show = smoothstep(0.1, -0.12, elev) * veil
            if show > 0.02:
                self._paint_moon(cv, mx, my, radius * 0.92, light.moon_phase, show)

    def _paint_moon(self, cv: Canvas, cx: float, cy: float, radius: float, phase: float, show: float) -> None:
        k = math.cos(math.tau * phase)
        waxing = phase < 0.5
        lit_amount = (1.0 - k) / 2.0
        cv.glow(cx, cy, radius * 4.5, (120, 142, 210), 0.34 * show * (0.25 + 0.75 * lit_amount))
        bright = (232, 236, 246)
        maria = (192, 198, 214)
        dark = lerp(self._tones[1], (70, 80, 110), 0.35)
        y0, y1 = int(cy - radius - 1), int(cy + radius + 2)
        x0, x1 = int(cx - radius - 1), int(cx + radius + 2)
        craters = ((-0.30, -0.25, 0.26), (0.28, 0.22, 0.2), (-0.05, 0.42, 0.16))
        for y in range(y0, y1 + 1):
            ny = (y - cy) / radius
            for x in range(x0, x1 + 1):
                nx = (x - cx) / radius
                dist = math.hypot(nx, ny)
                cover = (1.0 - dist) * radius + 0.5
                if cover <= 0.0:
                    continue
                cover = min(1.0, cover)
                edge = math.sqrt(max(0.0, 1.0 - ny * ny))
                side = (nx - k * edge) if waxing else (-k * edge - nx)
                lit = clamp(side * radius + 0.5)
                tone = bright
                for ox, oy, size in craters:
                    if math.hypot(nx - ox, ny - oy) < size:
                        tone = maria
                        break
                cv.blend(x, y, lerp(dark, tone, lit), int(256 * cover * show * (0.3 + 0.7 * lit)))

    def _paint_clouds(self, cv: Canvas) -> None:
        if not self._clouds:
            return
        now = self.now
        elev = self.light.elevation
        dayness = smoothstep(-0.15, 0.25, elev)
        lit, shade = _cloud_tones(elev)
        heavy = max(smoothstep(0.55, 1.0, now.cover), min(1.0, now.rain * 1.5), now.snow)
        grey_lit = lerp((46, 50, 62), (196, 202, 212), dayness)
        grey_shade = lerp((18, 20, 28), (112, 120, 134), dayness)
        lit = lerp(lit, grey_lit, heavy * 0.8)
        shade = lerp(shade, grey_shade, heavy * 0.85)
        if now.storm > 0.01:
            lit = lerp(lit, lerp((36, 38, 48), (104, 108, 122), dayness), now.storm * 0.9)
            shade = lerp(shade, lerp((12, 13, 18), (42, 44, 56), dayness), now.storm * 0.9)
        if self._flash > 0.0:
            lit = lerp(lit, (236, 232, 255), self._flash * 0.8)
            shade = lerp(shade, (150, 150, 190), self._flash * 0.6)
        haze = self._tones[1]
        far_lit, far_shade = lerp(lit, haze, 0.38), lerp(shade, haze, 0.38)

        W, H = cv.w, cv.h
        R, G, B = cv.r, cv.g, cv.b
        fade = self._cloud_fade
        if len(fade) != W or (fade and (fade[0] < 256) != (self.scrim > 0)):
            # Clouds thin out as they drift behind the weather text.
            span = min(self.scrim, W)
            fade = self._cloud_fade = [
                int(256 * (0.34 + 0.66 * smoothstep(0.45, 1.0, x / span))) if x < span else 256 for x in range(W)
            ]
        for cloud in self._clouds:
            if cloud.opacity <= 0.0:
                continue
            top_tone, low_tone = (lit, shade) if cloud.near else (far_lit, far_shade)
            strength = int(cloud.opacity * (256 if cloud.near else 205))
            ox = math.floor(cloud.x)
            f1 = int((cloud.x - ox) * 256)
            f0 = 256 - f1
            sw, sh, alpha = cloud.w, cloud.h, cloud.alpha
            x_lo = max(0, ox)
            x_hi = min(W - 1, ox + sw)
            if x_lo > x_hi:
                continue
            for sy in range(sh):
                y = cloud.y + sy
                if y < 0 or y >= H:
                    continue
                t = sy / (sh - 1) if sh > 1 else 0.0
                cr, cg, cb = lerp(top_tone, low_tone, t * t)
                base = sy * sw - ox
                row = y * W
                for x in range(x_lo, x_hi + 1):
                    sx = x - ox
                    a = (alpha[base + x] * f0 if sx < sw else 0) + (alpha[base + x - 1] * f1 if sx > 0 else 0)
                    a = (a * strength >> 16) * fade[x] >> 8
                    if a < 3:
                        continue
                    i = row + x
                    v = R[i]
                    R[i] = v + ((cr - v) * a >> 8)
                    v = G[i]
                    G[i] = v + ((cg - v) * a >> 8)
                    v = B[i]
                    B[i] = v + ((cb - v) * a >> 8)

    def _paint_bolt(self, cv: Canvas) -> None:
        strength = clamp(self._bolt_ttl / 0.2)
        core = (255, 252, 240)
        halo = (170, 150, 255)
        for (x0, y0), (x1, y1) in zip(self._bolt, self._bolt[1:]):
            steps = max(1, int(abs(y1 - y0) + abs(x1 - x0)))
            for s in range(steps + 1):
                t = s / steps
                x, y = int(x0 + (x1 - x0) * t), int(y0 + (y1 - y0) * t)
                cv.blend(x, y, core, int(256 * strength))
                cv.add(x - 1, y, halo, int(120 * strength))
                cv.add(x + 1, y, halo, int(120 * strength))

    def _paint_scrim(self, cv: Canvas) -> None:
        """Shade the left of the sky, where the weather is written, fading out to the right."""
        width = min(self.scrim, self.w)
        far, h = self._far, self.h
        tables = self._scrim_tables
        for x in range(width):
            level = int(24 * (1.0 - smoothstep(0.55, 1.0, x / width)))
            if level <= 0:
                continue
            table = tables.get(level)
            if table is None:
                keep = 1.0 - level / 24 * 0.4
                table = tables[level] = bytes(int(v * keep) for v in range(256))
            cv.remap_column(x, int(h - far[x]) + 1, table)

    def _paint_land(self, cv: Canvas) -> None:
        h = self.h
        low = self._tones[2]
        ground = self.ground
        dayness = smoothstep(-0.15, 0.25, self.light.elevation)
        far_tone = lerp(low, ground, 0.52)
        mid_tone = lerp(low, ground, 0.80)
        if self.now.snow > 0.02:
            frost = scale((204, 214, 230), 0.22 + 0.78 * dayness)
            far_tone = lerp(far_tone, frost, self.now.snow * 0.42)
            mid_tone = lerp(mid_tone, frost, self.now.snow * 0.24)
        if self._flash > 0.0:
            far_tone = lerp(far_tone, (150, 150, 180), self._flash * 0.25)
        for heights, tone in ((self._far, far_tone), (self._mid, mid_tone), (self._near, ground)):
            for x, height in enumerate(heights):
                top = h - height
                y = int(math.ceil(top))
                edge = y - top
                if edge > 0.02:
                    cv.blend(x, y - 1, tone, int(256 * edge))
                cv.fill_column(x, y, tone)

    def _paint_rain(self, cv: Canvas) -> None:
        dayness = smoothstep(-0.15, 0.25, self.light.elevation)
        tone = lerp((120, 140, 190), (214, 228, 250), dayness)
        slant = max(-0.45, min(0.45, self.now.wind / 55.0))
        blend = cv.blend
        for x, y, speed, near in self._drops:
            if near:
                blend(int(x), int(y), tone, 190)
                blend(int(x - slant), int(y) - 1, tone, 120)
                blend(int(x - slant * 2), int(y) - 2, tone, 60)
            else:
                blend(int(x), int(y), tone, 84)
                blend(int(x - slant), int(y) - 1, tone, 40)
        for x, y, age in self._splashes:
            a = int(170 * (1.0 - age / 0.22))
            if age < 0.09:
                blend(int(x), int(y), tone, a)
            else:
                blend(int(x) - 1, int(y), tone, a)
                blend(int(x) + 1, int(y), tone, a)

    def _paint_snow(self, cv: Canvas) -> None:
        t = self.t
        dayness = smoothstep(-0.15, 0.25, self.light.elevation)
        tone = lerp((170, 180, 210), (255, 255, 255), dayness)
        blend = cv.blend
        for x, y, _speed, sway, rate, phase, depth in self._flakes:
            px = int(x + sway * math.sin(t * rate + phase))
            blend(px, int(y), tone, int(90 + 150 * depth))
            if depth > 0.82:
                blend(px + 1, int(y), tone, 70)

    def _paint_fog(self, cv: Canvas) -> None:
        amount = self.now.fog
        dayness = smoothstep(-0.15, 0.25, self.light.elevation)
        tone = lerp((62, 68, 84), (206, 212, 222), dayness)
        cr, cg, cb = tone
        W, H = cv.w, cv.h
        R, G, B = cv.r, cv.g, cv.b
        t = self.t
        floor = H - int(max(self._near)) - 1 if self._near else H - 1
        # A veil that thickens toward the ground, so the hills sink into it.
        start = int(self.horizon * 0.35)
        depth = max(1, floor - start)
        for y in range(max(0, start), floor + 1):
            a = int(amount * 150 * ((y - start) / depth) ** 0.8)
            if a < 4:
                continue
            keep = 256 - a
            lut_r = bytes((v * keep + cr * a) >> 8 for v in range(256))
            lut_g = bytes((v * keep + cg * a) >> 8 for v in range(256))
            lut_b = bytes((v * keep + cb * a) >> 8 for v in range(256))
            i = y * W
            R[i : i + W] = R[i : i + W].translate(lut_r)
            G[i : i + W] = G[i : i + W].translate(lut_g)
            B[i : i + W] = B[i : i + W].translate(lut_b)
        # Slow drifting banks on top of the veil.
        for center, thick, freq, phase, speed in self._fog:
            y0 = max(0, int(center - thick))
            y1 = min(floor, int(center + thick))
            wave = [0.5 + 0.5 * math.sin(x * freq + phase + t * speed) for x in range(W)]
            for y in range(y0, y1 + 1):
                fall = 1.0 - abs(y - center) / thick
                if fall <= 0.0:
                    continue
                peak = amount * fall * fall * 96.0
                row = y * W
                for x in range(W):
                    a = int(peak * wave[x])
                    if a < 4:
                        continue
                    i = row + x
                    v = R[i]
                    R[i] = v + ((cr - v) * a >> 8)
                    v = G[i]
                    G[i] = v + ((cg - v) * a >> 8)
                    v = B[i]
                    B[i] = v + ((cb - v) * a >> 8)
