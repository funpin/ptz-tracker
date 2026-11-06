"""Автомат раскладки: оси и кнопки + время -> намерения. Без Qt и SDL, время приходит аргументом."""

from __future__ import annotations

from collections.abc import Callable

from ptztracker.control.manual_curve import DEFAULT_SENS, shape

from .device import RawPad
from .intents import (
    Composition,
    Disconnected,
    GainStep,
    Help,
    Intent,
    IrisStep,
    MenuAccept,
    MenuClose,
    MenuNav,
    MenuOpen,
    Neighbor,
    NextCamera,
    PanTilt,
    Preset,
    PushFocus,
    SaveMoment,
    SpeedStep,
    Stop,
    ToggleFollow,
    Zoom,
)

DEADZONE = 0.12
STALL_S = 0.3            # пауза между опросами дольше этого — сбой опроса, отпускание не доверяем
PRESET_HOLD_S = 1.0       # удержание кнопки пресета дольше — запомнить
REPEAT_DELAY_S = 0.4      # автоповтор диафрагмы/усиления: пауза, затем...
REPEAT_RATE = 6.0         # ...столько шагов в секунду
NEIGHBOR_THRESHOLD = 0.6  # правый стик вбок; взводится снова после возврата в мёртвую зону
SWIPE_MIN_DX = 1 / 3      # свайп по тачпаду: не меньше трети ширины...
SWIPE_MAX_S = 0.8         # ...не дольше этого (на DualSense свайп 0,1–0,4 с, замер 2026-11-06)

_PRESET_BUTTONS = {"cross": 0, "circle": 1, "square": 2, "triangle": 3}
_HOLD_BUTTONS = ("cross", "circle", "square", "triangle", "create")  # короткое/длинное нажатие
_REPEAT_BUTTONS = {"dpad_right": IrisStep(+1), "dpad_left": IrisStep(-1),
                   "dpad_up": GainStep(+1), "dpad_down": GainStep(-1)}
_PLAIN_BUTTONS = {"r3": ToggleFollow(), "touchpad": Stop(), "l3": PushFocus(),
                  "l1": SpeedStep(-1), "r1": SpeedStep(+1), "options": MenuOpen(), "ps": Help()}
_MENU_BUTTONS = {"dpad_up": MenuNav(0, -1), "dpad_down": MenuNav(0, +1), "dpad_left": MenuNav(-1, 0),
                 "dpad_right": MenuNav(+1, 0), "cross": MenuAccept(), "circle": MenuClose(),
                 "triangle": MenuClose(), "options": MenuClose()}


def _dz(x: float) -> float:
    """Линейная кривая после мёртвой зоны, со знаком."""
    a = abs(x)
    if a <= DEADZONE:
        return 0.0
    return (1.0 if x > 0 else -1.0) * min(1.0, (a - DEADZONE) / (1.0 - DEADZONE))


def _r(x: float) -> float:
    return round(x, 2) + 0.0  # +0.0 убирает -0.0


class Mapper:
    def __init__(self, menu_open: Callable[[], bool]) -> None:
        self._menu_open = menu_open
        # чувствительность стика 0…100 (control.manual_curve); пишет поток Qt, читает поток опроса — одно число
        self.sensitivity = DEFAULT_SENS
        self._reset()

    def _reset(self) -> None:
        self._connected = False
        self._prev: frozenset[str] = frozenset()
        self._sent_pt = (0.0, 0.0)
        self._sent_zoom = 0.0
        self._latched = False
        self._holds: dict[str, list] = {}   # кнопка -> [время нажатия, уже запомнили]
        self._repeat: dict[str, float] = {}  # кнопка -> время следующего шага
        self._neighbor_armed = True
        self._last_t = 0.0
        self._touch: list | None = None  # [t0, x0, y0, t, x, y, годится]: текущее касание тачпада

    def latch_stop(self) -> None:
        """После стопа скорости остаются нулём, пока стики и курки не вернутся в мёртвую зону."""
        self._latched = True
        self._sent_pt = (0.0, 0.0)
        self._sent_zoom = 0.0

    def cancel_holds(self) -> None:
        self._holds.clear()

    def update(self, pad: RawPad | None, t: float) -> list[Intent]:
        if pad is None:
            if self._connected:
                self._reset()
                return [Disconnected()]
            return []
        fresh = not self._connected
        if fresh:
            self._prev = pad.buttons  # кнопки, зажатые при подключении, не считаются нажатиями
            self._latched = True      # отклонённые при подключении левый стик и курки не двигают камеру, пока не вернутся в центр
            # правый стик, отклонённый при подключении, не даёт Neighbor, пока не вернётся в мёртвую зону
            self._neighbor_armed = abs(float(pad.axes.get("rightx", 0.0))) < NEIGHBOR_THRESHOLD
        self._connected = True
        out: list[Intent] = []
        menu = self._menu_open()
        ax = pad.axes
        g = lambda k: float(ax.get(k, 0.0))  # noqa: E731

        sens = self.sensitivity
        vx, vy = shape(_dz(g("leftx")), sens), -shape(_dz(g("lefty")), sens)
        z = max(-1.0, min(1.0, _dz(g("r2")) - _dz(g("l2"))))
        if self._latched:
            if vx == 0.0 and vy == 0.0 and z == 0.0:
                self._latched = False
            vx = vy = z = 0.0
        pt, zoom = (_r(vx), _r(vy)), _r(z)
        if pt != self._sent_pt:
            self._sent_pt = pt
            out.append(PanTilt(*pt))
        if zoom != self._sent_zoom:
            self._sent_zoom = zoom
            out.append(Zoom(zoom))

        rx = g("rightx")
        if abs(rx) <= DEADZONE:
            self._neighbor_armed = True
        elif self._neighbor_armed and abs(rx) >= NEIGHBOR_THRESHOLD:
            self._neighbor_armed = False
            out.append(Neighbor(1 if rx > 0 else -1))

        pressed = pad.buttons - self._prev
        released = self._prev - pad.buttons
        for b in sorted(pressed):
            if menu:
                if b in _MENU_BUTTONS:
                    out.append(_MENU_BUTTONS[b])
                elif b in ("touchpad", "r3", "ps"):
                    out.append({"touchpad": Stop(), "r3": ToggleFollow(), "ps": Help()}[b])
                continue
            if b in _HOLD_BUTTONS:
                self._holds[b] = [t, False]
            elif b in _REPEAT_BUTTONS:
                out.append(_REPEAT_BUTTONS[b])
                self._repeat[b] = t + REPEAT_DELAY_S
            elif b in _PLAIN_BUTTONS:
                out.append(_PLAIN_BUTTONS[b])
        for b in sorted(released):
            hold = self._holds.pop(b, None)
            if hold is not None and not hold[1]:
                # перезапись пресета разрушительна: запоминаем только при виденном удержании; после сбоя опроса
                # или при долгом удержании без записи не делаем ничего (оператор повторит)
                if t - self._last_t <= STALL_S and t - hold[0] < PRESET_HOLD_S:
                    out.append(NextCamera() if b == "create" else Preset(_PRESET_BUTTONS[b], False))
            self._repeat.pop(b, None)
            if menu and b in ("dpad_left", "dpad_right"):
                out.append(MenuNav(0, 0))  # отпустили ←/→: ручной фокус останавливается
        if menu:
            self._holds.clear()
            self._repeat.clear()
        else:
            for b, hold in self._holds.items():
                if not hold[1] and t - hold[0] >= PRESET_HOLD_S:
                    hold[1] = True
                    out.append(SaveMoment() if b == "create" else Preset(_PRESET_BUTTONS[b], True))
            for b in sorted(self._repeat):
                if t >= self._repeat[b] and b in pad.buttons:
                    out.append(_REPEAT_BUTTONS[b])  # не больше шага за опрос: после паузы без «очереди» шагов
                    nxt = self._repeat[b] + 1.0 / REPEAT_RATE
                    self._repeat[b] = nxt if nxt > t else t + 1.0 / REPEAT_RATE
        swipe = self._swipe(pad, t, fresh)
        if swipe and not menu:
            out.append(Composition(-1 if swipe > 0 else +1))  # вправо — шире, как прежний R1
        self._prev = pad.buttons
        self._last_t = t
        return out

    def _swipe(self, pad: RawPad, t: float, fresh: bool) -> int:
        """Свайп по тачпаду: +1 вправо, −1 влево, 0 — нет. Решается при отпускании пальца.

        Не свайп: палец был на тачпаде при подключении, был пропуск опросов, тачпад нажимали (это Стоп),
        короче трети ширины, дольше SWIPE_MAX_S, по вертикали больше, чем по горизонтали."""
        cur, tr = pad.touch, self._touch
        if cur is not None:
            x, y = float(cur[0]), float(cur[1])
            if tr is None:
                self._touch = [t, x, y, t, x, y, not fresh]
            else:
                tr[3:6] = [t, x, y]
                if t - self._last_t > STALL_S:
                    tr[6] = False
            if "touchpad" in pad.buttons:
                self._touch[6] = False
            return 0
        self._touch = None
        if tr is None or not tr[6] or t - self._last_t > STALL_S:
            return 0
        dx, dy, dur = tr[4] - tr[1], tr[5] - tr[2], tr[3] - tr[0]
        if dur > SWIPE_MAX_S or abs(dx) < SWIPE_MIN_DX or abs(dy) >= abs(dx):
            return 0
        return 1 if dx > 0 else -1
