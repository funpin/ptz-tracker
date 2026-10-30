"""Слой устройства: геймпад через SDL (pygame-ce). Видео — только пустой драйвер dummy (event.pump без него
не работает), окон нет.

pygame импортируется лениво, в SdlBackend.open(): без него модуль импортируется, тесты работают на фейке.
Фоновые события нужны, чтобы стик работал, пока окно программы не в фокусе (оператор в другом окне).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")  # без видеосистемы event.pump() бросает pygame.error

BUTTONS = ("cross", "circle", "square", "triangle", "l1", "r1", "l3", "r3",
           "dpad_up", "dpad_down", "dpad_left", "dpad_right", "touchpad", "options")
STICK_AXES = ("leftx", "lefty", "rightx", "righty")  # −1…1, вниз = + (как в SDL)
TRIGGER_AXES = ("l2", "r2")  # 0…1


@dataclass(frozen=True)
class RawPad:
    axes: dict[str, float] = field(default_factory=dict)
    buttons: frozenset[str] = frozenset()


log = logging.getLogger(__name__)
TOUCHPAD_BUTTON = 20  # SDL_CONTROLLER_BUTTON_TOUCHPAD в SDL2; в pygame-ce 2.5 константы нет (15 — это MISC1, кнопка микрофона)


class SdlBackend:
    def __init__(self) -> None:
        self._pg = None
        self._controller = None
        self._ctl = None
        self._pump_error_logged = False
        self._warned: set[str] = set()
        self._mapping: dict[str, int] = {}

    def open(self) -> bool:
        """Запускает joystick/controller и пустое видео dummy (без окон и звука). Любой сбой SDL — False и одна строка в лог."""
        try:
            import pygame
            import pygame._sdl2.controller as controller

            pygame.display.init()  # драйвер dummy: ни окна, ни Cocoa
            pygame.joystick.init()
            controller.init()
            p = pygame
            self._mapping = {
                "cross": p.CONTROLLER_BUTTON_A, "circle": p.CONTROLLER_BUTTON_B, "square": p.CONTROLLER_BUTTON_X,
                "triangle": p.CONTROLLER_BUTTON_Y, "l1": p.CONTROLLER_BUTTON_LEFTSHOULDER,
                "r1": p.CONTROLLER_BUTTON_RIGHTSHOULDER, "l3": p.CONTROLLER_BUTTON_LEFTSTICK,
                "r3": p.CONTROLLER_BUTTON_RIGHTSTICK, "dpad_up": p.CONTROLLER_BUTTON_DPAD_UP,
                "dpad_down": p.CONTROLLER_BUTTON_DPAD_DOWN, "dpad_left": p.CONTROLLER_BUTTON_DPAD_LEFT,
                "dpad_right": p.CONTROLLER_BUTTON_DPAD_RIGHT, "touchpad": TOUCHPAD_BUTTON,
                "options": p.CONTROLLER_BUTTON_START,
            }
            self._pg, self._controller = pygame, controller
            return True
        except Exception as e:
            log.warning("Геймпад недоступен: SDL не запустился (%s: %s)", type(e).__name__, e)
            self._pg = None
            return False

    def _attach(self) -> bool:
        if self._ctl is not None and self._ctl.attached():
            return True
        self._ctl = None
        c = self._controller
        for i in range(c.get_count()):
            if c.is_controller(i):
                self._ctl = c.Controller(i)
                return True
        return False

    def poll(self) -> RawPad | None:
        if self._pg is None:
            return None
        try:
            return self._poll()
        except self._pg.error as e:
            if not self._pump_error_logged:
                self._pump_error_logged = True
                log.debug("Опрос геймпада: pygame.error: %s", e)
            self._ctl = None  # устройство пропало посреди опроса
            return None

    def _poll(self) -> RawPad | None:
        self._pg.event.pump()
        if not self._attach():
            return None
        c, ctl = self._pg.CONTROLLER_AXIS_LEFTX, self._ctl
        names = {"leftx": c, "lefty": c + 1, "rightx": c + 2, "righty": c + 3,
                 "l2": self._pg.CONTROLLER_AXIS_TRIGGERLEFT, "r2": self._pg.CONTROLLER_AXIS_TRIGGERRIGHT}
        axes = {k: ctl.get_axis(v) / 32767.0 for k, v in names.items()}
        for k in TRIGGER_AXES:
            axes[k] = max(0.0, axes[k])
        for k in STICK_AXES:
            axes[k] = max(-1.0, min(1.0, axes[k]))
        return RawPad(axes, frozenset(k for k, v in self._mapping.items() if ctl.get_button(v)))

    def info(self) -> dict:
        if self._ctl is None:
            return {"name": None, "type": None, "has_led": False, "has_rumble": False, "power": None}
        name = self._ctl.name or ""
        low = name.lower()
        kind = "ps5" if "dualsense" in low else "ps4" if ("dualshock" in low or "ps4" in low) else "other"
        power = None
        try:
            power = self._ctl.as_joystick().get_power_level()
        except Exception:
            pass
        # pygame-ce не сообщает о возможностях напрямую: LED и вибрацию умеют DualSense/DualShock
        ps = kind in ("ps5", "ps4")
        return {"name": name, "type": kind, "has_led": ps, "has_rumble": True, "power": power}

    def _call(self, what: str, fn, *args) -> bool:
        """Вызов светодиода/вибрации: сбой или False не молчат (один раз в debug на каждый вид вызова)."""
        try:
            ok = fn(*args)
            err = None if ok is not False else "вернул False"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        if err and what not in self._warned:
            self._warned.add(what)
            log.debug("Геймпад: %s не удалось: %s", what, err)
        return err is None

    def set_led(self, rgb: tuple[int, int, int]) -> None:
        if self._ctl is not None:
            self._call("set_led", self._ctl.set_led, tuple(rgb))  # pygame-ce: один аргумент (кортеж или Color)

    def rumble(self, low: float, high: float, ms: int) -> None:
        if self._ctl is not None:
            self._call("rumble", self._ctl.rumble, low, high, ms)

    def close(self) -> None:
        try:
            if self._ctl is not None:
                self._ctl.quit()
            if self._controller is not None:
                self._controller.quit()
            if self._pg is not None:
                self._pg.joystick.quit()
                self._pg.display.quit()
        except Exception:
            pass
        self._ctl = self._pg = self._controller = None
