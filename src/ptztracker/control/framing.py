"""Регулятор кадрирования: по положению человека решает, с какой скоростью вести pan/tilt/zoom.

Чистая функция шага (без потоков и сети), вызывается ~25 раз в секунду. Как оператор:
- не реагирует на покачивания внутри мёртвой зоны;
- трогается и тормозит плавно (ограничение ускорения поверх разгона самой камеры);
- идёт вместе с человеком (скорость человека «в мире» — упреждение);
- учитывает, что кадр опаздывает: камера уже сдвинула картинку, пока кадр шёл по сети;
- оставляет место перед идущим человеком;
- зумирует только когда кадр спокоен, а отъезжает сразу, если человек у края.

Модель зума (zdyn) идёт по командам, которые реально ушли в камеру (zoom_sent), и подправляется
по ответам #GZ (FramingInput.zoom_pos_t): зум может ждать в очереди за поворотом.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from ptztracker.camera import he42
from ptztracker.camera import protocol as P
from ptztracker.control.camera_model import Calibration, CameraDynamics, zoom_ln
from ptztracker.control.composition import BodyMeasure, Composition
from ptztracker.control.effort import EffortProfile

ALPHA = 0.35  # сглаживание положения человека
VEL_WINDOW_S = 0.4  # окно оценки скорости человека
CALM_WINDOW_S = 1.5  # «человек стоит», если за это время сместился меньше мёртвой зоны
SWAY_FRACTION = 0.8  # смещение меньше этой доли мёртвой зоны за окно — покачивание, а не ходьба
# Вес упреждения по скорости человека. Скорость считается с поправкой на движение камеры по модели;
# если реальная камера быстрее модели в ρ раз, в оценку попадает (ρ−1)·скорость камеры и замыкается
# внутренняя петля. Вес 0.5 держит её устойчивой до ρ≈2 с задержкой очереди 0.13 с (тест).
FF_GAIN = 0.5
# Сторож раскачки: столько настоящих разворотов за окно — стоп и выключение. Часть 0 (B3): 61% прежних
# «разворотов» — при команде, на которой камера стоит, 81% — во время зума; правила — в SwingWatch.
WATCHDOG_REVERSALS, WATCHDOG_WINDOW_S = 3, 6.0
WATCHDOG_MOVING_V = 0.03  # fw/s: медленнее камера стоит (коды 50–59) — смена знака команды не разворот
WATCHDOG_ERR_FRACTION = 1.0  # пик ошибки между разворотами больше всей мягкой зоны
WATCHDOG_GROWTH = 1.0  # пик не меньше прошлого (в долях): затухающее качание — не раскачка
WATCHDOG_APC_MIN_DEG = 0.02  # за полупериод меньше — камера не ехала (0,03 fw/s за 0,5 с на теле — ~0,05°)
WATCHDOG_APC_MIN_STEP = max(1, math.ceil(WATCHDOG_APC_MIN_DEG / he42.APC_DEG_PER_UNIT))  # в единицах #APC
TILT_FROM_FH = 9 / 16  # скорость по вертикали (доли высоты) → эквивалент по ширине кадра 16:9
LENS_END_MARGIN = 8
LOST_STOP_S = 0.5
STILL_V = 0.02  # медленнее — считаем, что человек стоит
START_V, STOP_V = 0.05, 0.03  # скорость человека для перехода в «движение» / обратно в «стоим»
RELEASE = 0.4  # возврат в «стоим», когда ошибка < RELEASE·dead
EDGE_GAIN = 1.8  # за мягкой зоной догоняем сильнее
LEAD_MAX, LEAD_TAU = 0.08, 0.8
SETTLE_V = 0.06  # человек остановился, камера проехала — возвращаем медленно, без «пинг-понга»
NO_TARGET_S = 0.3
ZOOM_ACCEL = 0.5  # ln/с²
ZOOM_IN_AFTER_HOLD_S = 1.0
U_SMOOTH = 0.3
# Поправка модели зума по ответу #GZ: доля расхождения за один ответ и предел одной поправки (ln).
# Соответствие #GZ → масштаб живьём не мерялось (he42.ZOOM_RATIO) — замер не должен перебивать модель целиком.
GZ_GAIN, GZ_MAX_STEP = 0.3, 0.15


@dataclass(frozen=True)
class FramingInput:
    now: float
    t_capture: float | None  # когда пришёл кадр, по которому измерено (None — нового замера нет)
    measure: BodyMeasure | None
    zoom_pos: int
    aspect: float = 16 / 9
    zoom_pos_t: float | None = None  # когда пришёл ответ #GZ с zoom_pos (None — положение не замерено)
    pan_tilt: tuple[int, int] | None = None  # положение pan/tilt по #APC (единицы камеры)
    pan_tilt_t: float | None = None  # когда пришёл этот ответ #APC (None — положение не замерено)


@dataclass(frozen=True)
class FramingOutput:
    pan: float  # fw/s, > 0 — вправо
    tilt: float  # fh/s, > 0 — вверх
    zoom: float  # ln/s, > 0 — к теле
    state: Literal["idle", "holding", "moving", "diverged"]
    error: tuple[float, float]
    size_error: float | None


def _limit(v: float, lim: float) -> float:
    return max(-lim, min(lim, v))


class SwingWatch:
    """Сторож раскачки одной оси: считает только настоящие развороты камеры.

    Разворот — команда сменила направление, пройдя через скорость, на которой камера правда едет
    (WATCHDOG_MOVING_V). Он засчитывается, если:
    - новое направление дал не доворот за зумом (без него команда тоже за порогом);
    - пик ошибки с прошлого разворота больше мягкой зоны и не меньше прошлого пика (качание растёт);
    - по #APC камера за этот полупериод проехала и развернулась. Нет двух ответов #APC за полупериод
      (опрос не шёл, старая запись) — решают одни команды.
    """

    def __init__(self, fw_per_unit: float = 1.0, lag: float = 0.0) -> None:
        self.fw_per_unit = fw_per_unit  # скорость оси → fw/s камеры (наклон: TILT_FROM_FH)
        self.lag = lag  # на столько камера отстаёт от команды (мёртвое время + разгон)
        self.reset()

    def reset(self) -> None:
        self.flips: list[float] = []  # моменты засчитанных разворотов за окно WATCHDOG_WINDOW_S
        self.stand_flips: list[float] = []  # развороты камеры, пока человек стоит (по ним — «рыскание»)
        self._dir = 0  # направление команды, пока она за порогом WATCHDOG_MOVING_V
        self._turn_t: float | None = None  # прошлый разворот (засчитанный или нет)
        self._peak = 0.0  # пик |ошибки| с прошлого разворота
        self._prev_peak: float | None = None
        self._apc: list[tuple[float, int]] = []  # ответы #APC: (момент, положение)
        self._prev_move: int | None = None  # смещение камеры по #APC за прошлый полупериод
        # где был человек при двух прошлых разворотах (позапрошлый — разворот в ту же сторону)
        self._turn_targets: tuple[float | None, float | None] = (None, None)
        self._travel = 0.0  # сколько камера проехала по командам с прошлого разворота (доли кадра)
        self._t: float | None = None

    def camera_at(self, t: float, pos: int) -> None:
        """Ответ #APC по этой оси (единицы камеры)."""
        if self._apc and t <= self._apc[-1][0]:
            return
        self._apc.append((t, int(pos)))
        self._apc = [(ts, p) for ts, p in self._apc if t - ts <= 2 * WATCHDOG_WINDOW_S]

    def _camera_move(self, t0: float, t1: float) -> int | None:
        pts = [p for ts, p in self._apc if t0 <= ts <= t1]
        return pts[-1] - pts[0] if len(pts) >= 2 else None

    def _camera_turned(self, d: int, v_track: float, move: int | None) -> bool:
        """Разворот настоящий: новое направление дал не доворот за зумом, и #APC (если есть) это подтверждает."""
        if v_track * self.fw_per_unit * d <= WATCHDOG_MOVING_V:
            return False  # развернул доворот за зумом
        if move is not None:
            if abs(move) < WATCHDOG_APC_MIN_STEP:
                return False  # камера за полупериод не сдвинулась
            if self._prev_move is not None and abs(self._prev_move) >= WATCHDOG_APC_MIN_STEP \
                    and (move > 0) == (self._prev_move > 0):
                return False  # камера ехала в ту же сторону — не развернулась
        return True

    def _counts(self, d: int, v_track: float, soft: float, move: int | None) -> bool:
        if not self._camera_turned(d, v_track, move):
            return False
        if self._peak <= WATCHDOG_ERR_FRACTION * soft:
            return False
        return self._prev_peak is None or self._peak >= WATCHDOG_GROWTH * self._prev_peak

    @property
    def swinging(self) -> bool:
        return len(self.flips) >= WATCHDOG_REVERSALS or len(self.stand_flips) >= WATCHDOG_REVERSALS

    def update(
        self, now: float, v_cmd: float, err: float, soft: float, zoom_feed: float = 0.0,
        target: float | None = None, dead: float = 0.0,
    ) -> None:
        """Такт регулятора: команда оси, ошибка, мягкая зона; zoom_feed — часть команды от доворота за зумом.

        target — где человек «в мире» (в долях кадра при текущем зуме), dead — мёртвая зона: если между
        разворотами камеры человек сдвинулся меньше неё, он стоит, а камера ходит туда-сюда сама.
        """
        self._peak = max(self._peak, abs(err))
        if self._t is not None:
            self._travel += abs(v_cmd) * max(0.0, now - self._t)
        self._t = now
        v = v_cmd * self.fw_per_unit
        d = 1 if v > WATCHDOG_MOVING_V else (-1 if v < -WATCHDOG_MOVING_V else 0)
        if d and self._dir and d != self._dir:
            start = float("-inf") if self._turn_t is None else self._turn_t + self.lag
            move = self._camera_move(start, now)
            if self._counts(d, v_cmd - zoom_feed, soft, move):
                self.flips.append(now)
            # Человек стоит, а камера ходит туда-сюда: разворот считается при любой ошибке. Положение человека
            # сравнивается с разворотом в ту же сторону (период назад): ошибка модели камеры при качании
            # (например, неверная задержка видео) в одной фазе одинакова и вычитается.
            same_phase = self._turn_targets[0]
            stood = target is not None and same_phase is not None and abs(target - same_phase) < dead
            if not stood:
                self.stand_flips = []  # человек сдвинулся: развороты за ним — не рыскание
            elif self._travel > soft and self._camera_turned(d, v_cmd - zoom_feed, move):
                self.stand_flips.append(now)  # камера за полупериод проехала больше мягкой зоны
            self._prev_peak, self._peak, self._turn_t, self._prev_move = self._peak, 0.0, now, move
            self._travel = 0.0
            self._turn_targets = (self._turn_targets[1], target)
        if d:
            self._dir = d
        self.flips = [t for t in self.flips if now - t <= WATCHDOG_WINDOW_S]
        self.stand_flips = [t for t in self.stand_flips if now - t <= WATCHDOG_WINDOW_S]


class _Axis:
    """Одна ось (pan по x или tilt по y). sign: +1 — движение камеры уводит картинку к меньшим координатам.

    Внутри всё считается в «мировых» единицах, не зависящих от зума: смещение от центра кадра,
    делённое на текущий масштаб зума k. Иначе наезд (лицо уходит от центра) выглядел бы как ходьба.
    """

    def __init__(self, sign: int, dyn: CameraDynamics, fw_per_unit: float = 1.0, lag: float = 0.0):
        self.sign = sign
        self.dyn = dyn
        self.watch = SwingWatch(fw_per_unit, lag)
        self.reset()

    def reset(self) -> None:
        self.world: float | None = None
        self.v_world = 0.0  # мировые единицы/с
        self.v_img = 0.0  # то же в долях кадра/с при текущем зуме
        self.hist: list[tuple[float, float]] = []
        self.calm_hist: list[tuple[float, float]] = []
        self.t_meas: float | None = None
        self.mode = "holding"
        self.v_cmd = 0.0
        self.lead = 0.0
        self.err = 0.0
        self.watch.reset()

    def _cam(self, t: float) -> float:
        return self.dyn.position(t)

    def measure(self, img: float, t_shot: float, k_shot: float) -> None:
        # «мировая» позиция: что было бы без поворота камеры и без зума
        z = (img - 0.5) / k_shot + self.sign * self._cam(t_shot)
        if self.world is None or self.t_meas is None:
            self.world, self.v_world, self.t_meas = z, 0.0, t_shot
            self.hist = [(t_shot, z)]
            return
        dt = max(1e-3, t_shot - self.t_meas)
        pred = self.world + self.v_world * dt
        self.world = pred + ALPHA * (z - pred)
        self.t_meas = t_shot
        self.calm_hist.append((t_shot, z))
        self.calm_hist = [(t, v) for t, v in self.calm_hist if t_shot - t <= CALM_WINDOW_S + 0.5]
        # скорость — наклон по последним VEL_WINDOW_S: быстро замечает, что человек остановился
        self.hist.append((t_shot, z))
        self.hist = [(t, v) for t, v in self.hist if t_shot - t <= VEL_WINDOW_S]
        if len(self.hist) >= 3:
            ts = [t for t, _ in self.hist]
            zs = [v for _, v in self.hist]
            mt, mz = sum(ts) / len(ts), sum(zs) / len(zs)
            den = sum((t - mt) ** 2 for t in ts)
            if den > 0:
                self.v_world = sum((t - mt) * (v - mz) for t, v in zip(ts, zs)) / den

    def spread(self, now: float, k_now: float) -> float:
        """Размах положения человека за последние CALM_WINDOW_S, в долях кадра при текущем зуме."""
        pts = [z for t, z in self.calm_hist if now - t <= CALM_WINDOW_S]
        if len(pts) < 3:
            return float("inf")
        return (max(pts) - min(pts)) * k_now

    def step(
        self,
        now: float,
        dt: float,
        anchor: float,
        dead: float,
        soft: float,
        e: EffortProfile,
        lead_k: float,
        active: bool,
        k_now: float = 1.0,
        zoom_rate: float = 0.0,
    ) -> float:
        if active and self.world is not None:
            self.v_img = self.v_world * k_now
            v_w = self.v_img if abs(self.v_img) >= STILL_V else 0.0
            img_now = 0.5 + (self.world + self.v_world * (now - self.t_meas) - self.sign * self._cam(now)) * k_now
            lead_target = _limit(-lead_k * v_w, LEAD_MAX)
            self.lead += (lead_target - self.lead) * (1 - math.exp(-dt / LEAD_TAU))
            self.err = img_now - (anchor + self.lead)
            a = abs(self.err)
            # «Пошёл» — не просто скорость (покачивание у кафедры даёт её тоже), а реальное смещение
            # за окно CALM_WINDOW_S. Иначе камера ходит за покачиванием туда-сюда.
            walked = self.spread(now, k_now) >= SWAY_FRACTION * dead
            # трогаемся, только когда человек вышел из мёртвой зоны: скорость покачивания не повод
            if self.mode == "holding" and a > dead:
                self.mode = "moving"
            elif self.mode == "moving" and a < RELEASE * dead and (abs(v_w) < STOP_V or not walked):
                self.mode = "holding"
            if not walked:
                v_w = 0.0  # покачивание — не повод вести камеру за человеком
            if self.mode == "moving":
                base = RELEASE * dead
                shaped = max(0.0, a - base)
                if a > soft:
                    shaped = (soft - base) + EDGE_GAIN * (a - soft)
                want = self.sign * (FF_GAIN * v_w + e.gain * math.copysign(shaped, self.err))
                # Камера проехала остановившегося (или замедлившегося) человека: назад — только медленно,
                # пока он в мягкой зоне. Оператор так и делает: доводит, а не дёргает обратно.
                against = want * self.sign * self.v_img < 0
                if a <= soft and (abs(self.v_img) < STOP_V or against):
                    want = _limit(want, SETTLE_V)
            else:
                want = 0.0
            # Наезд/отъезд уводит человека от центра кадра — доворачиваем одновременно, как оператор
            zoom_feed = self.sign * (img_now - 0.5) * zoom_rate
            want += zoom_feed
        else:
            want = zoom_feed = 0.0
        want = _limit(want, e.v_max)
        # без цели — останавливаемся быстрее (за ~0.5 с), но так же плавно
        a_lim = e.a_max if active else max(e.a_max, abs(self.v_cmd) / LOST_STOP_S)
        self.v_cmd += _limit(want - self.v_cmd, a_lim * dt)
        if abs(self.v_cmd) < 1e-9:
            self.v_cmd = 0.0
        self.v_cmd = float(self.v_cmd)  # из ключевых точек приходят numpy-числа
        target = None if not active or self.world is None else (self.world + self.v_world * (now - self.t_meas)) * k_now
        self.watch.update(now, self.v_cmd, self.err, soft, float(zoom_feed), target, dead)
        self.dyn.command(now, self.v_cmd / k_now)  # камера даёт fw/s при текущем зуме → в мировые единицы
        return self.v_cmd


class FramingController:
    def __init__(self, composition: Composition, effort: EffortProfile, calib: Calibration, video_latency: float):
        self.composition = composition
        self.effort = effort
        self.calib = calib
        self.video_latency = video_latency
        self.reset()

    def set_composition(self, c: Composition) -> None:
        self.composition = c

    def set_effort(self, e: EffortProfile) -> None:
        self.effort = e

    def reset(self) -> None:
        c = self.calib
        lag = c.dead_time + c.tau
        self.x = _Axis(+1, CameraDynamics(c.dead_time, c.tau), lag=lag)
        self.y = _Axis(-1, CameraDynamics(c.dead_time, c.tau), fw_per_unit=TILT_FROM_FH, lag=lag)
        self.zdyn = CameraDynamics(c.dead_time, c.tau)
        self._t_prev: float | None = None
        self._t_seen: float | None = None
        self._last_cap: float | None = None
        self._u: float | None = None
        self._u_t: float | None = None
        self._zoom_cmd = 0.0
        self._zoom_active = False
        self._hold_since: float | None = None
        self._still_since: float | None = None
        self._gz_t: float | None = None  # момент последнего учтённого ответа #GZ
        self._gz_ref: float | None = None  # ln масштаба (по #GZ), которому соответствует k = 1
        self._apc_t: float | None = None  # момент последнего учтённого ответа #APC
        self.diverged = False

    def zoom_sent(self, t: float, rate: float) -> None:
        """Команда зума (ln/с) ушла в камеру в момент t: модель зума идёт по отправленному, а не по решённому."""
        self.zdyn.command(t, rate)

    def _zoom_measured(self, t: float, pos: int) -> None:
        """Ответ #GZ: подтянуть модель зума к замеру (масштаб k считается от первого ответа после reset)."""
        ln = zoom_ln(pos)
        if self._gz_ref is None:
            self._gz_ref = ln - self.zdyn.position(t)
            return
        self.zdyn.shift(_limit(GZ_GAIN * ((ln - self._gz_ref) - self.zdyn.position(t)), GZ_MAX_STEP))

    def step(self, inp: FramingInput) -> FramingOutput:
        now = inp.now
        dt = CTRL_DT_DEFAULT if self._t_prev is None else max(1e-3, now - self._t_prev)
        self._t_prev = now
        e, comp = self.effort, self.composition
        if inp.zoom_pos_t is not None and inp.zoom_pos_t != self._gz_t:
            self._gz_t = inp.zoom_pos_t
            self._zoom_measured(inp.zoom_pos_t, inp.zoom_pos)
        if inp.pan_tilt is not None and inp.pan_tilt_t is not None and inp.pan_tilt_t != self._apc_t:
            self._apc_t = inp.pan_tilt_t  # сторож раскачки сверяет развороты с настоящими
            self.x.watch.camera_at(inp.pan_tilt_t, inp.pan_tilt[0])
            self.y.watch.camera_at(inp.pan_tilt_t, inp.pan_tilt[1])

        if inp.measure is not None and inp.t_capture is not None and inp.t_capture != self._last_cap:
            self._last_cap = inp.t_capture
            self._t_seen = now
            t_shot = inp.t_capture - self.video_latency
            k_shot = self._k(t_shot)
            self.x.measure(inp.measure.nose[0], t_shot, k_shot)
            self.y.measure(inp.measure.nose[1], t_shot, k_shot)
            if inp.measure.u is not None:
                # размер «сейчас»: за время в пути кадра зум мог измениться
                u_now = inp.measure.u * math.exp(self.zdyn.displacement(t_shot, now))
                self._u = u_now if self._u is None else self._u + U_SMOOTH * (u_now - self._u)
                self._u_t = now

        if not self.diverged and (self.x.watch.swinging or self.y.watch.swinging):
            self.diverged = True  # раскачка — дальше только остановка, до reset()
        active = (self._t_seen is not None and now - self._t_seen <= NO_TARGET_S) and not self.diverged
        dead_y = e.dead_y * inp.aspect / (16 / 9)
        k_now = self._k(now)
        zr = self.zdyn.velocity(now)
        pan = self.x.step(now, dt, comp.anchor[0], e.dead_x, e.soft_x, e, e.lead, active, k_now, zr)
        tilt = self.y.step(now, dt, comp.anchor[1], dead_y, e.soft_y, e, 0.0, active, k_now, zr)

        if self.diverged:
            state = "diverged"
            self.x.mode = self.y.mode = "holding"
            self._hold_since = None
        elif not active:
            state = "idle"
            self.x.mode = self.y.mode = "holding"
            self._hold_since = None
        elif self.x.mode == "moving" or self.y.mode == "moving":
            state = "moving"
            self._hold_since = None
        else:
            state = "holding"
            if self._hold_since is None:
                self._hold_since = now

        # «Стоит» — по размаху мирового положения за CALM_WINDOW_S (покачивание у кафедры не мешает)
        still = active and self.x.spread(now, k_now) < e.dead_x and self.y.spread(now, k_now) < dead_y
        if not still:
            self._still_since = None
        elif self._still_since is None:
            self._still_since = now
        walking = state == "moving" or self._still_since is None
        zoom, size_err = self._zoom(now, dt, active, walking, e, inp.zoom_pos)
        return FramingOutput(pan, tilt, zoom, state, (self.x.err, self.y.err), size_err)

    def _k(self, t: float) -> float:
        """Во сколько раз картинка увеличена зумом с момента старта регулятора."""
        return math.exp(self.zdyn.position(t))

    def _zoom(
        self, now: float, dt: float, active: bool, walking: bool, e: EffortProfile, zoom_pos: int
    ) -> tuple[float, float | None]:
        want = 0.0
        size_err = None
        if active:
            at_edge = abs(self.x.err) > e.soft_x or abs(self.y.err) > e.soft_y
            if self._u is not None and self._u > 0:
                size_err = math.log(self._u / self.composition.u_frac)
                a = abs(size_err)
                if not self._zoom_active and a > e.zoom_dead:
                    self._zoom_active = True
                elif self._zoom_active and a < 0.5 * e.zoom_dead:
                    self._zoom_active = False
                if self._zoom_active:
                    want = _limit(-e.zoom_gain * size_err, e.zoom_max)
                    # Наезд — только когда человек сам стоит (≥1 с). Камера при этом может подправлять
                    # наклон: наезд идёт одним плавным движением, а не «ступеньками».
                    calm = self._still_since is not None and now - self._still_since >= ZOOM_IN_AFTER_HOLD_S
                    if want > 0 and not calm:
                        want = 0.0
            if at_edge:
                want = -e.zoom_max  # человек уходит из кадра — сразу шире
        # у края диапазона объектива дальше не зумируем
        if (want < 0 and zoom_pos <= P.LENS_MIN + LENS_END_MARGIN) or (want > 0 and zoom_pos >= P.LENS_MAX - LENS_END_MARGIN):
            want = 0.0
        if walking and self._zoom_cmd > 0 and want <= 0:
            self._zoom_cmd = 0.0  # человек пошёл — наезд прекращаем сразу, без плавного спада
        self._zoom_cmd += _limit(want - self._zoom_cmd, ZOOM_ACCEL * dt)
        if abs(self._zoom_cmd) < 1e-9:
            self._zoom_cmd = 0.0
        return self._zoom_cmd, size_err


CTRL_DT_DEFAULT = 0.04
