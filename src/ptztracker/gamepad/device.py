"""Слой устройства: геймпад через SDL (pygame-ce). Видео не инициализируется, окна нет.

pygame импортируется лениво, в SdlBackend.open(): без него модуль импортируется, тесты работают на фейке.
Фоновые события нужны, чтобы стик работал, пока окно программы не в фокусе (оператор в другом окне).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")

BUTTONS = ("cross", "circle", "square", "triangle", "l1", "r1", "l3", "r3",
           "dpad_up", "dpad_down", "dpad_left", "dpad_right", "touchpad", "options")
STICK_AXES = ("leftx", "lefty", "rightx", "righty")  # −1…1, вниз = + (как в SDL)
TRIGGER_AXES = ("l2", "r2")  # 0…1


@dataclass(frozen=True)
class RawPad:
    axes: dict[str, float] = field(default_factory=dict)
    buttons: frozenset[str] = frozenset()


class SdlBackend:
    def __init__(self) -> None:
        self._pg = None
        self._ctl = None

    def open(self) -> bool:
        """Запускает SDL; True — запуск удался (устройства может не быть). ImportError — нет pygame."""
        import pygame
        import pygame._sdl2.controller as controller

        pygame.display.quit()  # видео не нужно
        if not pygame.get_init():
            pygame.init()
        pygame.joystick.init()
        controller.init()
        self._pg, self._controller = pygame, controller
        return True

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
        p = self._pg
        mapping = {"cross": p.CONTROLLER_BUTTON_A, "circle": p.CONTROLLER_BUTTON_B, "square": p.CONTROLLER_BUTTON_X,
                   "triangle": p.CONTROLLER_BUTTON_Y, "l1": p.CONTROLLER_BUTTON_LEFTSHOULDER,
                   "r1": p.CONTROLLER_BUTTON_RIGHTSHOULDER, "l3": p.CONTROLLER_BUTTON_LEFTSTICK,
                   "r3": p.CONTROLLER_BUTTON_RIGHTSTICK, "dpad_up": p.CONTROLLER_BUTTON_DPAD_UP,
                   "dpad_down": p.CONTROLLER_BUTTON_DPAD_DOWN, "dpad_left": p.CONTROLLER_BUTTON_DPAD_LEFT,
                   "dpad_right": p.CONTROLLER_BUTTON_DPAD_RIGHT, "touchpad": getattr(p, "CONTROLLER_BUTTON_TOUCHPAD", 15),
                   "options": p.CONTROLLER_BUTTON_START}
        return RawPad(axes, frozenset(k for k, v in mapping.items() if ctl.get_button(v)))

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

    def set_led(self, rgb: tuple[int, int, int]) -> None:
        if self._ctl is not None:
            try:
                self._ctl.set_led(*rgb)
            except Exception:
                pass  # у этого устройства светодиода нет

    def rumble(self, low: float, high: float, ms: int) -> None:
        if self._ctl is not None:
            try:
                self._ctl.rumble(low, high, ms)
            except Exception:
                pass

    def close(self) -> None:
        try:
            if self._ctl is not None:
                self._ctl.quit()
            if self._pg is not None:
                self._pg.quit()
        except Exception:
            pass
        self._ctl = self._pg = None
