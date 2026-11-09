"""Что делает человек по одной оси: где он, как быстро идёт, вокруг какой точки переминается.

Камере важна не каждая точка, а смысл движения. Вес walk от 0 («стоит, переминается») до 1 («идёт») плавный:
регулятор смешивает по нему цель, поэтому между «стоит» и «идёт» нет переключателя и рывка.

Координаты — «мировые» (не зависят от зума и поворота камеры), пороги и размах — в долях кадра при текущем масштабе k.
"""

from __future__ import annotations

import math

from ptztracker.control.motion_law import smoothstep

# Положение — фильтр 1€: стоит — сильное сглаживание (F_MIN), идёт — малая задержка (FILTER_BETA · скорость)
F_MIN, FILTER_BETA, D_CUTOFF = 1.0, 13.0, 1.5
VEL_WINDOW_S = 0.4  # наклон за окно
VEL_START_N, VEL_STOP_N = 3, 2  # столько последних замеров — заметить раньше окна, что пошёл / что встал (при любой частоте кадров)
GAP_S = 0.4  # замеров не было дольше: человек мог уйти куда угодно, начинаем заново (размах помним — привычка та же)
START_BLEND = 0.5  # пошёл: скорость — середина между длинным и коротким окном
DECEL_BLEND = 0.4  # тормозит: доля поправки к короткому окну; больше — и качание шага заметно гасит упреждение
STOP_RATIO = 0.3  # короткое окно меньше этой доли длинного — человек встал
STOP_SAMPLES = 2  # столько замеров подряд: качание шага на один замер скорость тоже почти обнуляет
CENTER_STILL_S, CENTER_WALK_S = 2.5, 0.15  # центр — среднее за столько, пока стоит / пока идёт
SWAY_START = 0.03  # начальная оценка размаха, доли кадра
SWAY_FALL_S = 6.0  # размах забывается медленно
SWAY_MAX = 0.16  # доли кадра
REVERSAL_V, REVERSAL_SWAY_MAX, REVERSAL_LEARN = 0.05, 0.3, 0.5  # разворот быстрее этого (кадр/с): размах за разворот, не больше — иначе это проход туда-обратно
REVERSAL_WALK = 0.3  # развернулся — вес «идёт» падает до этой доли: шатание не считается ходьбой
EXCURSION_K, EXCURSION_MIN, EXCURSION_SPAN = 1.25, 0.05, 1.5  # вышел за размах: порог k·размах+мин, полный вес ·span
EXCURSION_MAX = 0.24  # доли кадра: порог не больше — кто бы как ни шатался, ушедшего от центра камера ведёт
RUSH_V_LOW, RUSH_V_HIGH, RUSH_AWAY_LOW, RUSH_AWAY_HIGH = 0.5, 0.75, 0.06, 0.14  # доли кадра/с и доли кадра
FAR_LOW, FAR_HIGH = 0.10, 0.16  # доли кадра от центра: дальше скорость не важна (медленная ровная ходьба)
SPEED_MIN, SPEED_K, SPEED_REF_MAX = 0.06, 2.2, 0.25  # доли кадра/с: быстрее переминания (размах · k), не меньше мин и не больше макс
SPEED_LOW, SPEED_HIGH = 0.6, 1.2  # доли этой скорости: вес от 0 до 1
SPEED_SURE_LOW, SPEED_SURE_HIGH = 4.4, 7.0  # доли этой скорости: столько — точно идёт
WALK_RISE_S, WALK_FALL_S = 0.1, 0.08
WALK_COOL_S = 1.2  # остановился: столько вес не поднимается снова — после ходьбы человек обычно шатается на месте (запись F9)
KEEP_S = 0.5


def _alpha(dt: float, cutoff_hz: float) -> float:
    return 1.0 / (1.0 + 1.0 / (2 * math.pi * cutoff_hz * dt))


def _slope(pts: list[tuple[float, float]]) -> float:
    """Наклон прямой по точкам (t, x) методом наименьших квадратов; 0 — если точек меньше двух."""
    if len(pts) < 2:
        return 0.0
    mt = sum(t for t, _ in pts) / len(pts)
    mx = sum(x for _, x in pts) / len(pts)
    den = sum((t - mt) ** 2 for t, _ in pts)
    return sum((t - mt) * (x - mx) for t, x in pts) / den if den > 0 else 0.0


class PersonEstimator:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.t: float | None = None  # момент последнего замера
        self.pos = 0.0
        self.vel = 0.0  # мировые единицы/с
        self.center = 0.0  # середина переминания
        self.sway = SWAY_START  # половина размаха переминания, доли кадра (в мире размах при наезде раздувался)
        self._dir = 0  # куда шёл в последний раз
        self._extreme: float | None = None  # где развернулся в прошлый раз
        self.walk = 0.0  # 0 — стоит, 1 — идёт
        self._dx = 0.0
        self._walked = False  # вес был высоким: следующий спад — остановка
        self._calm_until = float("-inf")  # до этого момента считаем, что человек только что остановился
        self._stopping = 0  # сколько замеров подряд короткое окно показывает остановку
        self._hist: list[tuple[float, float]] = []

    def update(self, t: float, z: float, k: float) -> None:
        """Новый замер: z — мировая координата в момент съёмки t, k — масштаб зума в этот момент."""
        z, k = float(z), float(k)
        if self.t is None:
            self.t, self.pos, self.center = t, z, z
            self._hist = [(t, z)]
            return
        if t - self.t > GAP_S:
            self.t, self.pos, self.center, self.vel, self.walk = t, z, z, 0.0, 0.0
            self._dx, self._stopping, self._hist, self._dir, self._extreme = 0.0, 0, [(t, z)], 0, None
            return
        dt = max(1e-3, t - self.t)
        self.t = t
        self._dx += _alpha(dt, D_CUTOFF) * ((z - self.pos) / dt - self._dx)
        self.pos += _alpha(dt, F_MIN + FILTER_BETA * abs(self._dx) * k) * (z - self.pos)
        self._hist.append((t, z))
        self._hist = [(a, b) for a, b in self._hist if t - a <= KEEP_S]
        self.vel = self._velocity(t)
        self._note_reversal(k)
        self._update_walk(t, dt, k)
        center_t = CENTER_STILL_S + (CENTER_WALK_S - CENTER_STILL_S) * self.walk
        self.center += (self.pos - self.center) * (1 - math.exp(-dt / center_t))
        self._forget_sway(dt)

    def _velocity(self, t: float) -> float:
        long = _slope([(a, b) for a, b in self._hist if t - a <= VEL_WINDOW_S])
        start = _slope(self._hist[-VEL_START_N:])
        stop = _slope(self._hist[-VEL_STOP_N:])
        stopping = stop * long <= 0 or abs(stop) < STOP_RATIO * abs(long)
        self._stopping = self._stopping + 1 if stopping else 0
        if self._stopping >= STOP_SAMPLES:
            return stop if abs(stop) < abs(long) else long  # встал или развернулся: верим меньшей скорости
        if start * long > 0 and abs(start) > abs(long):
            return long + START_BLEND * (start - long)  # пошёл: не ждём длинное окно
        if start * long > 0:
            return long + DECEL_BLEND * (start - long)  # тормозит: длинное окно ещё показывает прежнюю скорость
        return long

    def _note_reversal(self, k: float) -> None:
        """Разворот — признак шатания: уточняем размах по двум крайним точкам и не считаем это ходьбой."""
        v = self.vel * k
        d = 0 if abs(v) < REVERSAL_V else (1 if v > 0 else -1)
        if d and self._dir and d != self._dir:
            if self._extreme is not None:
                half = abs(self.pos - self._extreme) * k / 2
                if self.sway < half <= REVERSAL_SWAY_MAX:  # только вверх: мелкие развороты внутри качания размах не уменьшают
                    self.sway += (min(half, SWAY_MAX) - self.sway) * REVERSAL_LEARN
            self._extreme = self.pos
            self.walk *= REVERSAL_WALK
        if d:
            self._dir = d

    def _speed_ref(self, k: float) -> float:
        """Скорость, с которой человек уже считается идущим. Пошёл — порог минимальный: ровная медленная ходьба не обрывается."""
        ref = min(SPEED_REF_MAX, max(SPEED_MIN, SPEED_K * self.sway))
        return ref + (SPEED_MIN - ref) * smoothstep(self.walk, 0.1, 0.5)

    def _update_walk(self, t: float, dt: float, k: float) -> None:
        speed_ref = self._speed_ref(k)
        away = abs(self.pos - self.center) * k
        speed = smoothstep(abs(self.vel) * k, SPEED_LOW * speed_ref, SPEED_HIGH * speed_ref)
        # далеко за любым переминанием: идёт, даже если скорость похожа на качание (медленная ровная ходьба)
        if abs(self.vel) * k >= SPEED_LOW * SPEED_MIN:
            speed = max(speed, smoothstep(away, FAR_LOW, FAR_HIGH))
        limit = min(EXCURSION_MAX, EXCURSION_K * self.sway + EXCURSION_MIN)
        excursion = smoothstep(away, limit, EXCURSION_SPAN * limit)
        # намного быстрее переминания — идёт, не дожидаясь выхода за размах (резкий уход из кадра)
        excursion = max(excursion, smoothstep(abs(self.vel) * k, SPEED_SURE_LOW * speed_ref, SPEED_SURE_HIGH * speed_ref))
        # уже идёт — достаточно скорости: центр идёт за человеком и «выхода за размах» больше нет
        # Уходит быстро и уже в стороне от центра: при шатании человек на этой скорости проходит центр, а тут уже ушёл
        if (self.pos - self.center) * self.vel > 0:
            excursion = max(excursion, smoothstep(abs(self.vel) * k, RUSH_V_LOW, RUSH_V_HIGH) * smoothstep(away, RUSH_AWAY_LOW, RUSH_AWAY_HIGH))
        target = speed * max(excursion, smoothstep(self.walk, 0.1, 0.5))
        if self.walk > 0.6:
            self._walked = True
        elif self.walk < 0.3 and self._walked:
            self._walked, self._calm_until = False, t + WALK_COOL_S
        if t < self._calm_until:
            target *= smoothstep(away, FAR_LOW, FAR_HIGH)  # пошёл снова — только если правда ушёл от места
        rate = WALK_RISE_S if target > self.walk else WALK_FALL_S
        self.walk += (target - self.walk) * (1 - math.exp(-dt / rate))

    def _forget_sway(self, dt: float) -> None:
        """Размах узнаём только по разворотам (ровная ходьба его не раздувает), без них — постепенно забываем."""
        if self.walk <= 0.3:
            self.sway += (SWAY_START - self.sway) * (1 - math.exp(-dt / SWAY_FALL_S))
