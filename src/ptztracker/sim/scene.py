"""Сцены для виртуальной камеры: синтетическая сцена с докладчиком или зацикленное видео.

Сцена — это «панорама» всего, что может увидеть камера. У SyntheticStage по умолчанию панорама —
текстура задника в трёхмерной сцене, и кадр строится проективно (view_ptz). В плоском режиме и у
видео камера вырезает из панорамы окно по текущему положению и зуму (render).
"""

from __future__ import annotations

import math
import threading

import cv2
import numpy as np

from ptztracker.sim import person_motion as pm
from ptztracker.sim import projection

# Сценарии движения докладчика: (стоит у кафедры, идёт вправо, стоит, идёт влево), секунды.
# Человек ростом 1,75 м = 460 px сцены, путь 0,17 ширины сцены ≈ 5 м.
SCRIPTS = {
    "walk": (6.0, 4.0, 5.0, 6.0),  # быстро, ~1,2 м/с — стресс-тест
    "lecture": (8.0, 10.0, 8.0, 10.0),  # прохаживается, ~0,5 м/с
    "stand": None,  # стоит у кафедры, переминается с ноги на ногу
    "shuffle": None,  # стоит, но заметно шатается: на «Поясе» это ±0,11 кадра
}
SWAY_M = {"stand": 0.10, "shuffle": 0.18}  # размах переминания ±, метры
SWAY_PAUSE_M = 0.08  # переминание на стояниях между ходьбой
WALK_BOB_M, WALK_BOB_UP_M = 0.025, 0.015  # качание корпуса на ходу: вбок и вверх-вниз, метры
WALK_RAMP_S = 0.5  # человек разгоняется и тормозит не мгновенно

# Трёхмерная сцена (проективный режим), метры. Камера на штативе в конце зала: человек в 14 м
# от камеры и в 2 м перед задником; на широком угле он ~12% высоты кадра, как в плоском режиме.
PERSON_HEIGHT_M = 1.75
PERSON_DEPTH_M = 14.0
WALL_DISTANCE_M = 16.0
CAM_HEIGHT_M = 2.2  # объектив над полом сцены
WALL_HALF_ANGLE_DEG = 55.0  # задник (вся ширина панорамы) при pan = 0 виден на ±55°
MAP_STEP = 8  # шаг сетки, в узлах которой считаются лучи (между узлами — линейно)
LIM = 30000  # предел координат для рисования cv2 (точки далеко за кадром)


def _build_pyramid(img: np.ndarray, levels: int = 4) -> list[np.ndarray]:
    pyr = [img]
    for _ in range(levels - 1):
        pyr.append(cv2.pyrDown(pyr[-1]))
    return pyr


def view_matrix(cx: float, cy: float, crop_w: float, out_size: tuple[int, int]) -> np.ndarray:
    """Матрица 2×3 «координаты сцены → координаты кадра» для окна шириной crop_w с центром (cx, cy)."""
    out_w, out_h = out_size
    scale = out_w / crop_w
    return np.array([[scale, 0, out_w / 2 - scale * cx], [0, scale, out_h / 2 - scale * cy]])


def warp_view(
    pyramid: list[np.ndarray],
    cx: float,
    cy: float,
    crop_w: float,
    out_size: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Вырезать окно ширины crop_w с центром (cx, cy) в координатах уровня 0 и вписать в out_size.

    Возвращает (кадр, матрица 2×3 «координаты сцены → координаты кадра»).
    Всё за пределами сцены — чёрное.
    """
    out_w, out_h = out_size
    scale = out_w / crop_w  # сцена → кадр
    level = 0
    while level + 1 < len(pyramid) and scale * (2 ** (level + 1)) <= 1.0:
        level += 1
    src = pyramid[level]
    f = 2**level
    s = scale * f  # уровень → кадр
    m_level = np.array(
        [[s, 0, out_w / 2 - s * cx / f], [0, s, out_h / 2 - s * cy / f]],
        dtype=np.float64,
    )
    img = cv2.warpAffine(src, m_level, (out_w, out_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    return img, view_matrix(cx, cy, crop_w, out_size)


class SyntheticStage:
    """Сцена с фоном, экраном, кафедрой и одним идущим человеком. Полностью детерминирована по seed.

    projection="projective" (по умолчанию) — сцена трёхмерная: панорама — текстура задника на
    плоскости в wall_distance м перед камерой, пол на cam_height м ниже объектива, человек —
    вертикальная фигура на глубине person_depth м (пара — глубина у начала и у конца пути, между
    ними линейно). Кадр — поворот камеры, пинхол с углом обзора по зуму и бочка (sim.projection).
    projection="flat" — прежний режим: окно вырезается из плоской панорамы (сдвиг и масштаб).
    """

    PROJECTIONS = ("projective", "flat")

    def __init__(
        self,
        width: int = 7680,
        height: int = 2560,
        seed: int = 0,
        script: str = "walk",
        projection: str = "projective",
        person_depth: float | tuple[float, float] = PERSON_DEPTH_M,
        wall_distance: float = WALL_DISTANCE_M,
        cam_height: float = CAM_HEIGHT_M,
        map_step: int = MAP_STEP,
    ):
        if projection not in self.PROJECTIONS:
            raise ValueError(f"projection={projection!r}: ожидалось одно из {self.PROJECTIONS}")
        self.projection = projection
        self.script = SCRIPTS[script]
        self._sway_m = SWAY_M.get(script, SWAY_PAUSE_M)
        self.size = (width, height)
        self.seed = seed
        rng = np.random.default_rng(seed)
        w, h = width, height
        floor_y = int(h * 0.8)
        self.floor_y = floor_y
        img = np.zeros((h, w, 3), np.uint8)
        # стена: вертикальный градиент + шум-текстура (нужна для оценки сдвига кадра)
        grad = np.linspace(40, 90, floor_y, dtype=np.float32)[:, None]
        img[:floor_y] = np.stack([grad * 0.9, grad * 0.8, grad * 0.7], axis=-1).astype(np.uint8)
        small = rng.integers(0, 60, (h // 16 + 1, w // 16 + 1, 3), dtype=np.uint8)
        noise = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
        img = cv2.add(img, noise)
        # декоративные панели и светильники
        for _ in range(140):
            x, y = int(rng.integers(0, w)), int(rng.integers(0, floor_y - 50))
            pw, ph = int(rng.integers(30, 260)), int(rng.integers(30, 260))
            color = tuple(int(c) for c in rng.integers(30, 200, 3))
            cv2.rectangle(img, (x, y), (x + pw, y + ph), color, -1)
        # пол
        img[floor_y:] = (35, 45, 60)
        for x in range(0, w, 160):
            cv2.line(img, (x, floor_y), (x - 300, h), (50, 60, 75), 3)
        # экран для слайдов
        sx0, sx1 = int(w * 0.18), int(w * 0.38)
        self.screen_rect = (sx0, int(h * 0.15), sx1, int(h * 0.55))
        cv2.rectangle(img, (sx0, int(h * 0.15)), (sx1, int(h * 0.55)), (230, 225, 220), -1)
        cv2.putText(img, "SLIDE", (sx0 + 120, int(h * 0.38)), cv2.FONT_HERSHEY_SIMPLEX, 12, (90, 60, 40), 25)
        # кафедра
        lx = int(w * 0.42)
        cv2.rectangle(img, (lx, floor_y - int(h * 0.12)), (lx + int(w * 0.025), floor_y), (40, 60, 90), -1)
        self._bg = img
        self._pyr = _build_pyramid(img)
        self._person_h = h * 0.18
        self._x0, self._x1 = w * 0.45, w * 0.62
        self._shirt = tuple(int(c) for c in rng.integers(60, 220, 3))
        self._pants = tuple(int(c) for c in rng.integers(20, 90, 3))
        # трёхмерная сцена
        self.person_ppm = self._person_h / PERSON_HEIGHT_M  # px панорамы на метр пути человека
        self.wall_distance = float(wall_distance)
        self.cam_height = float(cam_height)
        self.wall_ppm = (w / 2) / (self.wall_distance * math.tan(math.radians(WALL_HALF_ANGLE_DEG)))
        d = person_depth if isinstance(person_depth, tuple) else (person_depth, person_depth)
        self.person_depth = (float(d[0]), float(d[1]))
        self.map_step = max(1, int(map_step))
        self._map_key = None
        self._maps = None

    def _phase(self, t: float) -> tuple[str, float, float]:
        """Что человек делает в момент t: ("stand"/"right"/"left", сколько уже идёт отрезок, его длина)."""
        if self.script is None:
            return "stand", float("inf"), float("inf")
        stand_a, walk_r, stand_b, walk_l = self.script
        u = t % sum(self.script)
        for name, length in (("stand", stand_a), ("right", walk_r), ("stand", stand_b), ("left", walk_l)):
            if u < length:
                return name, u, length
            u -= length
        return "left", walk_l, walk_l

    def _path_x(self, t: float) -> float:
        """Путь без качаний: стоит или идёт с разгоном и торможением."""
        if self.script is None:
            return self._x0
        name, u, length = self._phase(t)
        d = self._x1 - self._x0
        if name == "stand":
            stand_a = self.script[0]
            return self._x0 if (t % sum(self.script)) < stand_a else self._x1
        moved = pm.walk_position(u, 0.0, length, d / (length - WALK_RAMP_S), WALK_RAMP_S)
        return self._x0 + moved if name == "right" else self._x1 - moved

    def person_x(self, t: float) -> float:
        name, u, length = self._phase(t)
        if name == "stand":
            fade = 1.0 if math.isinf(length) else pm.phase_fade(u, length)
            return self._path_x(t) + fade * pm.sway(t, self._sway_m, self.seed) * self.person_ppm
        return self._path_x(t) + pm.phase_fade(u, length) * WALK_BOB_M * pm.walk_bob(t) * self.person_ppm

    def _bob_up_m(self, t: float) -> float:
        name, u, length = self._phase(t)
        if name == "stand":
            return 0.0
        return pm.phase_fade(u, length) * WALK_BOB_UP_M * abs(pm.walk_bob(t))

    def _walking(self, t: float) -> bool:
        return self._phase(t)[0] != "stand"

    def _swing(self, t: float) -> float:
        """Размах шага в долях роста."""
        return math.sin(t * 6.0) * 0.08 if self._walking(t) else 0.0

    # --- фигура: точки (a, b) в долях роста; a — вправо от оси, b — вверх от ступней -------------

    def _draw_skeleton(self, img: np.ndarray, P, ph: float, t: float) -> None:  # noqa: N803
        """Нарисовать человека. P(a, b) → (x, y) пикселя кадра или None (точка за камерой);
        ph — рост в пикселях кадра (толщина линий и голова)."""
        sw = self._swing(t)
        thick = max(1, int(ph * 0.07))
        arm = max(1, thick // 2 + 1)
        legs = [((-0.05, 0.47), (-0.06 + sw, 0.0)), ((0.05, 0.47), (0.06 - sw, 0.0))]
        arms = [((-0.11, 0.82), (-0.14 - sw * 0.5, 0.47)), ((0.11, 0.82), (0.14 + sw * 0.5, 0.47))]
        torso = [(-0.11, 0.82), (0.11, 0.82), (0.09, 0.47), (-0.09, 0.47)]

        def ip(ab):
            xy = P(*ab)
            if xy is None:
                return None
            return int(round(min(LIM, max(-LIM, xy[0])))), int(round(min(LIM, max(-LIM, xy[1]))))

        for a, b in legs:
            pa, pb = ip(a), ip(b)
            if pa and pb:
                cv2.line(img, pa, pb, self._pants, thick)
        quad = [ip(ab) for ab in torso]
        if all(quad):
            cv2.fillConvexPoly(img, np.array(quad), self._shirt)
        for a, b in arms:
            pa, pb = ip(a), ip(b)
            if pa and pb:
                cv2.line(img, pa, pb, self._shirt, arm)
        head = ip((0.0, 0.92))
        if head:
            cv2.circle(img, head, max(1, int(ph * 0.075)), (150, 170, 210), -1)

    def _keypoint_ab(self, t: float) -> dict[int, tuple[float, float]]:
        """COCO-индекс → (a, b). Заполнены нос, плечи, бёдра, колени, щиколотки. Левые точки —
        левые для самого человека (он смотрит на камеру, т. е. справа в кадре)."""
        sw = self._swing(t)
        return {
            0: (0.0, 0.92),
            5: (0.11, 0.82),
            6: (-0.11, 0.82),
            11: (0.05, 0.47),
            12: (-0.05, 0.47),
            13: (0.055 - sw / 2, 0.24),
            14: (-0.055 + sw / 2, 0.24),
            15: (0.06 - sw, 0.0),
            16: (-0.06 + sw, 0.0),
        }

    # --- плоский режим ---------------------------------------------------------------------------

    def _flat_projector(self, m: np.ndarray, t: float):
        px, hgt = self.person_x(t), self._person_h
        foot = self.floor_y - self._bob_up_m(t) * self.person_ppm
        return lambda a, b: (m[0, 0] * (px + a * hgt) + m[0, 2], m[1, 1] * (foot - b * hgt) + m[1, 2])

    def _draw_person(self, img: np.ndarray, m: np.ndarray, t: float) -> None:
        self._draw_skeleton(img, self._flat_projector(m, t), self._person_h * m[0, 0], t)

    def render(self, t: float, cx: float, cy: float, crop_w: float, out_size: tuple[int, int]) -> np.ndarray:
        """Плоский режим: окно ширины crop_w с центром (cx, cy) в координатах панорамы."""
        img, m = warp_view(self._pyr, cx, cy, crop_w, out_size)
        self._draw_person(img, m, t)
        return img

    def person_keypoints(self, t: float, m: np.ndarray, out_size: tuple[int, int]) -> np.ndarray:
        """Плоский режим: истинные ключевые точки (COCO-17, x/y/conf) в кадре; вне кадра — conf 0."""
        proj = self._flat_projector(m, t)
        kp = np.zeros((17, 3), np.float32)
        w, h = out_size
        for i, ab in self._keypoint_ab(t).items():
            fx, fy = proj(*ab)
            kp[i] = (fx, fy, 1.0 if 0 <= fx < w and 0 <= fy < h else 0.0)
        return kp

    # --- проективный режим -----------------------------------------------------------------------

    def person_world_point(self, t: float, a: float, b: float) -> tuple[float, float, float]:
        """Точка фигуры (a, b) в мире, метры (X вправо, Y вниз, Z вперёд от камеры)."""
        px = self.person_x(t)
        d0, d1 = self.person_depth
        z = d0 + (d1 - d0) * (px - self._x0) / (self._x1 - self._x0)
        x = (px - self.size[0] / 2) / self.person_ppm
        return x + a * PERSON_HEIGHT_M, self.cam_height - b * PERSON_HEIGHT_M - self._bob_up_m(t), z

    def wall_point(self, u: float, v: float) -> tuple[float, float, float]:
        """Точка (u, v) панорамы на плоскости задника, метры. Строка floor_y — линия пола."""
        return (
            (u - self.size[0] / 2) / self.wall_ppm,
            self.cam_height + (v - self.floor_y) / self.wall_ppm,
            self.wall_distance,
        )

    def _texture_maps(self, pan: float, tilt: float, hfov: float, out_size: tuple[int, int]):
        """Карты cv2.remap «пиксель кадра → точка панорамы» и уровень пирамиды.

        Пересчёт — только когда камера сдвинулась или сменился зум (неподвижная камера — из кэша).
        Лучи считаются в узлах сетки с шагом map_step и растягиваются до кадра: карта гладкая,
        а полный расчёт на 1280×720 — ~0,1 с на кадр.
        """
        key = (pan, tilt, hfov, tuple(out_size))
        if key == self._map_key:
            return self._maps
        out_w, out_h = out_size
        step = self.map_step if out_w % self.map_step == 0 and out_h % self.map_step == 0 else 1
        if step > 1:
            rays = projection.grid_rays(hfov, out_size, step)
        else:
            rays = projection.pixel_rays(hfov, out_size)
        rot = projection.rotation(pan, tilt).astype(np.float32)
        d = rays @ rot.T  # лучи пикселей в мире
        dx, dy, dz = d[..., 0], d[..., 1], d[..., 2]
        big = np.float32(1e9)
        dist, cam_h = np.float32(self.wall_distance), np.float32(self.cam_height)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            s_wall = np.where(dz > 1e-6, dist / dz, big)
            s_floor = np.where((dy > 1e-6) & (dz > 0), cam_h / dy, big)  # пол — только перед камерой
        on_floor = s_floor < s_wall
        s = np.minimum(s_wall, s_floor)
        w, h = self.size
        ppm, fy = np.float32(self.wall_ppm), np.float32(self.floor_y)
        mx = np.float32(w / 2) + dx * s * ppm
        v_wall = fy + (dy * s - cam_h) * ppm
        # пол — полоса пола панорамы, отражаемая туда-обратно в глубину (от задника к камере):
        # без разрывов, поэтому карту можно интерполировать
        period = np.float32(h - self.floor_y - 1)
        v_floor = fy + period - np.abs(np.mod((dist - dz * s) * ppm, 2 * period) - period)
        my = np.where(on_floor, v_floor, v_wall)
        miss = s >= big
        mx[miss] = -10.0
        my[miss] = -10.0
        # уровень пирамиды — по масштабу в центре кадра, как в плоском режиме
        ch, cw = rays.shape[0] // 2, rays.shape[1] // 2
        f = projection.focal_px(hfov, out_w)
        scale = f / (float(s[ch, cw]) * float(np.linalg.norm(rays[ch, cw])) * self.wall_ppm)
        if step > 1:
            big_size = (out_w + 2 * step, out_h + 2 * step)
            mx = cv2.resize(mx, big_size, interpolation=cv2.INTER_LINEAR)[step : step + out_h, step : step + out_w]
            my = cv2.resize(my, big_size, interpolation=cv2.INTER_LINEAR)[step : step + out_h, step : step + out_w]
        level = 0
        while level + 1 < len(self._pyr) and scale * (2 ** (level + 1)) <= 1.0:
            level += 1
        k = np.float32(2**level)
        # модель: центр пикселя i — координата i + 0.5; у cv2.remap центр пикселя источника — целое
        mx = np.clip(mx / k - 0.5, -10.0, w + 10.0)
        my = np.clip(my / k - 0.5, -10.0, h + 10.0)
        self._map_key, self._maps = key, (mx, my, level)
        return self._maps

    def _projective_view(self, t, pan, tilt, hfov, out_size):
        mx, my, level = self._texture_maps(pan, tilt, hfov, out_size)
        img = cv2.remap(self._pyr[level], mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        k1 = projection.lens_k1(hfov)

        def proj(a, b):
            xy, _ = projection.project(np.array([self.person_world_point(t, a, b)]), pan, tilt, hfov, out_size, k1)
            return None if np.isnan(xy[0, 0]) else (xy[0, 0] - 0.5, xy[0, 1] - 0.5)

        ids = self._keypoint_ab(t)
        pts = np.array([self.person_world_point(t, a, b) for a, b in ids.values()])
        xy, vis = projection.project(pts, pan, tilt, hfov, out_size, k1)
        kp = np.zeros((17, 3), np.float32)
        for j, i in enumerate(ids):
            if not np.isnan(xy[j, 0]):
                kp[i] = (xy[j, 0], xy[j, 1], 1.0 if vis[j] else 0.0)
        feet, top = proj(0.0, 0.0), proj(0.0, 1.0)
        if feet and top:
            self._draw_skeleton(img, proj, math.hypot(feet[0] - top[0], feet[1] - top[1]), t)
        return img, kp

    def view_ptz(
        self,
        t: float,
        pan_deg: float,
        tilt_deg: float,
        hfov_deg: float,
        out_size: tuple[int, int],
        pan_span_deg: float = 120.0,
        tilt_span_deg: float = 40.0,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Кадр камеры в положении pan/tilt с углом обзора hfov и истинные ключевые точки (17×3).

        pan_span_deg/tilt_span_deg — сколько градусов занимает панорама в плоском режиме.
        """
        if self.projection == "projective":
            return self._projective_view(t, pan_deg, tilt_deg, hfov_deg, out_size)
        w, h = self.size
        cx = w / 2 + pan_deg / pan_span_deg * w
        cy = h / 2 - tilt_deg / tilt_span_deg * h
        crop_w = w * hfov_deg / pan_span_deg
        img = self.render(t, cx, cy, crop_w, out_size)
        return img, self.person_keypoints(t, view_matrix(cx, cy, crop_w, out_size), out_size)

    def frame_at(self, t: float) -> np.ndarray:
        img = self._bg.copy()
        self._draw_person(img, np.array([[1.0, 0, 0], [0, 1.0, 0]]), t)
        return img


class VideoScene:
    """Зацикленное видео (например, запись доклада общим планом) как панорама для виртуальной камеры."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise ValueError(f"Не удалось открыть видео: {path}")
        self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 25.0
        ok, frame = self._cap.read()
        if not ok:
            raise ValueError(f"В видео нет кадров: {path}")
        self._frame = frame
        self._index = 0
        self.size = (frame.shape[1], frame.shape[0])

    def frame_at(self, t: float) -> np.ndarray:
        want = int(t * self._fps)
        with self._lock:
            while self._index < want:
                ok, frame = self._cap.read()
                if not ok:  # конец — по кругу
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, frame = self._cap.read()
                    if not ok:
                        break
                self._frame = frame
                self._index += 1
            return self._frame

    def render(self, t: float, cx: float, cy: float, crop_w: float, out_size: tuple[int, int]) -> np.ndarray:
        img, _ = warp_view(_build_pyramid(self.frame_at(t)), cx, cy, crop_w, out_size)
        return img
