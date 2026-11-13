"""Область сцены: геометрия без камеры (точка кадра ↔ направление в зале)."""

import math

import numpy as np
import pytest

from ptztracker.camera import he42
from ptztracker.camera import protocol as P
from ptztracker.control import stage_area as sa
from ptztracker.sim import projection
from ptztracker.vision.types import Person

WIDE, TELE = sa.CameraView(0.0, 0.0, he42.HFOV_WIDE_DEG), sa.CameraView(20.0, -8.0, 5.0)


@pytest.mark.parametrize("view", [WIDE, TELE, sa.CameraView(-40.0, 12.0, 30.0)])
@pytest.mark.parametrize("xy", [(0.5, 0.5), (0.0, 0.0), (1.0, 1.0), (0.1, 0.9), (0.8, 0.3)])
def test_frame_point_round_trip(view, xy):
    back = sa.to_frame(view, *sa.to_world(view, *xy))
    assert back == pytest.approx(xy, abs=1e-9)


def test_frame_centre_is_camera_direction():
    assert sa.to_world(TELE, 0.5, 0.5) == pytest.approx((20.0, -8.0))


def test_right_and_down_in_frame_are_pan_right_and_tilt_down():
    pan, tilt = sa.to_world(WIDE, 1.0, 0.5)
    assert pan == pytest.approx(he42.HFOV_WIDE_DEG / 2) and tilt == pytest.approx(0.0)
    assert sa.to_world(WIDE, 0.5, 1.0)[1] < 0  # низ кадра — ниже горизонта камеры


def test_point_behind_camera_has_no_place_in_frame():
    assert sa.to_frame(WIDE, 170.0, 0.0) is None


@pytest.mark.parametrize("pan,tilt,hfov", [(0, 0, 62.1), (25, -10, 62.1), (-30, 15, 20.0), (10, -5, 3.3)])
def test_agrees_with_simulator_projection(pan, tilt, hfov):
    """Та же точка мира в симуляторе (без бочки объектива) и здесь — в одном месте кадра."""
    pts = (projection.rotation(pan, tilt) @ np.array([[0.02, 0.01, 1.0], [-0.03, -0.02, 1.0]]).T).T * 10
    xy, _ = projection.project(pts, pan, tilt, hfov, (1280, 720), k1=0.0)
    view = sa.CameraView(pan, tilt, hfov)
    for (X, Y, Z), (px, py) in zip(pts, xy):
        wp, wt = math.degrees(math.atan2(X, Z)), math.degrees(math.atan2(-Y, math.hypot(X, Z)))
        assert sa.to_frame(view, wp, wt) == pytest.approx((px / 1280, py / 720), abs=1e-6)


def test_view_from_camera_answers():
    v = sa.view_from((he42.APC_CENTER + 1000, he42.APC_CENTER - 500), P.LENS_MIN)
    assert v.pan_deg == pytest.approx(8.3) and v.tilt_deg == pytest.approx(-4.15)
    assert v.hfov_deg == pytest.approx(he42.HFOV_WIDE_DEG)
    assert sa.view_from((he42.APC_CENTER, he42.APC_CENTER), P.LENS_MAX).hfov_deg == pytest.approx(he42.HFOV_TELE_DEG)


def test_contains_and_borders():
    a = sa.StageArea(-20.0, 20.0, -5.0, 30.0)
    assert a.contains(0.0, 0.0) and a.contains(-20.0, -5.0) and a.contains(20.0, 30.0)
    assert not a.contains(0.0, -5.1) and not a.contains(20.1, 0.0)


def test_json_round_trip_and_garbage():
    a = sa.StageArea(-20.5, 20.0, -5.0, 30.0, on=False)
    assert sa.StageArea.from_json(a.to_json()) == a
    assert a.to_json() == {"pan": [-20.5, 20.0], "tilt": [-5.0, 30.0], "on": False}
    for bad in (None, 5, "x", {}, {"pan": [1], "tilt": [0, 1]}, {"pan": [3, 1], "tilt": [0, 1]},
                {"pan": ["a", 1], "tilt": [0, 1]}, {"pan": [0, float("nan")], "tilt": [0, 1]},
                {"pan": [0, 1], "tilt": [5, 5]}, {"pan": [-500, 1], "tilt": [0, 1]}):
        assert sa.StageArea.from_json(bad) is None


def test_point_of_frame_inside_area():
    a = sa.StageArea(-180.0, 180.0, -5.0, 90.0)  # «линия»: ниже −5° — зал
    assert a.holds(WIDE, 0.5, 0.5) and not a.holds(WIDE, 0.5, 0.95)


def test_outline_is_inside_view_even_for_huge_area():
    a = sa.StageArea(-180.0, 180.0, -5.0, 90.0)
    pts = a.outline(WIDE)
    assert len(pts) >= 16 and all(math.isfinite(x) and math.isfinite(y) for x, y in pts)
    ys = [y for x, y in pts if 0.3 < x < 0.7]
    assert max(ys) == pytest.approx(sa.to_frame(WIDE, 0.0, -5.0)[1], abs=0.01)  # нижний край — на −5°
    assert min(y for _, y in pts) < 0 and min(x for x, _ in pts) < 0 and max(x for x, _ in pts) > 1


def _person(nose_conf):
    kp = np.zeros((17, 3), np.float32)
    kp[0] = (300.0, 120.0, nose_conf)
    return Person((200.0, 100.0, 400.0, 600.0), 0.9, kp)


def test_head_point_is_nose_or_top_of_box():
    assert sa.head_point(_person(0.9)) == pytest.approx((300.0, 120.0))
    assert sa.head_point(_person(0.05)) == pytest.approx((300.0, 150.0))  # середина верха, на 0,1 высоты ниже


@pytest.mark.parametrize("motion", [(1.0, 0.1, 0.0), (1.0, 0.0, -0.08), (1.3, 0.05, 0.02), (0.8, -0.1, 0.04)])
def test_moved_view_keeps_world_points_where_the_picture_moved_them(motion):
    """Картинку сдвинула сама камера: точка зала, стоявшая в p, теперь в 0,5 + r·(p − 0,5) + сдвиг."""
    r, sx, sy = motion
    new = sa.moved_view(TELE, motion)
    for p in ((0.5, 0.5), (0.3, 0.6), (0.7, 0.4)):
        want = (0.5 + r * (p[0] - 0.5) + sx, 0.5 + r * (p[1] - 0.5) + sy)
        assert sa.to_frame(new, *sa.to_world(TELE, *p)) == pytest.approx(want, abs=2e-3)


def test_moved_view_without_motion_is_the_same_view():
    assert sa.moved_view(WIDE, (1.0, 0.0, 0.0)) == WIDE


def test_flags_for_people_in_frame():
    area = sa.StageArea(-180.0, 180.0, -5.0, 90.0)
    hi, lo = _person(0.9), Person((200.0, 500.0, 400.0, 720.0), 0.9, np.zeros((17, 3), np.float32))
    assert sa.flags(area, WIDE, [hi, lo], (1280, 720)) == [True, False]
    assert sa.flags(None, WIDE, [hi], (1280, 720)) is None  # области нет
    assert sa.flags(sa.StageArea(-180.0, 180.0, -5.0, 90.0, on=False), WIDE, [hi], (1280, 720)) is None  # выключена
    assert sa.flags(area, None, [hi], (1280, 720)) is None  # вид камеры неизвестен — область не действует


# --- настройка мышью ---

def test_first_area_is_a_line_at_70_percent_of_frame():
    a = sa.default_area(TELE)
    assert (a.pan0, a.pan1, a.tilt1, a.on) == (-180.0, 180.0, 90.0, True)
    assert a.tilt0 == pytest.approx(sa.to_world(TELE, 0.5, 0.7)[1])
    assert a.holds(TELE, 0.1, 0.1) and a.holds(TELE, 0.9, 0.65) and not a.holds(TELE, 0.5, 0.8)


def test_side_under_mouse():
    a = sa.StageArea(-20.0, 20.0, -10.0, 10.0)
    x0, y1 = sa.to_frame(WIDE, -20.0, 0.0)[0], sa.to_frame(WIDE, 0.0, -10.0)[1]
    assert sa.side_at(a, WIDE, x0 + 0.01, 0.5, 0.03) == "pan0"
    assert sa.side_at(a, WIDE, 0.5, y1 - 0.01, 0.03) == "tilt0"
    assert sa.side_at(a, WIDE, 0.5, 0.5, 0.03) is None
    assert sa.side_at(sa.default_area(WIDE), WIDE, 0.02, 0.3, 0.03) is None  # бок далеко за кадром — не хватается


def test_drag_side_moves_one_edge_and_keeps_area_sane():
    a = sa.StageArea(-20.0, 20.0, -10.0, 10.0)
    assert sa.drag_side(a, "tilt0", 3.0, -14.0) == sa.StageArea(-20.0, 20.0, -14.0, 10.0)
    assert sa.drag_side(a, "pan1", 31.0, 0.0) == sa.StageArea(-20.0, 31.0, -10.0, 10.0)
    assert sa.drag_side(a, "pan0", 50.0, 0.0).pan0 == pytest.approx(20.0 - sa.MIN_SIZE_DEG)  # не выворачивается
    assert sa.drag_side(a, "tilt1", 0.0, 200.0).tilt1 == 90.0


def test_hidden_sides_come_back_into_frame():
    a = sa.fit_hidden_sides(sa.default_area(WIDE), WIDE)
    out = a.outline(WIDE)
    assert all(0.0 < x < 1.0 and 0.0 < y < 1.0 for x, y in out)  # вся рамка в кадре
    assert a.tilt0 == pytest.approx(sa.default_area(WIDE).tilt0)  # видимый край не тронут
    assert sa.fit_hidden_sides(a, WIDE) == a


def test_handles_are_on_visible_sides():
    a = sa.StageArea(-20.0, 20.0, -10.0, 10.0)
    h = sa.handles(a, WIDE)
    assert set(h) == {"pan0", "pan1", "tilt0", "tilt1"}
    assert h["tilt0"] == pytest.approx(sa.to_frame(WIDE, 0.0, -10.0))
    assert set(sa.handles(sa.default_area(WIDE), WIDE)) == {"tilt0"}
