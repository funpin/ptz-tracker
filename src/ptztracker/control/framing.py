"""Регулятор кадрирования: по положению человека решает, с какой скоростью вести pan/tilt/zoom.

Чистая функция шага (без потоков и сети), вызывается ~25 раз в секунду. Как оператор:
- не реагирует на покачивания внутри мёртвой зоны; «пошёл» — по скорости и смещению в одну сторону за 0,4 с;
- идёт вместе с человеком (полное упреждение по скорости человека «в мире»);
- трогается и тормозит плавно: ускорение и рывок ограничены у движения самой камеры (модель разгона), а
  когда человек уходит за мягкую зону — догоняет изо всех сил;
- тормозит заранее: ошибка берётся такой, какой станет, когда камера ответит (тормозной путь —
  мёртвое время + τ), после перелёта возвращается медленно;
- учитывает, что кадр опаздывает: камера уже сдвинула картинку, пока кадр шёл по сети;
- оставляет место перед идущим человеком;
- зумирует только когда кадр спокоен, а отъезжает сразу, если человек у края или поворотом его не догнать.

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
WALK_WINDOW_S = 0.4  # «пошёл»: за это время сместился в одну сторону ...
WALK_SHIFT = 1.2  # ... больше этой доли мёртвой зоны (покачивание у кафедры столько не проходит)
WALK_SHIFT_MIN = 0.06  # ... и не меньше этого (доли кадра): переминание ±0,06 при «Резко» — не ходьба
WALK_SLOW_SHIFT = 0.16  # или за CALM_WINDOW_S в одну сторону больше этого: медленно идёт (общий план)
# Поворот ждёт в очереди планировщика (min_interval 0,13 с, последняя команда побеждает) в среднем половину
# интервала: модель pan/tilt регулятора считает это частью мёртвого времени. Без этого при разгоне τ 0,12 с
# модель опережала камеру на очередь, переминание казалось ходьбой, и «Резко» качалось при ρ ≥ 0,85 (DECISIONS).
# Модель зума идёт по командам, которые ушли (zoom_sent), — ей это не нужно.
PT_QUEUE_S = 0.065
BRAKE = 2.5  # тормозим твёрже, чем разгоняемся: ускорение и рывок торможения — в столько раз больше
CATCH_UP = 10.0  # человек уходит за мягкую зону — разгон в столько раз больше a_max (по сути, как может камера)
# Упреждение по скорости человека — полное: на ходу человек у якоря. Скорость считается с поправкой на
# движение камеры по модели; если камера быстрее модели в ρ раз, в оценку попадает (ρ−1)·скорость камеры.
# Рост упреждения плавный (FF_RISE_S), падение — сразу: так эта петля не раскачивает камеру, пока
# подстройка скорости (speed_adapt) не нашла ρ (тесты ×0,5…×2).
FF_GAIN = 1.0
FF_RISE_S = 0.5
START_BLEND = 0.5  # пошёл: скорость — середина между окном VEL_WINDOW_S и тремя последними замерами
STOP_INNOV = 0.01  # доли кадра: человек позади прогноза больше этого два замера подряд — тормозит
# Возврат после перелёта: со скоростью RETURN_GAIN·ошибка, не быстрее RETURN_V_MAX (человек за мягкой
# зоной) и RETURN_V_SOFT (в ней); направление «туда»
# забывается, если камера стоит дольше RETURN_WINDOW_S. Человек правда пошёл назад — когда упреждение
# в ту сторону не меньше WALK_BACK_V (кажущееся «назад» сразу после торможения камеры меньше).
RETURN_GAIN, RETURN_V_MAX, RETURN_WINDOW_S, WALK_BACK_V = 2.5, 0.25, 3.0, 0.1
# Отъезд у края: за EDGE_ZOOM_ERR — со скоростью EDGE_ZOOM·zoom_max; прогноз ухода (Axis.escape) за
# ESCAPE_ERR — сразу ESCAPE_ZOOM ln/с: поворотом уже не успеть (быстрая ходьба на «Поясе»).
EDGE_ZOOM_ERR, EDGE_ZOOM, EDGE_ZOOM_ACCEL = 0.3, 1.5, 2.0
ESCAPE_ERR, ESCAPE_ZOOM, ESCAPE_ZOOM_ACCEL = 0.4, 0.6, 10.0
SAT_FRAC, EDGE_SHORTFALL, HARD_EDGE_ERR = 0.9, 0.08, 0.39  # отъезд — когда v_cmd ≥ 0,9·потолок; полная скорость при недостаче ≥ 0,08
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
# Предел наезда при слежении (#GZ): выше 0xD54 угол обзора меньше ~7° и любое движение — большая доля кадра, а
# поворот камеры замедляется (he42.TELE_PAN_DEG_S). «Пояс» на дальнего человека (HE40 2026-10-30) уводил зум до
# 0xF41 (~3°) — размер цели просто не достигается. Ручной зум и отъезд не ограничены.
TRACK_ZOOM_MAX = 0xD55
# ln: наезд снова разрешён, когда зум отъехал от предела больше чем на столько. Больше ZOOM_LIMIT_LEAD_S·zoom_max на
# «Резко» (0,04): иначе обнуление команды наезда при остановке само снимало бы защёлку
ZOOM_LIMIT_RELEASE = 0.05
ZOOM_LIMIT_LEAD_S = 0.1  # команда наезда, ещё не дошедшая до камеры (очередь планировщика), — в прогнозе положения
LOST_STOP_S = 0.5
LOST_COAST_S = 0.6  # человек ушёл за край быстрее камеры и пропал — столько ещё идём за ним
STILL_V = 0.02  # медленнее — считаем, что человек стоит
START_V, STOP_V = 0.05, 0.03  # скорость человека для перехода в «движение» / обратно в «стоим»
RELEASE = 0.4  # возврат в «стоим», когда ошибка < RELEASE·dead
EDGE_GAIN = 1.8  # за мягкой зоной догоняем сильнее
LEAD_MAX, LEAD_TAU = 0.08, 0.8
SETTLE_V = 0.08  # человек остановился, камера проехала — возвращаем медленно, без «пинг-понга»
RETURN_V_SOFT = 1.5 * SETTLE_V  # возврат после перелёта, пока человек в мягкой зоне
NO_TARGET_S = 0.3
ZOOM_ACCEL = 0.5  # ln/с²
ZOOM_IN_AFTER_HOLD_S = 1.0
# «Спокоен» для наезда: размах за CALM_WINDOW_S меньше ZOOM_CALM_IN·мёртвая зона (наезд начинается) и не больше
# ZOOM_CALM_OUT·мёртвая зона (идущий наезд продолжается; размах считается в кадре сейчас, так что наезд сам
# останавливается, когда переминание в кадре вырастает до того, что камера ещё держит без раскачки). Стоящий человек переминается ±0,03…0,06 кадра —
# размах 0,06…0,12 больше мёртвой зоны 0,05, и по прежнему правилу «стоит» наезд не начинался.
ZOOM_CALM_IN, ZOOM_CALM_OUT = 2.2, 2.5
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


def _slope(pts: list[tuple[float, float]]) -> float:
    """Наклон прямой по точкам (t, x) методом наименьших квадратов; 0 — если точек меньше трёх."""
    if len(pts) < 3:
        return 0.0
    mt = sum(t for t, _ in pts) / len(pts)
    mx = sum(x for _, x in pts) / len(pts)
    den = sum((t - mt) ** 2 for t, _ in pts)
    return sum((t - mt) * (x - mx) for t, x in pts) / den if den > 0 else 0.0


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
        self._pending: int | None = None  # разворот, начатый доворотом за зумом: засчитать, если команда его удержит

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
        if self._pending is not None:
            # разворот начал доворот за зумом, но в этом же полупериоде его продолжила сама команда
            if self._pending == d and (v_cmd - zoom_feed) * self.fw_per_unit * d > WATCHDOG_MOVING_V:
                self.flips.append(now)
                self._pending = None
            elif d and d != self._pending:
                self._pending = None
        if d and self._dir and d != self._dir:
            start = float("-inf") if self._turn_t is None else self._turn_t + self.lag
            move = self._camera_move(start, now)
            self._pending = None
            if abs(zoom_feed) > abs(v_cmd):
                pass  # доворот за зумом больше всей команды: ось спорит с моделью зума, это не раскачка
            elif self._counts(d, v_cmd - zoom_feed, soft, move):
                self.flips.append(now)
            elif (v_cmd - zoom_feed) * self.fw_per_unit * d <= WATCHDOG_MOVING_V and self._counts(d, v_cmd, soft, move):
                self._pending = d  # засчитать, если направление удержит сама команда, а не доворот
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
        self._behind = 0  # сколько замеров подряд человек позади прогноза
        self.walked = False  # «пошёл» (Axis.walking)
        self.mode = "holding"
        self.v_cmd = 0.0
        self.a_cmd = 0.0  # ускорение камеры по модели (fw/s²), которое задаёт команда: по нему — рывок
        self.v_ff = 0.0  # скорость упреждения, доли кадра/с
        self.lead = 0.0
        self.err = 0.0
        self.escape = 0.0  # прогноз наибольшей ошибки, пока камера догоняет (доли кадра)
        self.catch_up = False  # человек уходит за мягкую зону — догоняем с ускорением камеры без ограничения
        self.travel_dir = 0  # куда камера шла за человеком в последний раз
        self.travel_t = 0.0
        self.exit_cmd = 0.0  # команда «за уходящим за край», если он пропадёт
        self.active_t = 0.0
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
        # скорость — наклон по последним VEL_WINDOW_S
        self.hist.append((t_shot, z))
        self.hist = [(t, v) for t, v in self.hist if t_shot - t <= VEL_WINDOW_S]
        if len(self.hist) >= 3:
            self.v_world = _slope(self.hist)
            v_short = _slope(self.hist[-3:])
            # пошёл (разгоняется): наклон по трём последним замерам больше — берём середину, а не ждём окна
            if v_short * self.v_world > 0 and abs(v_short) > abs(self.v_world):
                self.v_world += START_BLEND * (v_short - self.v_world)
            # человек отстаёт от прогноза два замера подряд — тормозит: скорость по трём последним замерам
            behind = (z - pred) * self.v_world < 0 and abs(z - pred) * k_shot > STOP_INNOV
            self._behind = self._behind + 1 if behind else 0
            if self._behind >= 2:
                self.v_world = v_short if v_short * self.v_world > 0 and abs(v_short) < abs(self.v_world) else (
                    0.0 if v_short * self.v_world <= 0 else self.v_world)

    def spread(self, now: float, k_now: float) -> float:
        """Размах положения человека за последние CALM_WINDOW_S, в долях кадра при текущем зуме."""
        pts = [z for t, z in self.calm_hist if now - t <= CALM_WINDOW_S]
        if len(pts) < 3:
            return float("inf")
        return (max(pts) - min(pts)) * k_now

    def walking(self, k_now: float, dead: float) -> bool:
        """«Пошёл»: за последние WALK_WINDOW_S человек сместился в одну сторону больше WALK_SHIFT·dead,
        и скорость того же знака. Покачивание у кафедры за такое окно столько не проходит. Вызывать раз
        за такт: помнит прошлый ответ."""
        if self.t_meas is None:
            return False
        pts = [(t, z) for t, z in self.calm_hist if self.t_meas - t <= WALK_WINDOW_S]
        if len(pts) < 3 or pts[-1][0] - pts[0][0] < 0.5 * WALK_WINDOW_S:
            return False
        shift = (pts[-1][1] - pts[0][1]) * k_now
        # гистерезис: уже идёт — достаточно половины смещения и скорости STOP_V
        need = max(WALK_SHIFT * dead, WALK_SHIFT_MIN)
        need, v_min = (0.5 * need, STOP_V) if self.walked else (need, START_V)
        self.walked = abs(shift) >= need and abs(self.v_img) >= v_min and shift * self.v_img > 0
        if not self.walked and abs(self.v_img) >= v_min:
            # медленная ходьба (общий план): за CALM_WINDOW_S в одну сторону больше, чем переминание туда-обратно
            slow = [z for t, z in self.calm_hist if self.t_meas - t <= CALM_WINDOW_S]
            if len(slow) >= 3:
                long_shift = (slow[-1] - slow[0]) * k_now
                self.walked = abs(long_shift) >= WALK_SLOW_SHIFT and long_shift * self.v_img > 0
        return self.walked

    def _ramp(self, want: float, dt: float, e: EffortProfile, now: float, k_now: float) -> None:
        """Команда к want так, чтобы ускорение камеры по модели ((команда − скорость)/τ) было не больше a_max
        (торможение — BRAKE·a_max, человек уходит за мягкую зону — без ограничения), а его изменение —
        не больше j_max. Плавность — у движения самой камеры, а не у команды."""
        tau = max(0.05, self.dyn.tau)
        v_m = self.dyn.velocity(now + self.dyn.dead_time) * k_now  # скорость камеры, когда команда дойдёт
        dv = want - v_m
        braking = v_m * dv < 0
        if braking:
            a_lim, j = BRAKE * e.a_max, BRAKE * e.j_max
        elif self.catch_up:
            a_lim, j = CATCH_UP * e.a_max, CATCH_UP * e.j_max
        else:
            a_lim, j = e.a_max, e.j_max
        # сколько скорость ещё изменится, пока нынешнее ускорение гаснет с рывком j, — это уже «в пути»
        rest = dv - self.a_cmd * abs(self.a_cmd) / (2 * j)
        a_des = math.copysign(min(a_lim, math.sqrt(2 * j * abs(rest))), rest)
        u = v_m + (self.a_cmd + _limit(a_des - self.a_cmd, j * dt)) * tau
        u = max(min(v_m, want), min(max(v_m, want), u))  # не дальше want и не назад от него
        if abs(want) < 1e-4 and abs(u) < 1e-3:
            u = 0.0  # камера и так стоит (код 50): без «хвоста» команды
        self.a_cmd = (u - v_m) / tau
        self.v_cmd = u

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
        v_cap: float | None = None,
        coast: bool = False,
    ) -> float:
        v_cap = e.v_max if v_cap is None else v_cap
        if active and self.world is not None:
            self.v_img = self.v_world * k_now
            v_w = self.v_img if abs(self.v_img) >= STILL_V else 0.0
            img_now = 0.5 + (self.world + self.v_world * (now - self.t_meas) - self.sign * self._cam(now)) * k_now
            lead_target = _limit(-lead_k * v_w, LEAD_MAX)
            self.lead += (lead_target - self.lead) * (1 - math.exp(-dt / LEAD_TAU))
            self.err = img_now - (anchor + self.lead)
            a = abs(self.err)
            # «Пошёл» — по скорости и направлению за WALK_WINDOW_S: покачивание у кафедры даёт скорость,
            # но не смещение в одну сторону. Иначе камера ходит за покачиванием туда-сюда.
            walked = self.walking(k_now, dead)
            # трогаемся, только когда человек вышел из мёртвой зоны: скорость покачивания не повод
            if self.mode == "holding" and a > dead:
                self.mode = "moving"
            elif self.mode == "moving" and a < RELEASE * dead and (abs(v_w) < STOP_V or not walked):
                self.mode = "holding"
            if not walked:
                v_w = 0.0  # покачивание — не повод вести камеру за человеком
            # Упреждение: падение скорости — сразу (остановился), рост — плавно, с FF_RISE_S. Если камера
            # быстрее модели, её разгон кажется замедлением человека; так эта петля не раскачивает камеру.
            if v_w * self.v_ff <= 0:
                self.v_ff = v_w * (1 - math.exp(-dt / FF_RISE_S))
            elif abs(v_w) <= abs(self.v_ff):
                self.v_ff = v_w
            else:
                self.v_ff += (v_w - self.v_ff) * (1 - math.exp(-dt / FF_RISE_S))
            t_resp = self.dyn.dead_time + self.dyn.tau  # за столько камера отвечает на команду
            # Куда уйдёт человек, пока камера его догоняет: v·(мёртвое время + τ) + v²/(2·a) по скорости
            # относительно камеры. За мягкой зоной — догоняем изо всех сил; за ESCAPE_ERR — ещё и шире.
            v_rel = v_w - self.sign * self.dyn.velocity(now) * k_now
            self.escape = a + abs(v_rel) * t_resp + v_rel * v_rel / (2 * BRAKE * e.a_max) if v_rel * self.err > 0 else a
            # догон без ограничения — только по горизонтали: человек уходит вбок; по вертикали ошибка большая
            # бывает от наезда/отъезда (модель зума отстаёт), и резкий наклон за ней раскачивает камеру
            self.catch_up = self.sign > 0 and (a > soft or abs(self.err + v_w * t_resp) > soft)
            if self.mode == "moving":
                # Ошибка — какой она станет, когда камера ответит: человек пройдёт v·(мёртвое время + τ),
                # камера — свой тормозной путь по уже отданным командам. Так камера тормозит заранее.
                e_pred = self.err + v_w * t_resp - self.sign * self.dyn.coast(now) * k_now
                ap, base = abs(e_pred), RELEASE * dead
                shaped = max(0.0, ap - base)
                if ap > soft:
                    shaped = (soft - base) + EDGE_GAIN * (ap - soft)
                # камера по инерции проскочит человека: тормозим до его скорости, но не командуем «назад»
                fb = 0.0 if e_pred * self.err < 0 else e.gain * math.copysign(shaped, e_pred)
                want = self.sign * (FF_GAIN * self.v_ff + fb)
                # Назад (против того, куда камера шла за человеком), а человек туда не идёт — перелёт после
                # остановки: возвращаемся плавно, не быстрее RETURN_GAIN·ошибка и RETURN_V_MAX.
                # Пока камера ещё катится «туда», «человек идёт назад» — кажущееся (ошибка задержки или скорости
                # камеры при торможении): возврат всё равно медленный.
                v_cam = self.dyn.velocity(now) * k_now
                walks_back = abs(self.v_ff) >= WALK_BACK_V and want * self.sign * self.v_ff > 0 \
                    and v_cam * self.travel_dir * self.sign <= WATCHDOG_MOVING_V
                returning = want * self.travel_dir < 0 and not walks_back
                against = want * self.sign * self.v_img < 0
                if returning:
                    v_back = RETURN_V_MAX if a > soft else RETURN_V_SOFT  # в мягкой зоне — совсем медленно
                    want = _limit(want, min(v_back, max(SETTLE_V, RETURN_GAIN * (a - base))))
                elif a <= soft and (abs(self.v_img) < STOP_V or against):
                    # Оператор так и делает: доводит, а не дёргает обратно.
                    want = _limit(want, SETTLE_V)
            else:
                want = 0.0
            # Наезд/отъезд сдвигает человека в кадре — доворачиваем одновременно, как оператор. Но если человек
            # уходит за край, а доворот при отъезде повёл бы камеру от него — не доворачиваем: отъезд сам
            # вернёт его к центру.
            zoom_feed = self.sign * (img_now - 0.5) * zoom_rate
            if zoom_rate < 0 and a > soft and zoom_feed * self.sign * self.err < 0:
                zoom_feed = 0.0
            want += zoom_feed
            # Человек уходит за край кадра: если он пропадёт, камера ещё LOST_COAST_S идёт за ним с его
            # скоростью — он снова появляется в кадре, а не теряется насовсем.
            self.exit_cmd = self.sign * FF_GAIN * self.v_ff if a > soft and self.err * self.v_ff > 0 else 0.0
            self.active_t = now
        elif coast and self.exit_cmd and now - self.active_t <= LOST_COAST_S:
            want, zoom_feed = self.exit_cmd, 0.0
        else:
            want = zoom_feed = 0.0
            self.exit_cmd = 0.0
        want = _limit(want, v_cap)
        if abs(self.v_ff) >= WALK_BACK_V:
            self.travel_dir = 1 if self.sign * self.v_ff > 0 else -1
            self.travel_t = now
        elif self.travel_dir and self.mode == "holding" and now - self.travel_t > RETURN_WINDOW_S:
            self.travel_dir = 0  # давно стоим: следующий шаг в любую сторону — уже не возврат после перелёта
        if active:
            self._ramp(want, dt, e, now, k_now)
        else:
            # без цели — останавливаемся быстрее (за ~0.5 с), с торможением камеры BRAKE·a_max
            a_lim = max(BRAKE * e.a_max, abs(self.v_cmd) / LOST_STOP_S)
            self.v_cmd += _limit(want - self.v_cmd, a_lim * dt)
            self.a_cmd = 0.0
        if abs(self.v_cmd) < 1e-9:
            self.v_cmd = self.a_cmd = 0.0
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
        dead = c.dead_time + PT_QUEUE_S
        lag = dead + c.tau
        self.x = _Axis(+1, CameraDynamics(dead, c.tau), lag=lag)
        self.y = _Axis(-1, CameraDynamics(dead, c.tau), fw_per_unit=TILT_FROM_FH, lag=lag)
        self.zdyn = CameraDynamics(c.dead_time, c.tau)
        self._t_prev: float | None = None
        self._t_seen: float | None = None
        self._last_cap: float | None = None
        self._u: float | None = None
        self._u_t: float | None = None
        self._zoom_cmd = 0.0
        self._zoom_active = False
        self._zoom_capped = False  # наезд остановлен у TRACK_ZOOM_MAX (защёлка с запасом ZOOM_LIMIT_RELEASE)
        self._hold_since: float | None = None
        self._still_since: float | None = None
        self._zoom_calm_since: float | None = None
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
        v_cap = min(e.v_max, self.calib.pan_max_fw(inp.zoom_pos))  # fw/s: не быстрее, чем может камера
        coast = not self.diverged
        pan = self.x.step(now, dt, comp.anchor[0], e.dead_x, e.soft_x, e, e.lead, active, k_now, zr, v_cap, coast)
        tilt = self.y.step(now, dt, comp.anchor[1], dead_y, e.soft_y, e, 0.0, active, k_now, zr,
                           min(e.v_max, self.calib.pan_max_fw(inp.zoom_pos, axis="tilt") / TILT_FROM_FH), coast)

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
        spread = max(self.x.spread(now, k_now) / max(e.dead_x, 1e-9), self.y.spread(now, k_now) / max(dead_y, 1e-9))
        if not active or spread >= (ZOOM_CALM_OUT if self._zoom_calm_since is not None else ZOOM_CALM_IN):
            self._zoom_calm_since = None
        elif self._zoom_calm_since is None:
            self._zoom_calm_since = now
        caps = ((self.x, v_cap, e.soft_x), (self.y, min(e.v_max, self.calib.pan_max_fw(inp.zoom_pos, axis="tilt") / TILT_FROM_FH), e.soft_y))
        # недостача: на сколько догон не успевает (прогноз ухода минус порог). В середине зоны (EDGE_ZOOM_ERR…
        # HARD_EDGE_ERR) — только если поворот на пределе (v_cmd ≥ SAT_FRAC·потолок), а ошибка растёт: иначе
        # камера справится поворотом сама. Человек уже у самого края — отъезд сразу, не дожидаясь разгона.
        shortfall = 0.0
        for ax, cap, soft in caps:
            a, thr = abs(ax.err), max(soft, EDGE_ZOOM_ERR)
            if a > HARD_EDGE_ERR:
                shortfall = max(shortfall, a - thr)
            elif a > thr and ax.escape > a and (abs(ax.v_cmd) >= SAT_FRAC * cap or ax.escape > ESCAPE_ERR):
                shortfall = max(shortfall, ax.escape - thr)
        zoom, size_err = self._zoom(now, dt, active, walking, e, inp.zoom_pos, shortfall)
        return FramingOutput(pan, tilt, zoom, state, (self.x.err, self.y.err), size_err)

    def image_motion(self, t0: float, t1: float) -> tuple[float, float, float]:
        """Как сама камера сдвинула картинку между кадрами, снятыми в t0 и t1 (моменты прихода кадров).

        (во сколько раз зум увеличил, сдвиг по x, сдвиг по y) в долях кадра: точка p кадра t0 стоит в кадре t1 на
        0,5 + r·(p − 0,5) + сдвиг. По отправленным камере командам (модель), не по картинке — для трекера людей.
        """
        a, b = t0 - self.video_latency, t1 - self.video_latency
        r = math.exp(self.zdyn.displacement(a, b))
        k = self._k(b)
        return r, -self.x.sign * k * self.x.dyn.displacement(a, b), -self.y.sign * k * self.y.dyn.displacement(a, b)

    def _k(self, t: float) -> float:
        """Во сколько раз картинка увеличена зумом с момента старта регулятора."""
        return math.exp(self.zdyn.position(t))

    def _zoom_limit_reached(self, now: float, zoom_pos: int) -> bool:
        """Дошёл ли зум (с учётом уже отправленного и тормозного пути) до TRACK_ZOOM_MAX. Защёлка: после остановки
        у предела наезд не трогается заново от дрожания #GZ — только когда зум отъехал на ZOOM_LIMIT_RELEASE."""
        ln = zoom_ln(zoom_pos) if self._gz_ref is None else self._gz_ref
        ln += self.zdyn.position(now) + self.zdyn.coast(now) + max(self._zoom_cmd, 0.0) * ZOOM_LIMIT_LEAD_S
        limit = zoom_ln(TRACK_ZOOM_MAX)
        if ln >= limit:
            self._zoom_capped = True
        elif ln < limit - ZOOM_LIMIT_RELEASE:
            self._zoom_capped = False
        return self._zoom_capped

    def _zoom(
        self, now: float, dt: float, active: bool, walking: bool, e: EffortProfile, zoom_pos: int,
        shortfall: float = 0.0,
    ) -> tuple[float, float | None]:
        want = 0.0
        size_err = None
        escaping = False
        if active:
            at_edge = shortfall > 0
            escaping = self.x.escape > ESCAPE_ERR and self.x.escape > abs(self.x.err)
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
                    calm = self._zoom_calm_since is not None and now - self._zoom_calm_since >= ZOOM_IN_AFTER_HOLD_S
                    if want > 0 and not calm:
                        want = 0.0
            if at_edge:
                # человек уходит из кадра, а поворот на пределе — шире, со скоростью по недостаче
                want = -e.zoom_max * EDGE_ZOOM * min(1.0, shortfall / EDGE_SHORTFALL)
            if escaping:
                miss = self.x.escape - ESCAPE_ERR  # прогноз ухода дальше ESCAPE_ERR — отъезд по недостаче
                want = min(want, -ESCAPE_ZOOM * min(1.0, miss / EDGE_SHORTFALL))
        elif any(ax.exit_cmd and now - ax.active_t <= LOST_COAST_S for ax in (self.x, self.y)) and not self.diverged:
            want, escaping = -ESCAPE_ZOOM, True  # ушёл за край и пропал: идём за ним и шире, чтобы снова увидеть
        capped = self._zoom_limit_reached(now, zoom_pos)
        if capped:
            # У предела наезда нет: идущий наезд гасится сразу (торможение учтено в прогнозе), даже если размер цели
            # уже в мёртвой зоне и want = 0 — иначе команда плавно спадает и зум ползёт за предел
            if want > 0:
                want = 0.0  # размер цели не достигается: у предела наезда дальше не едем
            self._zoom_cmd = min(self._zoom_cmd, 0.0)
        # у края диапазона объектива дальше не зумируем
        if (want < 0 and zoom_pos <= P.LENS_MIN + LENS_END_MARGIN) or (want > 0 and zoom_pos >= P.LENS_MAX - LENS_END_MARGIN):
            want = 0.0
        # человек идёт — не наезжаем. Подправка камерой за переминающимся (спокойный размах, нет упреждения) — не ходьба:
        # иначе зум останавливался и трогался в такт переминанию
        calm_zoom = self._zoom_calm_since is not None
        if any(ax.v_ff or ax.walked or (not calm_zoom and ax.mode == "moving" and abs(ax.v_img) >= START_V)
               for ax in (self.x, self.y)):
            want = min(want, 0.0)
        if walking and self._zoom_cmd > 0 and want <= 0:
            self._zoom_cmd = 0.0  # человек пошёл — наезд прекращаем сразу, без плавного спада
        # отъезд у края разгоняется быстрее наезда (но тоже плавно, без рывков 18↔50)
        faster = ESCAPE_ZOOM_ACCEL if escaping else EDGE_ZOOM_ACCEL
        acc = ZOOM_ACCEL * (faster if want < 0 and want < self._zoom_cmd else 1.0)
        if not active and not escaping:
            acc = max(acc, ESCAPE_ZOOM / LOST_STOP_S)  # без цели зум тоже встаёт не дольше чем за ~0,5 с
        self._zoom_cmd += _limit(want - self._zoom_cmd, acc * dt)
        if abs(self._zoom_cmd) < 1e-9:
            self._zoom_cmd = 0.0
        return self._zoom_cmd, size_err


CTRL_DT_DEFAULT = 0.04
