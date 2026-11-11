import pytest

from cloudspin.canvas import Canvas
from cloudspin.mood import SCENES
from cloudspin.sky import Daylight, SkyScene, Weather, clear_sky


def paint(weather, light, size=(96, 40), steps=30, **setup):
    scene = SkyScene()
    for name, value in setup.items():
        setattr(scene, name, value)
    scene.resize(*size)
    scene.set_weather(weather)
    scene.set_daylight(light)
    canvas = Canvas(*size)
    for _ in range(steps):
        scene.step(1 / 12)
    scene.render(canvas)
    return scene, canvas


@pytest.mark.parametrize("key", list(SCENES))
@pytest.mark.parametrize("size", [(40, 8), (96, 40), (200, 64)])
def test_every_scene_paints_at_every_size(key, size):
    scene, canvas = paint(SCENES[key].weather, SCENES[key].daylight, size)
    assert len(canvas.r) == size[0] * size[1]
    assert len(set(canvas.r)) > 3, "the scene should not be a flat colour"


def test_rain_moves_between_frames():
    scene, canvas = paint(SCENES["rain"].weather, SCENES["rain"].daylight)
    before = bytes(canvas.b)
    scene.step(1 / 12)
    scene.render(canvas)
    assert bytes(canvas.b) != before


def test_clear_noon_is_brighter_than_midnight():
    _, day = paint(Weather(cover=0.0), Daylight(elevation=1.0))
    _, night = paint(Weather(cover=0.0), Daylight(elevation=-1.0))
    assert sum(day.b) > sum(night.b) * 3


def test_weather_eases_in_instead_of_cutting():
    scene, _ = paint(Weather(cover=0.0), Daylight(elevation=0.9))
    scene.set_weather(Weather(cover=1.0, rain=1.0))
    scene.step(1 / 12)
    assert 0.0 < scene.now.rain < 0.5
    for _ in range(200):
        scene.step(1 / 12)
    assert scene.now.rain > 0.99


def test_ground_takes_the_deck_colour():
    scene, canvas = paint(Weather(), Daylight(), ground_override=(40, 10, 60))
    scene.step(1 / 12)
    scene.render(canvas)
    last = len(canvas.r) - 1
    assert (canvas.r[last], canvas.g[last], canvas.b[last]) == (40, 10, 60)


def test_storm_strikes_lightning():
    scene, _ = paint(SCENES["storm"].weather, SCENES["storm"].daylight, steps=1)
    flashed = False
    for _ in range(12 * 20):
        scene.step(1 / 12)
        flashed = flashed or scene._flash > 0.5
    assert flashed


def test_sky_palette_runs_from_night_to_day():
    assert sum(clear_sky(-1.0)[0]) < sum(clear_sky(0.0)[2]) < sum(clear_sky(1.0)[2]) + 300
    assert clear_sky(0.0)[2][0] > clear_sky(0.0)[2][2], "the horizon is warm at sunset"
