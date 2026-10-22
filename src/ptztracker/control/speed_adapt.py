"""Подстройка скорости во время слежения: таблица «код → скорость» сверяется с тем, как камера правда ехала.

Поворот: между ответами #APC (опрос раз в 0,5 с; отрезок — от одного из прошлых ответов до нового)
путь камеры в градусах сравнивается с путём,
который дали бы отправленные коды по таблице (через ту же модель разгона, что у регулятора: мёртвое
время + τ). Зум: так же между двумя ответами #GZ (ln масштаба). Отношение «правда / таблица» —
во сколько раз камера быстрее таблицы; поправка (Calibration.pan_scale/tilt_scale/zoom_scale) плавно
идёт к медиане последних отношений и не выходит за SCALE_MIN…SCALE_MAX.

Отрезок годится, только если камера на нём ехала (коды, двигающие камеру, — не меньше ADAPT_STEADY_S),
почти всё время в одну сторону, со скоростью модели на концах почти одной (ошибка разгона не сказывается)
и сдвинулась больше шума положения. Пока сторож раскачки сработал, поправка
не меняется. Здесь нет потоков и сети: вызывающий (Follower, виртуальный стенд) кормит события.
"""

from __future__ import annotations

import math
import statistics

from ptztracker.camera import he42
from ptztracker.camera import protocol as P
from ptztracker.control.camera_model import (
    SCALE_MAX,
    SCALE_MIN,
    Calibration,
    CameraDynamics,
    zoom_ln,
)

# Отрезок между ответами положения (не обязательно соседними): короче — шум; длиннее — камера за это
# время могла и постоять, и развернуться. Опрос раз в 0,5 с, но в очереди за поворотом ответ приходит и через 2 с.
ADAPT_SEGMENT_MIN_S, ADAPT_SEGMENT_MAX_S = 0.4, 3.0
# Отрезок годится, если камера на нём ехала: коды, правда двигающие камеру (pan/tilt ≥ ADAPT_MIN_FW_S, как
# у сторожа раскачки; зум ≥ ADAPT_MIN_LN_S), действовали не меньше ADAPT_STEADY_S, и по модели путь почти
# весь в одну сторону (чистый путь ≥ ADAPT_ONE_WAY полного). Величина кода может меняться: разгон и
# торможение учитывает модель (мёртвое время + τ), камера линейна по скорости. Один код 0,5 с подряд
# регулятор почти не держит (разгон 0,45 fw/s²) — см. DECISIONS.
ADAPT_STEADY_S = 0.5
ADAPT_MIN_FW_S = 0.03
ADAPT_MIN_LN_S = 0.02
ADAPT_ONE_WAY = 0.9
# Скорость модели на концах отрезка почти одна (разница ≤ ADAPT_END_TOL средней): ошибка мёртвого времени
# или τ на δ сдвигает путь на δ·(v_конец − v_начало), и на таких отрезках почти не сказывается.
ADAPT_END_TOL = 0.5
_PATH_STEP_S = 0.1  # шаг, которым считается полный путь модели на отрезке
# Камера правда сдвинулась: ±1 единица #APC на конце даёт ≤ 5% ошибки при 40 единицах (0,3°).
ADAPT_MIN_APC_UNITS = 40
# Зум сдвинулся: 0,05 ln ≈ 45 единиц #GZ (±1 единица — ≤ 5%).
ADAPT_MIN_ZOOM_LN = 0.05
# Поворот меряем, только пока зум стоит: скорость pan в долях кадра зависит от зума.
ADAPT_ZOOM_STEADY_LN = 0.03
# Постоянная времени поправки — по секундам годных отрезков (на ходьбе годна примерно половина, т. е. ~2 с
# по часам). Быстрее — успевает до раскачки при ×2; медленнее — меньше шума (см. DECISIONS).
ADAPT_TAU_S = 1.0
ADAPT_MEDIAN_N = 5  # поправка идёт к медиане последних отношений: один выброс её не уводит
_KEEP_CMDS_S = 5.0
AXES = ("pan", "tilt", "zoom")


def _hfov_at(zoom_pos: int) -> float:
    return he42.hfov_deg((zoom_pos - P.LENS_MIN) / (P.LENS_MAX - P.LENS_MIN))


class _Axis:
    """Одна ось: отправленные скорости по таблице (без поправки) и замеры положения той же единицы."""

    def __init__(self, dead_time: float, tau: float, min_move: float):
        self.dead_time, self.tau, self.min_move = dead_time, tau, min_move
        self.reset()

    def reset(self) -> None:
        self.dyn = CameraDynamics(self.dead_time, self.tau)
        self.cmds: list[tuple[float, float, bool]] = []  # (когда ушла, скорость по таблице, камера едет)
        self.marks: list[tuple[float, float]] = []  # замеры (момент, положение) после последнего учтённого

    def sent(self, t: float, v: float, moving: bool) -> None:
        if self.cmds and t < self.cmds[-1][0]:
            return  # модель разгона — только вперёд по времени
        self.dyn.command(t, v)
        self.cmds.append((t, v, moving))
        old = [i for i, (ts, _, _) in enumerate(self.cmds) if ts < t - _KEEP_CMDS_S]
        if old:
            del self.cmds[: old[-1]]  # одна команда старше окна остаётся: она действовала в его начале

    def _moving_time(self, t0: float, t1: float) -> float:
        """Сколько секунд в [t0, t1] действовали коды, двигающие камеру (отправлены на мёртвое время раньше)."""
        a, b = t0 - self.dead_time, t1 - self.dead_time
        total = 0.0
        for (ts, _, moving), nxt in zip(self.cmds, self.cmds[1:] + [(math.inf, 0.0, False)]):
            lo, hi = max(ts, a), min(nxt[0], b)
            if moving and hi > lo:
                total += hi - lo
        return total

    def _path(self, t0: float, t1: float) -> tuple[float, float]:
        """Путь модели на [t0, t1]: (чистый, полный — сумма модулей шагов _PATH_STEP_S)."""
        n = max(1, math.ceil((t1 - t0) / _PATH_STEP_S))
        xs = [self.dyn.position(t0 + (t1 - t0) * i / n) for i in range(n + 1)]
        return xs[-1] - xs[0], sum(abs(b - a) for a, b in zip(xs, xs[1:]))

    def _ratio(self, ta: float, xa: float, t: float, x: float) -> float | None:
        """ln «правда/таблица» на отрезке [ta, t] или None, если отрезок не годится."""
        if not self.cmds or self.cmds[0][0] > ta - self.dead_time:
            return None  # неизвестно, какой код действовал в начале отрезка
        dt = t - ta
        if dt < ADAPT_SEGMENT_MIN_S or self._moving_time(ta, t) < ADAPT_STEADY_S:
            return None
        net, gross = self._path(ta, t)
        if abs(self.dyn.velocity(t) - self.dyn.velocity(ta)) > ADAPT_END_TOL * abs(net) / dt:
            return None  # разгон или торможение на концах: неверные мёртвое время и τ дали бы ошибку
        moved, expected = abs(x - xa), abs(net)
        # Направление по #APC живьём не сверялось (может быть зеркальным) — сравниваем только величины;
        # путь почти весь в одну сторону, поэтому и камера ехала в одну сторону.
        if expected < ADAPT_ONE_WAY * gross or moved < self.min_move or expected < 0.5 * self.min_move:
            return None
        return math.log(moved / expected)

    def measured(self, t: float, x: float, usable: bool) -> tuple[float, float] | None:
        """Новый замер положения; (ln «правда/таблица», длина отрезка) или None, если годного отрезка нет.

        Отрезок кончается этим замером, а начинается одним из прошлых (после последнего учтённого, не дальше
        ADAPT_SEGMENT_MAX_S): берётся самый длинный годный — так в него попадают и разгон, и торможение.
        """
        if not usable:
            self.marks = []
        self.marks = [m for m in self.marks if t - m[0] <= ADAPT_SEGMENT_MAX_S]
        found = None
        for ta, xa in self.marks:
            r = self._ratio(ta, xa, t, x)
            if r is not None:
                found = (r, t - ta)
                break
        self.marks = [] if found else self.marks
        self.marks.append((t, x))
        return found


class SpeedAdapter:
    """Поправка скоростей камеры по ходу слежения; пишет её в calib (pan_scale, tilt_scale, zoom_scale)."""

    def __init__(self, calib: Calibration):
        self.calib = calib
        min_deg = ADAPT_MIN_APC_UNITS * he42.APC_DEG_PER_UNIT
        self._axes = {
            "pan": _Axis(calib.dead_time, calib.tau, min_deg),
            "tilt": _Axis(calib.dead_time, calib.tau, min_deg),
            "zoom": _Axis(calib.dead_time, calib.tau, ADAPT_MIN_ZOOM_LN),
        }
        self._ratios: dict[str, list[float]] = {a: [] for a in AXES}
        self._zoom_at_apc: int | None = None

    def reset(self) -> None:
        """Новое включение слежения: история команд и замеров заново, поправка остаётся."""
        for ax in self._axes.values():
            ax.reset()
        self._zoom_at_apc = None

    # --- события ----------------------------------------------------------

    def pt_sent(self, t: float, pan_code: int, tilt_code: int, zoom_pos: int) -> None:
        """В камеру ушла команда #PTS (момент отправки): скорости по таблице без поправки, в °/с."""
        hfov = _hfov_at(zoom_pos)
        for axis, code in (("pan", pan_code), ("tilt", tilt_code)):
            v = self.calib.pan.speed(code, zoom_pos)
            self._axes[axis].sent(t, v * hfov, abs(v) >= ADAPT_MIN_FW_S)

    def zoom_sent(self, t: float, code: int) -> None:
        r = self.calib.zoom.rate(code)
        self._axes["zoom"].sent(t, r, abs(r) >= ADAPT_MIN_LN_S)

    def pan_tilt_measured(self, t: float, pan: int, tilt: int, zoom_pos: int, frozen: bool = False) -> None:
        """Ответ #APC (единицы камеры) и положение зума в тот момент. frozen — сторож сработал."""
        z_prev, self._zoom_at_apc = self._zoom_at_apc, zoom_pos
        zoom_still = z_prev is not None and abs(zoom_ln(zoom_pos) - zoom_ln(z_prev)) <= ADAPT_ZOOM_STEADY_LN
        for axis, units in (("pan", pan), ("tilt", tilt)):
            r = self._axes[axis].measured(t, units * he42.APC_DEG_PER_UNIT, zoom_still and not frozen)
            if r is not None:
                self._apply(axis, *r)

    def zoom_measured(self, t: float, pos: int, frozen: bool = False) -> None:
        r = self._axes["zoom"].measured(t, zoom_ln(pos), not frozen)
        if r is not None:
            self._apply("zoom", *r)

    # --- поправка ---------------------------------------------------------

    def _apply(self, axis: str, log_ratio: float, dt: float) -> None:
        hist = self._ratios[axis]
        hist.append(max(math.log(SCALE_MIN), min(math.log(SCALE_MAX), log_ratio)))
        del hist[:-ADAPT_MEDIAN_N]
        cur = math.log(getattr(self.calib, f"{axis}_scale"))
        new = cur + (statistics.median(hist) - cur) * (1 - math.exp(-dt / ADAPT_TAU_S))
        setattr(self.calib, f"{axis}_scale", min(SCALE_MAX, max(SCALE_MIN, math.exp(new))))
