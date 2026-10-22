"""Проективная модель виртуальной камеры: поворот, пинхол с углом обзора по зуму, бочка объектива."""

import math
import time

import cv2
import numpy as np
import pytest

from ptztracker.camera import he42
from ptztracker.sim import projection as pj
from ptztracker.sim.scene import SyntheticStage

W, H = 1280, 720
WIDE, TELE = he42.HFOV_WIDE_DEG, he42.HFOV_TELE_DEG


def far(pan_deg, tilt_deg, dist=1000.0):
    """Точка мира далеко в направлении (pan, tilt)."""
    return pj.rotation(pan_deg, tilt_deg) @ np.array([0.0, 0.0, dist])


def proj1(p, pan, tilt, hfov, k1=0.0, size=(W, H)):
    xy, vis = pj.project(np.array([p]), pan, tilt, hfov, size, k1=k1)
    return xy[0], bool(vis[0])


def test_k1_schedule_strongest_at_wide():
    assert pj.lens_k1(WIDE) == pytest.approx(he42.LENS_K1_WIDE)
    assert he42.LENS_K1_WIDE < 0
    assert abs(pj.lens_k1(TELE)) < 1e-9
    assert pj.lens_k1(WIDE) < pj.lens_k1(20.0) < pj.lens_k1(5.0) <= 0


def test_optical_axis_stays_centred():
    for pan, tilt, hfov in [(0, 0, WIDE), (12.5, -7.0, WIDE), (-40, 20, 10.0), (3, -8, TELE)]:
        xy, vis = proj1(far(pan, tilt), pan, tilt, hfov, k1=pj.lens_k1(hfov))
        assert vis
        assert abs(xy[0] - W / 2) < 1e-6 and abs(xy[1] - H / 2) < 1e-6


def test_pan_by_half_fov_moves_point_to_edge():
    p = far(0, 0)
    # камера повернулась влево на полугол обзора — точка «прямо» уезжает к правому краю
    xy, _ = proj1(p, -WIDE / 2, 0, WIDE, k1=0.0)
    assert abs(xy[0] - W) < 0.5 and abs(xy[1] - H / 2) < 1e-6
    # бочка стягивает край к центру
    xy_d, _ = proj1(p, -WIDE / 2, 0, WIDE, k1=pj.lens_k1(WIDE))
    assert W * 0.9 < xy_d[0] < W - 10


def test_tilt_gives_keystone():
    # камера смотрит вниз: вертикаль сцены сходится — верх и низ одной вертикали смещены по x по-разному
    top = np.array([3.0, -2.0, 10.0])
    bottom = np.array([3.0, 2.0, 10.0])
    (xt, _), _ = proj1(top, 0, -20, WIDE)
    (xb, _), _ = proj1(bottom, 0, -20, WIDE)
    assert abs(xt - xb) > 20


def test_straight_line_bends_with_barrel():
    # горизонтальная прямая выше центра: при бочке середина дальше от центра, чем хорда
    xs = np.linspace(-5.0, 5.0, 3)
    pts = np.stack([xs, np.full(3, -2.5), np.full(3, 10.0)], axis=1)

    def sag(k1):
        xy, vis = pj.project(pts, 0, 0, WIDE, (W, H), k1=k1)
        assert vis.all()
        chord_y = (xy[0, 1] + xy[2, 1]) / 2
        return chord_y - xy[1, 1]  # > 0: середина выше хорды (дальше от центра)

    assert abs(sag(0.0)) < 1e-6
    assert sag(pj.lens_k1(WIDE)) > 3.0


def test_edge_moves_faster_than_centre_for_same_angular_speed():
    hfov = WIDE
    k1 = pj.lens_k1(hfov)
    d = 0.5  # градусов поворота

    def dx(angle):
        p = far(angle, 0)
        (x0, _), _ = proj1(p, 0, 0, hfov, k1=k1)
        (x1, _), _ = proj1(p, d, 0, hfov, k1=k1)
        return abs(x1 - x0)

    centre, edge = dx(0.0), dx(hfov / 2 - 1.0)
    assert edge > centre * 1.1
    # в центре кадра: f·dθ, где f = (W/2)/tan(hfov/2)
    f = pj.focal_px(hfov, W)
    assert centre == pytest.approx(f * math.radians(d), rel=1e-3)


def test_points_behind_camera_invisible():
    xy, vis = proj1(np.array([0.0, 0.0, -5.0]), 0, 0, WIDE)
    assert not vis


def test_pixel_rays_invert_projection():
    k1 = pj.lens_k1(WIDE)
    rays = pj.pixel_rays(WIDE, (320, 180), k1)
    # луч пикселя, спроецированный обратно, попадает в центр этого пикселя
    for v, u in [(0, 0), (90, 160), (179, 319), (10, 300)]:
        xy, vis = pj.project(rays[v, u][None, :].astype(np.float64), 0, 0, WIDE, (320, 180), k1=k1)
        assert vis[0]
        assert abs(xy[0, 0] - (u + 0.5)) < 0.01 and abs(xy[0, 1] - (v + 0.5)) < 0.01


# --- сцена ---------------------------------------------------------------


def test_projective_is_default_and_flat_selectable():
    assert SyntheticStage().projection == "projective"
    assert SyntheticStage(projection="flat").projection == "flat"
    with pytest.raises(ValueError):
        SyntheticStage(projection="fisheye")


def test_flat_view_matches_old_crop_path():
    st = SyntheticStage(projection="flat")
    w, h = st.size
    img, kp = st.view_ptz(2.0, 5.0, -8.0, WIDE, (640, 360))
    cx, cy, crop_w = w / 2 + 5.0 / 120 * w, h / 2 + 8.0 / 40 * h, w * WIDE / 120
    assert np.array_equal(img, st.render(2.0, cx, cy, crop_w, (640, 360)))
    assert kp.shape == (17, 3)


def aim_at_person(st, t, hfov):
    """pan/tilt, при которых середина человека в центре кадра."""
    x, y, z = st.person_world_point(t, 0.0, 0.5)
    pan = math.degrees(math.atan2(x, z))
    tilt = -math.degrees(math.atan2(y, math.hypot(x, z)))
    return pan, tilt


def test_keypoints_land_on_rendered_person():
    st = SyntheticStage()
    for t, hfov, dpan in [(0.0, WIDE, 0.0), (8.0, 20.0, 0.0), (8.0, WIDE, 22.0)]:
        pan, tilt = aim_at_person(st, t, hfov)
        img, kp = st.view_ptz(t, pan - dpan, tilt, hfov, (W, H))
        assert kp[[0, 5, 6, 11, 12], 2].min() > 0
        # голова: в точке носа — цвет головы
        nx, ny = int(kp[0, 0]), int(kp[0, 1])
        assert np.abs(img[ny, nx].astype(int) - (150, 170, 210)).max() < 30
        # торс: середина между плечами и бёдрами — цвет рубашки
        mx = int((kp[5, 0] + kp[6, 0] + kp[11, 0] + kp[12, 0]) / 4)
        my = int((kp[5, 1] + kp[6, 1] + kp[11, 1] + kp[12, 1]) / 4)
        assert np.abs(img[my, mx].astype(int) - st._shirt).max() < 30


def test_wall_texture_lands_where_projection_says():
    # угол экрана для слайдов у края кадра на широком угле: дисторсия здесь заметна (несколько px)
    st = SyntheticStage()
    u0, v0 = st.screen_rect[0], st.screen_rect[1]  # левый верхний угол экрана в текстуре
    p = st.wall_point(u0, v0)
    pan = math.degrees(math.atan2(p[0], p[2])) + 25.0
    tilt = -math.degrees(math.atan2(p[1], math.hypot(p[0], p[2]))) - 12.0
    img, _ = st.view_ptz(0.0, pan, tilt, WIDE, (W, H))
    xy, vis = pj.project(np.array([p]), pan, tilt, WIDE, (W, H), k1=pj.lens_k1(WIDE))
    xy_pin, _ = pj.project(np.array([p]), pan, tilt, WIDE, (W, H), k1=0.0)
    assert vis[0] and np.abs(xy[0] - xy_pin[0]).max() > 8  # без дисторсии промахнулись бы
    x, y = xy[0]
    screen = np.array([230, 225, 220])
    inside = img[int(y) + 3, int(x) + 3].astype(int)
    outside = img[int(y) - 4, int(x) - 4].astype(int)
    assert np.abs(inside - screen).max() < 25
    assert np.abs(outside - screen).max() > 40


def test_walking_toward_camera_grows():
    st = SyntheticStage(script="walk", person_depth=(14.0, 7.0))

    def height_px(t):
        pan, tilt = aim_at_person(st, t, WIDE)
        _, kp = st.view_ptz(t, pan, tilt, WIDE, (W, H))
        return kp[15, 1] - kp[0, 1]

    assert height_px(12.0) > 1.7 * height_px(0.0)  # у x1 вдвое ближе


def test_out_of_scene_black_projective():
    img, kp = SyntheticStage().view_ptz(0.0, 170.0, 0.0, WIDE, (640, 360))
    assert img.max() == 0 and kp[:, 2].max() == 0


def test_grid_maps_match_exact_maps():
    # карта по редкой сетке (быстро) совпадает с картой по каждому пикселю до долей пикселя кадра.
    # Сравниваем задник: у пола у горизонта карта круто меняется, там ошибка до ~1 px кадра.

    for pan, tilt, hfov in [(0.0, -6.0, WIDE), (-25.0, -14.0, WIDE), (4.0, -5.0, 8.0)]:
        st = SyntheticStage(map_step=1)
        fast = SyntheticStage(map_step=8)._texture_maps(pan, tilt, hfov, (W, H))
        mx, my, level = st._texture_maps(pan, tilt, hfov, (W, H))
        assert fast[2] == level
        wall = ((my > 0) & (my < st.floor_y / 2**level - 1) & (mx > 0)).astype(np.uint8)
        wall = cv2.erode(wall, np.ones((17, 17), np.uint8)).astype(bool)  # без соседства со швом
        assert wall.mean() > 0.3
        px_per_frame = (mx[H // 2, -1] - mx[H // 2, 0]) / W  # единиц карты на пиксель кадра
        for a, b in zip(fast[:2], (mx, my), strict=True):
            assert np.abs(a - b)[wall].max() < 0.1 * px_per_frame


def test_render_speed_1280():
    st = SyntheticStage()
    st.view_ptz(0.0, 0.0, -6.0, WIDE, (W, H))
    t0 = time.perf_counter()
    for i in range(10):
        st.view_ptz(0.1 * i, 0.3 * i, -6.0, WIDE, (W, H))  # камера поворачивается каждый кадр
    assert (time.perf_counter() - t0) / 10 < 0.040


def test_render_speed_640():
    st = SyntheticStage()
    st.view_ptz(0.0, 0.0, -6.0, WIDE, (640, 360))
    t0 = time.perf_counter()
    for i in range(10):
        st.view_ptz(0.1 * i, 0.3 * i, -6.0, WIDE * (1 - 0.01 * i), (640, 360))  # всё меняется каждый кадр
    assert (time.perf_counter() - t0) / 10 < 0.040
