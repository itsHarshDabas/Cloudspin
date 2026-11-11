"""The record: grooves that catch the light, a label that turns, a tonearm that tracks."""

from __future__ import annotations

import math

from .canvas import Canvas
from .color import RGB, lerp
from .songlook import THUMB

RPM = 33.333
_LIGHT = math.radians(-38.0)  # where the sheen sits on the grooves


class Vinyl:
    def __init__(self) -> None:
        self.w = 0
        self.h = 0
        self.angle = 0.0
        self.speed = 0.0  # 0 stopped .. 1 full speed
        self.reach = 1.16  # tonearm head, in disc radii from the centre
        self.cx = 0.0
        self.cy = 0.0
        self.radius = 0.0
        self._groove: list[tuple[int, int, int, float, float, int]] = []
        self._label: list[tuple[int, float, float, float, int]] = []
        self._halo: list[tuple[int, int]] = []
        self._pivot = (0.0, 0.0)
        self._arm = 0.0
        self._base = 0.0

    @staticmethod
    def width_for(pixel_height: int) -> int:
        """How many columns a record of this height needs, tonearm included."""
        radius = (pixel_height - 2) / 2.0
        return int(radius * 2.36) + 6

    def resize(self, w: int, h: int) -> None:
        if (w, h) == (self.w, self.h):
            return
        self.w, self.h = w, h
        radius = max(3.0, min((h - 2) / 2.0, (w - 6) / 2.36))
        self.radius = radius
        self.cx = radius + 2.0
        self.cy = (h - 1) / 2.0
        cx, cy = self.cx, self.cy
        groove, label, halo = [], [], []
        label_edge = 0.36
        gaps = (0.53, 0.69, 0.84)
        for y in range(h):
            for x in range(w):
                dx, dy = x - cx, y - cy
                dist = math.hypot(dx, dy)
                d = dist / radius
                i = y * w + x
                if d > 1.0 + 0.5 / radius:
                    fall = 1.0 - (d - 1.0) / 0.55
                    if fall > 0.0:
                        halo.append((i, int(fall * fall * 256)))
                    continue
                theta = math.atan2(dy, dx)
                if d <= label_edge + 0.5 / radius:
                    cover = min(1.0, (label_edge - d) * radius + 0.5)
                    label.append((i, dx / (label_edge * radius), dy / (label_edge * radius), d, int(cover * 256)))
                    if cover >= 1.0:
                        continue
                edge = min(1.0, (1.0 - d) * radius + 0.5)
                base = 15.0 + 6.0 * math.sin(dist * 2.3)
                for gap in gaps:
                    if abs(d - gap) < 0.55 / radius:
                        base -= 7.0
                if d > 0.955:
                    base += 16.0
                if d < label_edge + 1.2 / radius:
                    base -= 5.0
                wedge = max(0.0, math.cos(2.0 * (theta - _LIGHT))) ** 14
                sheen = wedge * (0.62 + 0.38 * math.sin(math.pi * min(1.0, max(0.0, (d - 0.36) / 0.64))))
                phase = 3.0 * theta + d * 9.0
                groove.append((i, int(base), int(sheen * 256), math.sin(phase), math.cos(phase), int(edge * 256)))
        self._groove, self._label, self._halo = groove, label, halo
        self._pivot = (cx + radius * 1.0, cy - radius * 0.84)
        self._arm = radius * 1.25
        self._base = math.hypot(self._pivot[0] - cx, self._pivot[1] - cy)

    def step(self, dt: float, spinning: bool, loaded: bool, progress: float) -> None:
        goal = 1.0 if spinning else 0.0
        rate = 0.45 if spinning else 0.9
        self.speed += (goal - self.speed) * (1.0 - math.exp(-dt / rate))
        if abs(goal - self.speed) < 0.004:
            self.speed = goal
        self.angle = (self.angle + self.speed * (RPM / 60.0) * math.tau * dt) % math.tau
        reach = 0.93 - 0.50 * max(0.0, min(1.0, progress)) if loaded else 1.16
        self.reach += (reach - self.reach) * (1.0 - math.exp(-dt / 0.3))

    @property
    def moving(self) -> bool:
        return self.speed > 0.0

    def render(
        self, cv: Canvas, bg: RGB, label: RGB, label2: RGB, glow: RGB, pulse: float, art: bytes | None = None
    ) -> None:
        if cv.w != self.w or cv.h != self.h or not self._groove:
            return
        cv.fill(bg)
        R, G, B = cv.r, cv.g, cv.b
        br, bg_, bb = bg

        strength = 0.07 + 0.2 * pulse
        hr, hg, hb = int(glow[0] * strength), int(glow[1] * strength), int(glow[2] * strength)
        for i, fall in self._halo:
            R[i] = min(255, br + (hr * fall >> 8))
            G[i] = min(255, bg_ + (hg * fall >> 8))
            B[i] = min(255, bb + (hb * fall >> 8))

        turn = 3.0 * self.angle
        cos_t, sin_t = math.cos(turn), math.sin(turn)
        tr, tg, tb = glow[0] * 0.30, glow[1] * 0.30, glow[2] * 0.30
        for i, base, sheen, s3, c3, edge in self._groove:
            shimmer = s3 * cos_t - c3 * sin_t
            v = base + shimmer * (2.5 + sheen * 0.085) + sheen * 0.26
            r = v + (tr * sheen) / 256.0
            g = v + (tg * sheen) / 256.0
            b = v + 2.0 + (tb * sheen) / 256.0
            if edge < 256:
                r = br + (r - br) * edge / 256.0
                g = bg_ + (g - bg_) * edge / 256.0
                b = bb + (b - bb) * edge / 256.0
            R[i] = 0 if r < 0 else 255 if r > 255 else int(r)
            G[i] = 0 if g < 0 else 255 if g > 255 else int(g)
            B[i] = 0 if b < 0 else 255 if b > 255 else int(b)

        ca, sa = math.cos(self.angle), math.sin(self.angle)
        hole = max(0.05, 1.0 / self.radius)
        ring = hole * 2.0
        dark = (10, 10, 13)
        metal = (176, 180, 190)
        stripe = lerp(label2, (255, 255, 255), 0.1)
        rim = lerp(label, (0, 0, 0), 0.32)
        half = THUMB / 2.0
        for i, nx, ny, d, cover in self._label:
            u = nx * ca + ny * sa
            v = -nx * sa + ny * ca
            if d < hole:
                tone = dark
            elif d < ring:
                tone = metal
            elif art is not None:
                ax = min(THUMB - 1, max(0, int((u * 0.98 + 1.0) * half)))
                ay = min(THUMB - 1, max(0, int((v * 0.98 + 1.0) * half)))
                k = (ay * THUMB + ax) * 3
                tone = (art[k], art[k + 1], art[k + 2])
                if u * u + v * v > 0.86:
                    tone = lerp(tone, (0, 0, 0), 0.35)
            elif u * u + v * v > 0.84:
                tone = rim
            elif abs(v) < 0.24 and abs(u) > 0.3:
                tone = stripe
            else:
                tone = label
            if cover >= 256:
                R[i], G[i], B[i] = tone
            else:
                R[i] += (tone[0] - R[i]) * cover >> 8
                G[i] += (tone[1] - G[i]) * cover >> 8
                B[i] += (tone[2] - B[i]) * cover >> 8

        self._paint_arm(cv, glow)

    def _paint_arm(self, cv: Canvas, accent: RGB) -> None:
        px, py = self._pivot
        radius, arm, base = self.radius, self._arm, self._base
        rho = self.reach * radius
        cos_a = max(-1.0, min(1.0, (base * base + arm * arm - rho * rho) / (2.0 * base * arm)))
        heading = math.atan2(self.cy - py, self.cx - px) - math.acos(cos_a)
        ux, uy = math.cos(heading), math.sin(heading)
        hx, hy = px + ux * arm, py + uy * arm
        thick = max(1.0, radius * 0.075)
        cv.line(px - ux * radius * 0.2, py - uy * radius * 0.2, px, py, (112, 116, 128), 256, thick * 2.2)
        cv.line(px, py, hx, hy, (204, 208, 218), 256, thick)
        cv.line(hx - ux * radius * 0.17, hy - uy * radius * 0.17, hx, hy, accent, 256, thick * 2.3)
        cv.disc(px, py, max(1.4, radius * 0.1), (148, 152, 164))
        cv.disc(px, py, max(0.5, radius * 0.04), (232, 234, 240))
