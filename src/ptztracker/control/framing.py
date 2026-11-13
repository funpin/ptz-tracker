"""Регулятор кадрирования: по положению человека решает, с какой скоростью вести pan/tilt/zoom.

Чистая функция шага (без потоков и сети), вызывается ~25 раз в секунду. Как оператор:
- различает «стоит, переминается» и «идёт» плавным весом (PersonEstimator): стоит — держит середину переминания, идёт — ведёт человека;
- за идущим едет с его скоростью и держит его чуть впереди точки — на столько, сколько камера проедет, пока узнает
  об остановке (v·T, control.timing): остановится — камера докатится до точки, а не проскочит;
- поправка положения растёт с отклонением по одной гладкой кривой (motion_law.speed_for_error), без ступеней;
- трогается и тормозит плавно: ускорение и рывок ограничены у движения самой камеры (модель разгона); сильнее —
  только когда иначе человек пересечёт границу зоны оператора (по времени до границы);
- учитывает, что кадр опаздывает: камера уже сдвинула картинку, пока кадр шёл по сети;
- зумирует вместе с поворотом: наезд замедляется, только когда поворот не успевал бы за человеком или мало места до края.

Модель зума (zdyn) идёт по командам, которые реально ушли в камеру (zoom_sent), и подправляется
по ответам #GZ (FramingInput.zoom_pos_t): зум может ждать в очереди за поворотом.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from typing import Literal

from ptztracker.camera import he42
from ptztracker.camera import protocol as P
from ptztracker.control.camera_model import Calibration, CameraDynamics, zoom_ln
from ptztracker.control.composition import BodyMeasure, Composition
from ptztracker.control.effort import EffortProfile
from ptztracker.control.motion_law import smoothstep, speed_for_error
from ptztracker.control.person_estimator import PersonEstimator
from ptztracker.control.size_filter import SizeFilter
from ptztracker.control.timing import FrameClock, late_knowledge

# Поворот ждёт в очереди планировщика (min_interval 0,13 с, последняя команда побеждает) в среднем половину
# интервала: модель pan/tilt регулятора считает это частью мёртвого времени. Без этого при разгоне τ 0,12 с
# модель опережала камеру на очередь, переминание казалось ходьбой, и «Резко» качалось при ρ ≥ 0,85 (DECISIONS).
# Модель зума идёт по командам, которые ушли (zoom_sent), — ей это не нужно.
PT_QUEUE_S = 0.065
BRAKE = 2.5  # тормозим твёрже, чем разгоняемся: ускорение и рывок торможения — в столько раз больше
# Идущего держим впереди точки (в сторону хода) на LEAD·v·T, где T — «позднее знание» (control.timing): столько камера
# проедет, пока узнает, что он встал. Не больше LEAD_ZONE_SHARE границы зоны: запас не должен сам выводить из зоны.
# 0,55 — «как на макете», решение оператора 2026-11-11: на спокойном шаге человек в 0,11–0,13 от точки, проскок на
# остановке 0,07–0,09 (при 0,25 — 0,07–0,09 от точки и проскок 0,10–0,12). При 0,8 человек уже в 0,16 от точки
# (пики 0,21 — у самой границы зоны 0,22), а обгон на случайных маршрутах тот же; при 0,45–0,5 камера, которая
# вчетверо быстрее своей таблицы, увозит человека к краю кадра (test_never_sustained_oscillation…) — DECISIONS
LEAD, LEAD_ZONE_SHARE = 0.55, 0.6
# Разгон сильнее характера — только чтобы человек не пересёк границу зоны: v_отн²/(2·место до границы), не больше
# A_CAMERA (доли кадра/с²: живая HE40 набирает скорость за 0,05–0,1 с); ROOM_MIN — места меньше не считаем (деление)
A_CAMERA, ROOM_MIN = 4.0, 0.005
# Команда поворота ждёт окна планировщика (его делят поворот, зум и опросы): на стенде между командами поворота
# 0,26 с в середине и 0,54 с у 10 % — настолько камера в среднем отвечает позже, чем считает её модель
REACT_EXTRA_S = 0.13
# Человек наполовину за боковым краем кадра: замеру высоты не верим (голова прикинута по рамке), но наклон не бросаем —
# столько секунд он едет с прежней скоростью идущего (проход наискось: стенд 2026-11-11, голова уходила ещё на 0,13)
FOLLOW_COAST_S = 1.0
# Скорость идущего для камеры сглажена (качание шага ±0,1–0,17 кадра/с не должно дёргать камеру), но упала ниже
# FOLLOW_STOP_RATIO от сглаженной — человек встал: камера тормозит сразу, не дожидаясь сглаживания
FOLLOW_SMOOTH_S, FOLLOW_FALL_S, FOLLOW_STOP_RATIO = 0.25, 0.01, 0.5
FIX_V_MAX = 0.2  # доли кадра/с
GAIN_NORMAL = 1.7  # e.gain характера «Обычно»: пределы поправки заданы для него
RETURN_V, RETURN_ZONE_FROM = 0.12, 0.6  # доли кадра/с; доля границы зоны, дальше которой предел растёт до FIX_V_MAX
URGENT_WALK_FROM, URGENT_WALK_TO = 0.3, 0.7  # вес «идёт»: срочный разгон — только за идущим
FIX_EDGE_FROM, FIX_EDGE_TO = 0.3, 0.42  # доли кадра от середины кадра
# Вес «идёт» меняется за один-два кадра, а середина шатания и сам человек бывают в 0,2–0,3 кадра друг от друга: цель по
# сырому весу прыгала, и камера дёргалась на десяток кодов (запись 2026-11-09). В цель идёт вес, сглаженный по времени.
AIM_BLEND_RISE_S, AIM_BLEND_FALL_S = 0.25, 0.45  # пошёл — быстрее, отпустил — медленнее: ложный «разворот» не срывает цель
REVERSE_PAUSE_S = 0.15  # камера остановилась и надо обратно: столько стоит, как оператор, — иначе проскок виден как отскок
REVERSE_EDGE_FROM, REVERSE_EDGE_TO = 0.2, 0.35  # доли кадра: человек дальше от метки — паузы нет
REVERSE_STOPPED_V = 0.03  # доли кадра/с: медленнее камера уже стоит
# Доводка: ближе этого (доли мёртвой зоны) камера не двигается вовсе, иначе вечно ползёт на нижних кодах
HOLD_FROM, HOLD_TO = 0.5, 0.9
# Доводку начинаем, когда она нужна не слабее самого малого кода камеры, и ведём, пока не станет слабее его половины.
# Без этого музыкант, стоящий в 0,6 покоя от точки, давал поправку 0,003–0,007 кадра/с — на самом пороге кода ±1
# (0,008 кадра/с на теле): код скакал 50 ↔ 51, камера трогалась и вставала по 23 раза в минуту по повороту и 32 по
# наклону (площадка 2026-11-13). Порог — только у доводки: скорость идущего и доворот при зуме идут как раньше.
FIX_START, FIX_STOP = 1.0, 0.5  # доли скорости самого малого кода
# И только когда середина переминания вышла из покоя целиком: при плотной зоне (покой ±0,026) доводка начиналась на
# 0,7 покоя — 1,8 % кадра, и камера поправлялась по 28 раз в минуту по повороту и 42 по наклону (площадка 2026-11-13,
# 20:22). Начав, доводит до HOLD_FROM, как раньше.
FIX_FROM_DEAD = 0.9  # 1,0 оставляло стоящего на 1,25 покоя от точки (test_tilt_follows_vertical: предел 1,2)
LATENCY_GAIN_REF = 0.3  # с: задержка видео больше — коэффициент обратной связи падает пропорционально (MJPEG 0,12–0,26 не трогаем)
# Отъезд «от края» — крайняя мера: только когда человек уходит из самого кадра — он ближе FRAME_EDGE_MARGIN к краю
# или окажется там раньше, чем камера его догонит (прогноз Axis.escape). Скорость — EDGE_ZOOM·zoom_max, полная при
# недостаче EDGE_SHORTFALL. Было: от 0,3–0,4 кадра от точки — на крупном плане отъезд на каждом быстром шаге («насос»).
FRAME_EDGE_MARGIN = 0.06
EDGE_ZOOM, EDGE_ZOOM_ACCEL = 1.5, 2.0
ESCAPE_ZOOM, ESCAPE_ZOOM_ACCEL = 0.6, 10.0  # уходит далеко за край кадра на глазах: шире быстрее, чем позволяет характер
# Пропавшего ищем спокойно: кадр расширяется со скоростью характера и не больше чем в SEARCH_WIDEN раз, а не нашли —
# крупность возвращается. Площадка 2026-11-13, вечер: в зале зрители, цель переходила на них и пропадала — камера
# каждый раз за 2 с уходила кодом 1 на полный общий план (3263 → 1365) и оставалась там; по наклону шла вниз кодами
# 29–40 (31461 → 29267). По наклону пропавшего больше не ищем: со сцены уходят вбок, а не вниз.
SEARCH_WIDEN = math.log(1.4)
EDGE_SHORTFALL = 0.08
# Отъезд «от края» начинается, когда уход из кадра виден на стольких замерах подряд: по одному замеру (цель на кадр
# перескочила на соседа) камера за 0,1 с уходила на отъезд кодом 1 (площадка 2026-11-13)
EDGE_CONFIRM_N = 2
Y_PREDICT_N = 4  # столько замеров подряд прогноз ухода по высоте должен держаться, чтобы по нему отъезжать
# Терпение зума: наезд — когда размер меньше нужного дольше ZOOM_IN_PATIENCE_S и человек в зоне; отъезд по размеру —
# через ZOOM_OUT_PATIENCE_S (крупный план режет голову — ждать меньше); после отъезда «от края» наезд — не раньше, чем
# человек ZOOM_CALM_AFTER_ESCAPE_S спокойно в зоне.
ZOOM_IN_PATIENCE_S, ZOOM_OUT_PATIENCE_S, ZOOM_CALM_AFTER_ESCAPE_S = 2.0, 0.7, 12.0
# Сторож раскачки: столько настоящих разворотов за окно — стоп и выключение. Часть 0 (B3): 61% прежних
# «разворотов» — при команде, на которой камера стоит, 81% — во время зума; правила — в SwingWatch.
WATCHDOG_REVERSALS, WATCHDOG_WINDOW_S = 4, 6.0  # было 3: случайные маршруты (сменил направление, сильно шатается) давали ложные срабатывания
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
LOST_SEARCH_V = 0.35  # доли кадра/с: не медленнее этого ищем пропавшего
# Пропавшего ищем, только если он был за границей зоны столько замеров подряд. Площадка 2026-11-13: цель на один кадр
# перескочила на другого человека и пропала — камера 5 с шла вправо и вниз кодами 63 и 40 и отъехала на общий план
# («ушла вниз»).
EXIT_CONFIRM_N = 2
LOST_COAST_S = 2.5  # человек ушёл за край быстрее камеры и пропал — столько ещё идём за ним и расширяем кадр, пока не найдём
STILL_V = 0.02  # медленнее — считаем, что человек стоит
MOVING_CMD, MOVING_WALK = 0.01, 0.3  # «движение» для состояния: команда быстрее этого или вес «идёт» больше этого
NO_TARGET_S = 0.3
ZOOM_ACCEL = 0.5  # ln/с²
# Наезд на ходу: множитель скорости от запаса до края (ошибка + куда уйдёт, пока камера ответит) и от того,
# успеет ли поворот, когда картинка станет крупнее (скорость человека в кадре ÷ потолок поворота)
ZOOM_ROOM_FROM, ZOOM_ROOM_TO = 0.18, 0.34
ZOOM_SPEED_FROM, ZOOM_SPEED_TO = 0.55, 0.9
ZOOM_WALK_SLOWDOWN = 0.6  # пока человек идёт, наезд в 2,5 раза медленнее: картинка крупнеет — поворот должен успевать
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
        self.person = PersonEstimator()
        self.reset()

    def reset(self) -> None:
        self.person.reset()
        self.sent_model = False  # модель поворота идёт по ушедшим в камеру командам (FramingController.pt_sent)
        self.mode = "holding"
        self.v_cmd = 0.0
        self.a_cmd = 0.0  # ускорение камеры по модели (fw/s²), которое задаёт команда: по нему — рывок
        self.v_follow = 0.0  # скорость идущего, с которой едет камера (доли кадра/с): без качания шага
        self.aim_w = 0.0  # вес «идёт» для цели: тот же, но без скачков
        self.went = 0  # куда камера ехала в последний раз (+1/−1, 0 — ещё не ехала)
        self.stopped_t: float | None = None  # с какого момента стоит (None — едет)
        self.v_img = 0.0  # скорость человека в кадре при текущем зуме
        self.lead_now = 0.0  # запас: на столько идущий сейчас держится впереди точки (доли кадра, со знаком хода)
        self.a_lim = 0.0  # разрешённое сейчас ускорение камеры (характер или больше — по времени до границы зоны)
        self.a_brake = 0.0  # разрешённое торможение (характер или больше — чтобы не проехать мимо человека)
        self.err = 0.0  # отклонение цели от метки
        self.reach = 0.0  # |отклонение самого человека от метки|: переминание у края важно, хотя цель — середина
        self.off = 0.0  # насколько человек далеко от середины кадра (0,5 — у самого края)
        self.zone = 1.0  # граница зоны оператора по этой оси на последнем такте
        self.escape = 0.0  # прогноз наибольшей ошибки, пока камера догоняет (доли кадра)
        self.exit_cmd = 0.0  # команда «за уходящим за край», если он пропадёт
        self.fixing = False  # доводка идёт (FIX_START / FIX_STOP)
        self._out_t: float | None = None  # замер, по которому в последний раз считали _out_n
        self._out_n = 0  # сколько замеров подряд человек за границей зоны
        self.active_t = 0.0
        self.watch.reset()

    def _cam(self, t: float) -> float:
        return self.dyn.position(t)

    def measure(self, img: float, t_shot: float, k_shot: float, zone: float = 1.0) -> None:
        # «мировая» позиция: что было бы без поворота камеры и без зума; zone — размер зоны оператора по этой оси
        self.person.update(t_shot, (img - 0.5) / k_shot + self.sign * self._cam(t_shot), k_shot, zone)

    def _ramp(self, want: float, dt: float, a_max: float, j_max: float, now: float, k_now: float) -> None:
        """Команда к want так, чтобы ускорение камеры по модели ((команда − скорость)/τ) было не больше a_max
        (торможение — BRAKE·a_max), а его изменение — не больше j_max. Плавность — у движения самой камеры, а не
        у команды."""
        tau = max(0.05, self.dyn.tau)
        v_m = self.dyn.velocity(now + self.dyn.dead_time) * k_now  # скорость камеры, когда команда дойдёт
        dv = want - v_m
        if v_m * dv < 0:
            a_lim = max(BRAKE * a_max, self.a_brake)
            j = j_max * a_lim / a_max
        else:
            a_lim, j = a_max, j_max
        # сколько скорость ещё изменится, пока нынешнее ускорение гаснет с рывком j, — это уже «в пути»
        rest = dv - self.a_cmd * abs(self.a_cmd) / (2 * j)
        a_des = math.copysign(min(a_lim, math.sqrt(2 * j * abs(rest))), rest)
        u = v_m + (self.a_cmd + _limit(a_des - self.a_cmd, j * dt)) * tau
        u = max(min(v_m, want), min(max(v_m, want), u))  # не дальше want и не назад от него
        if abs(want) < 1e-4 and abs(u) < 1e-3:
            u = 0.0  # камера и так стоит (код 50): без «хвоста» команды
        self.a_cmd = (u - v_m) / tau
        self.v_cmd = u

    def _after_pause(self, want: float, now: float, k_now: float) -> float:
        """Обратно — только после остановки и короткой паузы: так оператор гасит проскок. Человек у края — без паузы."""
        v_m = self.dyn.velocity(now + self.dyn.dead_time) * k_now
        if abs(v_m) > REVERSE_STOPPED_V:
            self.went, self.stopped_t = (1 if v_m > 0 else -1), None
        elif self.stopped_t is None:
            self.stopped_t = now
        if self.went == 0 or want * self.went >= 0:
            return want
        pause = REVERSE_PAUSE_S * (1 - smoothstep(self.reach, REVERSE_EDGE_FROM, REVERSE_EDGE_TO))
        return 0.0 if self.stopped_t is None or now - self.stopped_t < pause else want

    def step(
        self,
        now: float,
        dt: float,
        anchor: float,
        dead: float,
        zone: float,  # полуразмер границы зоны оператора по этой оси, доли кадра
        e: EffortProfile,
        t_late: float,  # «позднее знание» T, с
        active: bool,
        k_now: float = 1.0,
        zoom_rate: float = 0.0,
        v_cap: float | None = None,
        coast: bool = False,
        walk_hint: float = 0.0,  # вес «идёт» по другой оси: идёт человек, а не ось
        keep_follow: bool = False,  # замера по этой оси нет, но человека ведёт другая ось: недолго едем, как ехали
        watch: tuple[float, float] | None = None,  # пороги сторожа раскачки (граница, покой); None — zone и dead
        v_floor: float = 0.0,  # скорость самого малого кода камеры на этом зуме, доли кадра/с (0 — порога доводки нет)
    ) -> float:
        v_cap = e.v_max if v_cap is None else v_cap
        p = self.person
        self.a_lim, self.a_brake = e.a_max, BRAKE * e.a_max
        overrun = False  # камера проехала человека и ещё едет от него
        if active and p.t is not None:
            w = max(p.walk, walk_hint)
            self.aim_w += (w - self.aim_w) * (1 - math.exp(-dt / (AIM_BLEND_RISE_S if w > self.aim_w else AIM_BLEND_FALL_S)))
            self.v_img = p.vel * k_now
            moving_img = self.v_img if abs(self.v_img) >= STILL_V else 0.0
            pred = p.pos + p.vel * (now - p.t)
            aim = (1 - self.aim_w) * p.center + self.aim_w * pred  # стоит — середина переминания, идёт — сам человек
            shift = -self.sign * self._cam(now)
            img_aim = 0.5 + (aim + shift) * k_now
            img_person = 0.5 + (pred + shift) * k_now
            self.err = img_aim - anchor
            self.reach = abs(img_person - anchor)
            self.off, self.zone = abs(img_person - 0.5), zone
            a = max(abs(self.err), self.reach)
            t_resp = self.dyn.dead_time + self.dyn.tau  # за столько камера отвечает на команду
            v_w = w * moving_img  # скорость идущего в кадре
            cam_img = self.sign * self.dyn.velocity(now) * k_now  # камера в тех же единицах и с тем же знаком, что человек
            v_rel = moving_img - cam_img  # человек относительно камеры
            t_react = t_resp + REACT_EXTRA_S
            # Ускорение — по времени до границы зоны: сколько нужно, чтобы человек её не пересёк. Обычно хватает
            # плавности характера; человек у границы и идёт к ней — больше, вплоть до того, что может камера.
            if cam_img * self.err < 0 and abs(cam_img) > REVERSE_STOPPED_V:
                overrun = True
            elif v_rel * self.err > 0:
                room = max(zone - self.reach - abs(v_rel) * t_react, ROOM_MIN)
                need = min(v_rel * v_rel / (2 * room), A_CAMERA)
                # только за идущим: тот, кто шатается у границы, сам вернётся — рывок за ним лишний
                self.a_lim = max(e.a_max, e.a_max + (need - e.a_max) * smoothstep(w, URGENT_WALK_FROM, URGENT_WALK_TO))
            elif v_rel * self.err < 0:
                # камера нагоняет человека: тормозить так, чтобы не проехать мимо него (он встал — это видно поздно)
                room = max(abs(self.err) - abs(v_rel) * t_react, ROOM_MIN)
                self.a_brake = max(BRAKE * e.a_max, min(v_rel * v_rel / (2 * room), A_CAMERA))
            # Скорость идущего для камеры: растёт плавно (качание шага), но времени до границы мало — сразу
            stopped = abs(v_w) < FOLLOW_STOP_RATIO * abs(self.v_follow) or v_w * self.v_follow < 0
            smooth = FOLLOW_SMOOTH_S * e.a_max / self.a_lim
            self.v_follow += (v_w - self.v_follow) * (1 - math.exp(-dt / (FOLLOW_FALL_S if stopped else smooth)))
            if abs(self.v_follow) < (STILL_V if stopped else 1e-4):
                self.v_follow = 0.0  # встал: без «хвоста» из малых кодов — каждый занял бы окно перед стопом
            # Запас: идущий — впереди точки на столько, сколько камера проедет, пока узнает, что он встал
            self.lead_now = _limit(LEAD * self.v_follow * t_late, LEAD_ZONE_SHARE * zone)
            x = self.err - self.lead_now  # что выбирает поправка
            # Куда уйдёт человек, пока камера его догоняет: v·(мёртвое время + τ) + v²/(2·a) по скорости
            # относительно камеры — по этому прогнозу зум решает, отъезжать ли.
            self.escape = a + abs(v_rel) * t_resp + v_rel * v_rel / (2 * BRAKE * e.a_max) if v_rel * self.err > 0 else a
            # Поправка положения — по гладкой кривой, но не быстрее, чем камера успеет затормозить на оставшемся пути
            # доводка в покое: совсем рядом с точкой стоящего не двигаем; идущего ведём без этой полосы — камера и так едет
            hold = smoothstep(abs(x), HOLD_FROM * dead, HOLD_TO * dead)
            hold += (1 - hold) * smoothstep(w, 0.5, 0.9)
            v_fix = min(abs(speed_for_error(x, dead, zone, e.gain, v_cap)), math.sqrt(2 * BRAKE * self.a_lim * abs(x)))
            # Поправка — не быстрее FIX_V_MAX: лишняя скорость камеры на остановке человека превращается в проскок,
            # а возврат после проскока виден как отскок. Человек у края кадра — без этого предела.
            # Стоящего в зоне подводим к точке медленно (RETURN_V): после проскока это не отскок, а спокойная доводка.
            # Поправка против хода камеры — это торможение: её не ограничиваем (камера не должна проехать человека).
            # Оба предела — для «Обычно»; «Мягко … Резко» меняет их вместе с крутизной кривой (e.gain).
            off_centre = abs(img_person - 0.5)
            char = e.gain / GAIN_NORMAL
            calm = char * (RETURN_V + (FIX_V_MAX - RETURN_V) * max(w, smoothstep(self.reach, RETURN_ZONE_FROM * zone, zone)))
            fix_cap = calm + (v_cap - calm) * smoothstep(off_centre, FIX_EDGE_FROM, FIX_EDGE_TO)
            if x * cam_img < 0 and abs(cam_img) > REVERSE_STOPPED_V:
                fix_cap = max(fix_cap, abs(cam_img))
            v_fix = min(v_fix, fix_cap)
            fix = hold * v_fix
            if self.fixing:
                self.fixing = fix >= FIX_STOP * v_floor
            else:  # с места — только когда середина переминания вышла из покоя: внутри покоя камера стоит
                # (идёт зум — доводим сразу: наезд сам сдвигает человека в кадре, ждать выхода из покоя нельзя)
                self.fixing = fix >= FIX_START * v_floor and (abs(x) > FIX_FROM_DEAD * dead or w > 0.5 or not v_floor
                                                              or abs(zoom_rate) > 1e-3)
            if not self.fixing:
                fix = 0.0
            want = self.sign * (self.v_follow + math.copysign(fix, x))
            if p.settling:  # цель скачком оказалась в новом месте (person_estimator.SETTLE_S): пока не продержится — стоим
                want, self.v_follow = 0.0, 0.0
                self.escape = a
            self.mode = "moving" if abs(want) > MOVING_CMD or abs(self.v_cmd) > MOVING_CMD or w > MOVING_WALK else "holding"
            # Наезд/отъезд сдвигает человека в кадре — доворачиваем одновременно, как оператор. Но если человек
            # уходит за край, а доворот при отъезде повёл бы камеру от него — не доворачиваем: отъезд сам
            # вернёт его к центру.
            zoom_feed = self.sign * (img_aim - 0.5) * zoom_rate
            if zoom_rate < 0 and a > zone and zoom_feed * self.sign * self.err < 0:
                zoom_feed = 0.0
            want += zoom_feed
            # Человек у края кадра: если он пропадёт, камера ещё LOST_COAST_S идёт в его сторону и шире — он снова
            # появляется в кадре, а не теряется насовсем.
            # Туда, где его видели в последний раз: и если он ушёл, и если это камера проскочила мимо остановившегося.
            side = img_person - anchor
            # Ищем того, кто был за границей зоны на нескольких замерах подряд: идёт он сам или это камера проскочила
            # мимо стоящего — не важно. Цель, на один кадр перескочившая на соседа, сюда не попадает; сбой в один
            # замер отсекает оценка человека, прыжок цели — settling.
            if p.t != self._out_t:
                self._out_t, self._out_n = p.t, (self._out_n + 1 if a > zone else 0)
            leaving = a > zone and self._out_n >= EXIT_CONFIRM_N and not p.settling
            self.exit_cmd = self.sign * math.copysign(max(abs(v_w), LOST_SEARCH_V), side) if leaving else 0.0
            self.active_t = now
        elif coast and self.exit_cmd and now - self.active_t <= LOST_COAST_S:
            want, zoom_feed = self.exit_cmd, 0.0
            self.v_follow = 0.0
        elif keep_follow and self.v_follow and now - self.active_t <= FOLLOW_COAST_S:
            want, zoom_feed = self.sign * self.v_follow, 0.0
        else:
            want = zoom_feed = 0.0
            self.exit_cmd = 0.0
            self.v_follow = 0.0
        want = _limit(want, v_cap)
        if active or (keep_follow and want):
            want = self._after_pause(want, now, k_now)
            if overrun and want * self.v_cmd <= 0.0:
                # Стоп одной командой: плавное торможение — это несколько команд с убывающей скоростью, каждая ждёт
                # своего окна (0,13 с), и камера уезжает от человека всё это время. Плавность даёт сама камера (разгон τ).
                self.v_cmd = self.a_cmd = 0.0
            elif overrun and abs(want) < abs(self.v_cmd):
                self.v_cmd, self.a_cmd = want, 0.0  # и сбросить скорость до нужной — тоже сразу
            else:
                self._ramp(want, dt, self.a_lim, e.j_max * self.a_lim / e.a_max, now, k_now)
        else:
            # без цели — останавливаемся быстрее (за ~0.5 с), с торможением камеры BRAKE·a_max
            a_lim = max(BRAKE * e.a_max, abs(self.v_cmd) / LOST_STOP_S)
            self.v_cmd += _limit(want - self.v_cmd, a_lim * dt)
            self.a_cmd = 0.0
        if abs(self.v_cmd) < 1e-9:
            self.v_cmd = self.a_cmd = 0.0
        self.v_cmd = float(self.v_cmd)  # из ключевых точек приходят numpy-числа
        target = None if not active or p.t is None else (p.pos + p.vel * (now - p.t)) * k_now
        w_soft, w_dead = watch if watch is not None else (zone, dead)
        self.watch.update(now, self.v_cmd, self.err, w_soft, float(zoom_feed), target, w_dead)
        if not self.sent_model:
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
        self._t_seen_y: float | None = None  # последний замер, которому верим по вертикали (видны оба плеча)
        self._unsure = False  # последний замер — прикидка по рамке (BodyMeasure.confident = False)
        self._last_cap: float | None = None
        self.clock = FrameClock()  # как часто на самом деле приходят замеры
        self.t_late = self._late()  # «позднее знание» T, с: за столько камера узнаёт о старте и остановке и отвечает
        self._u: float | None = None  # размер человека при нынешнем зуме (после фильтра размера)
        self._u_t: float | None = None
        self.size = SizeFilter()  # размер без зума: его спад — это человек (встал боком), а не отъезд камеры
        self._zoom_cmd = 0.0
        self._zoom_active = False
        self._zoom_capped = False  # наезд остановлен у TRACK_ZOOM_MAX (защёлка с запасом ZOOM_LIMIT_RELEASE)
        self._size_side = 0  # +1 — человек крупнее нужного, −1 — мельче, 0 — размер в мёртвой зоне
        self._size_t: float | None = None  # с какого момента размер не тот в эту сторону
        self._calm_s = 0.0  # сколько секунд подряд человек в зоне (после отъезда «от края» наезд ждёт этого покоя)
        self._escaped = False  # был отъезд «от края»: наезд ждёт ZOOM_CALM_AFTER_ESCAPE_S покоя
        self._meas_n = 0  # счётчик замеров
        self._y_pred_meas, self._y_pred_n = -1, 0  # прогноз ухода по высоте: на каком замере считали и сколько подряд
        self._search_ln0: float | None = None  # зум (ln) в момент, когда начали искать пропавшего
        self._edge_n0: int | None = None  # с какого замера человек «уходит из кадра» без перерыва
        self._gz_t: float | None = None  # момент последнего учтённого ответа #GZ
        self._gz_ref: float | None = None  # ln масштаба (по #GZ), которому соответствует k = 1
        self._apc_t: float | None = None  # момент последнего учтённого ответа #APC
        self.diverged = False

    def _late(self) -> float:
        return late_knowledge(self.video_latency, self.clock.meas_dt, self.calib.dead_time + self.calib.tau)

    def zoom_sent(self, t: float, rate: float) -> None:
        """Команда зума (ln/с) ушла в камеру в момент t: модель зума идёт по отправленному, а не по решённому."""
        self.zdyn.command(t, rate)

    def pt_sent(self, t: float, pan: int, tilt: int, zoom_pos: int) -> None:
        """Коды поворота и наклона ушли в камеру в момент t: модель идёт по настоящей отправке и настоящей скорости кода.

        Очередь планировщика меняется от 0 до нескольких окон (поворот делит их с зумом и опросами), и модель,
        ждущая среднюю очередь, опережала камеру: на ходу это ошибка положения человека «в мире».
        Масштаб берётся на момент последнего #GZ: скорость кода посчитана по этому же положению зума.
        """
        k = self._k(self._gz_t if self._gz_t is not None else t)
        for ax, code, axis, to_axis in ((self.x, pan, "pan", 1.0), (self.y, tilt, "tilt", 1 / TILT_FROM_FH)):
            if not ax.sent_model:
                ax.sent_model = True
                ax.dyn.dead_time = self.calib.dead_time  # очередь больше не угадываем: момент отправки известен
                ax.dyn.forget_after(t)  # решённые, но не ушедшие команды: иначе список команд потеряет порядок
            ax.dyn.command(t, self.calib.pan_fw(code, zoom_pos, axis) * to_axis / k)

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
        if self.video_latency > LATENCY_GAIN_REF:  # чем дольше идёт кадр (H.264), тем мягче обратная связь: иначе раскачка
            e = dataclasses.replace(e, gain=e.gain * LATENCY_GAIN_REF / self.video_latency)
        if inp.zoom_pos_t is not None and inp.zoom_pos_t != self._gz_t:
            self._gz_t = inp.zoom_pos_t
            self._zoom_measured(inp.zoom_pos_t, inp.zoom_pos)
        if inp.pan_tilt is not None and inp.pan_tilt_t is not None and inp.pan_tilt_t != self._apc_t:
            self._apc_t = inp.pan_tilt_t  # сторож раскачки сверяет развороты с настоящими
            self.x.watch.camera_at(inp.pan_tilt_t, inp.pan_tilt[0])
            self.y.watch.camera_at(inp.pan_tilt_t, inp.pan_tilt[1])

        if inp.measure is not None and inp.t_capture is not None and inp.t_capture != self._last_cap:
            self._last_cap = inp.t_capture
            self._meas_n += 1
            self.clock.note(inp.t_capture)
            self.t_late = self._late()
            self._t_seen = now
            t_shot = inp.t_capture - self.video_latency
            k_shot = self._k(t_shot)
            self.x.measure(inp.measure.nose[0], t_shot, k_shot, e.zone_w)
            # Плеч не видно (человек срезан краем кадра): голова прикинута по рамке и по вертикали бывает на треть кадра
            # мимо (запись F9 2026-11-10: камера ныряла вниз). Такой замер ведёт только поворот — к человеку.
            self._unsure = not inp.measure.confident
            if inp.measure.confident:
                self.y.measure(inp.measure.nose[1], t_shot, k_shot, e.zone_h)
                self._t_seen_y = now
            if inp.measure.u is not None and inp.measure.confident:
                # размер «сейчас»: за время в пути кадра зум мог измениться
                u_now = inp.measure.u * math.exp(self.zdyn.displacement(t_shot, now))
                k = self._k(now)
                self._u = self.size.update(now, u_now / k) * k  # фильтр — в единицах без зума
                self._u_t = now

        if not self.diverged and (self.x.watch.swinging or self.y.watch.swinging):
            self.diverged = True  # раскачка — дальше только остановка, до reset()
        active = (self._t_seen is not None and now - self._t_seen <= NO_TARGET_S) and not self.diverged
        dead_y = e.dead_y * inp.aspect / (16 / 9)
        k_now = self._k(now)
        zr = self.zdyn.velocity(now)
        v_cap = min(e.v_max, self.calib.pan_max_fw(inp.zoom_pos))  # fw/s: не быстрее, чем может камера
        coast = not self.diverged
        # наклон ведём, только пока есть замер, которому верим по вертикали; человек виден, но срезан краем — наклон стоит
        active_y = active and self._t_seen_y is not None and now - self._t_seen_y <= NO_TARGET_S
        ratio = inp.aspect / (16 / 9)
        watch_x = (e.watch_soft_x or e.soft_x, e.watch_dead_x or e.dead_x)
        watch_y = (e.watch_soft_y or e.soft_y, (e.watch_dead_y or e.dead_y) * ratio)
        # скорость самого малого кода на этом зуме: слабее неё доводка не начинается (FIX_START)
        floor_x = abs(self.calib.pan_fw(51, inp.zoom_pos))
        floor_y = abs(self.calib.pan_fw(51, inp.zoom_pos, axis="tilt")) / TILT_FROM_FH
        pan = self.x.step(now, dt, comp.anchor[0], e.dead_x, e.soft_x, e, self.t_late, active, k_now, zr, v_cap, coast,
                          watch=watch_x, v_floor=floor_x)
        # наклон ведёт голову, как только поворот увидел ходьбу: на проходе наискось уход по высоте медленный и сам
        # на ходьбу долго не похож (стенд 2026-11-11: голова уходила на 0,35–0,75 высоты кадра)
        tilt = self.y.step(now, dt, comp.anchor[1], dead_y, e.soft_y, e, self.t_late, active_y, k_now, zr,
                           min(e.v_max, self.calib.pan_max_fw(inp.zoom_pos, axis="tilt") / TILT_FROM_FH),
                           False, walk_hint=self.x.person.walk,  # по наклону пропавшего не ищем
                           keep_follow=active and self._unsure, watch=watch_y, v_floor=floor_y)
        if not active_y:
            self.y.mode = "holding"

        if self.diverged:
            state = "diverged"
            self.x.mode = self.y.mode = "holding"
        elif not active:
            state = "idle"
            self.x.mode = self.y.mode = "holding"
        elif self.x.mode == "moving" or self.y.mode == "moving":
            state = "moving"
        else:
            state = "holding"

        caps = ((self.x, v_cap, e.soft_x), (self.y, min(e.v_max, self.calib.pan_max_fw(inp.zoom_pos, axis="tilt") / TILT_FROM_FH), e.soft_y))
        # недостача: на сколько человек выходит (или выйдет, пока камера его догоняет) за край кадра без
        # FRAME_EDGE_MARGIN
        shortfall = 0.0
        edge = 0.5 - FRAME_EDGE_MARGIN
        for ax, _cap, _soft in caps:
            if ax is self.y and not active_y:
                continue  # замеру высоты не верим — по нему не отъезжаем
            ahead = max(0.0, ax.escape - max(abs(ax.err), ax.reach))  # столько он ещё пройдёт
            off = ax.off
            if ax is self.y:
                # По высоте прогнозу верим, только когда он держится Y_PREDICT_N замеров подряд; до тех пор — тому, что
                # видно. Скорость головы по вертикали у выступающего скачет на 0,5–0,8 кадра/с (наклонился, поднял
                # микрофон, ключевые точки «гуляют»), и прогноз «уйдёт за край» давал отъезд кодом 1 при человеке в
                # центре кадра (площадка 2026-11-13, вечер: 6 раз за 5 минут). Проход наискось длится дольше.
                predicted = ax.off > edge or ahead > 0
                if self._meas_n != self._y_pred_meas:
                    self._y_pred_meas, self._y_pred_n = self._meas_n, (self._y_pred_n + 1 if predicted else 0)
                if self._y_pred_n < Y_PREDICT_N:
                    ahead, off = 0.0, abs((ax.person.pos - ax.sign * ax._cam(now)) * k_now)
            if off > edge:
                shortfall = max(shortfall, off - edge + ahead)
            elif ahead > 0:
                shortfall = max(shortfall, off + ahead - edge)
        if self.x.person.settling or self.y.person.settling:
            shortfall = 0.0  # цель только что скачком сменила место: по ней не отъезжаем
        zoom, size_err = self._zoom(now, dt, active, e, inp.zoom_pos, shortfall, caps)
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
        ln = max(ln, zoom_ln(zoom_pos))  # камера уже на пределе по #GZ: наезд теперь идёт и на ходу, модель могла отстать
        limit = zoom_ln(TRACK_ZOOM_MAX)
        if ln >= limit:
            self._zoom_capped = True
        elif ln < limit - ZOOM_LIMIT_RELEASE:
            self._zoom_capped = False
        return self._zoom_capped

    def _zoom_room(self, caps) -> float:
        """Насколько можно наезжать (0…1): мало места до края или поворот на этом зуме не успевал бы — медленнее."""
        room = 1.0
        for ax, cap, _soft in caps:
            t_resp = ax.dyn.dead_time + ax.dyn.tau
            near_edge = max(abs(ax.err), ax.reach) + abs(ax.v_img) * ax.person.walk * t_resp
            room = min(room, 1.0 - smoothstep(near_edge, ZOOM_ROOM_FROM, ZOOM_ROOM_TO))
            room = min(room, 1.0 - smoothstep(abs(ax.v_img) * ax.person.walk / max(cap, 1e-6), ZOOM_SPEED_FROM, ZOOM_SPEED_TO))
        return room

    def _zoom(
        self, now: float, dt: float, active: bool, e: EffortProfile, zoom_pos: int, shortfall: float, caps,
    ) -> tuple[float, float | None]:
        want = 0.0
        size_err = None
        escaping = False
        if shortfall <= 0:
            self._edge_n0 = None
        elif self._edge_n0 is None:
            self._edge_n0 = self._meas_n
        if active:
            at_edge = shortfall > 0 and self._meas_n - self._edge_n0 >= EDGE_CONFIRM_N - 1
            inside = all(max(abs(ax.err), ax.reach) <= ax.zone for ax, _cap, _soft in caps)
            if shortfall > 0:  # наезд придерживаем по первому же признаку ухода, как раньше; отъезжаем — по подтверждению
                self._escaped, self._calm_s = True, 0.0
            else:
                self._calm_s = self._calm_s + dt if inside else 0.0  # вышел из зоны — покой считается заново
            if self._escaped and self._calm_s >= ZOOM_CALM_AFTER_ESCAPE_S:
                self._escaped = False
            if self._u is not None and self._u > 0:
                size_err = math.log(self._u / self.composition.u_frac)
                a = abs(size_err)
                side = (1 if size_err > 0 else -1) if a > e.zoom_dead else (0 if a < 0.5 * e.zoom_dead else self._size_side)
                if side != self._size_side:
                    self._size_side, self._size_t = side, (now if side else None)
                if not side:
                    self._zoom_active = False
                elif not self._zoom_active and self._size_t is not None:
                    patience = ZOOM_OUT_PATIENCE_S if side > 0 else ZOOM_IN_PATIENCE_S
                    self._zoom_active = now - self._size_t >= patience
                if self._zoom_active:
                    want = _limit(-e.zoom_gain * size_err, e.zoom_max)
                    if want > 0:
                        # наезд — только пока человек в зоне и не сразу после отъезда «от края»
                        if not inside or self._escaped:
                            want = 0.0
                        want *= self._zoom_room(caps) * (1 - ZOOM_WALK_SLOWDOWN * max(self.x.person.walk, self.y.person.walk))
            if self._unsure and want > 0:
                want = 0.0  # размер по прикидке не меряется: наезд по старому размеру не продолжаем (отъезд у края — можно)
            if at_edge:
                # человек уходит из кадра, а поворот на пределе — шире, со скоростью по недостаче
                want = -e.zoom_max * EDGE_ZOOM * min(1.0, shortfall / EDGE_SHORTFALL)
                if shortfall > EDGE_SHORTFALL:  # уйдёт далеко за край: быстрее, чем позволяет характер
                    escaping = True
                    want = min(want, -ESCAPE_ZOOM * min(1.0, shortfall / EDGE_SHORTFALL - 1.0))
        elif any(ax.exit_cmd and now - ax.active_t <= LOST_COAST_S for ax in (self.x, self.y)) and not self.diverged:
            # ушёл за край и пропал: идём за ним и шире, чтобы снова увидеть, — спокойно и не дальше SEARCH_WIDEN
            if self._search_ln0 is None:
                self._search_ln0 = self.zdyn.position(now)
            if self._search_ln0 - self.zdyn.position(now) < SEARCH_WIDEN:
                want = -e.zoom_max * EDGE_ZOOM
            self._escaped, self._calm_s = True, 0.0
        elif self._search_ln0 is not None and not self.diverged:
            # не нашли: возвращаем крупность, с которой начали искать, — на общем плане без цели не остаёмся
            if self.zdyn.position(now) < self._search_ln0 - 0.02:
                want = e.zoom_max
            else:
                self._search_ln0 = None
        if active:
            self._search_ln0 = None  # нашёлся: дальше крупностью занимается обычный закон
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
