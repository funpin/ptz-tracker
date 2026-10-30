"""Слой устройства: геймпад через SDL (pygame-ce). Видео — только пустой драйвер dummy (event.pump без него
не работает), окон нет.

pygame импортируется лениво, в SdlBackend.open(): без него модуль импортируется, тесты работают на фейке.
Фоновые события нужны, чтобы стик работал, пока окно программы не в фокусе (оператор в другом окне).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import queue
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .intents import Disconnected

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
        self._sdl = None

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

    def _sdl_lib(self):
        """SDL2 из пакета pygame (тот же, что у геймпада); иначе системный."""
        if self._sdl is None:
            import pathlib
            cands: list[str] = []
            try:
                import pygame
                base = pathlib.Path(pygame.__file__).parent
                cands += [str(p) for p in (base / ".dylibs").glob("libSDL2*.dylib")]
                cands += [str(base / "SDL2.dll")] if sys.platform == "win32" else []
                cands += [str(p) for p in base.glob("libSDL2*.so*")]
            except Exception:
                pass
            found = ctypes.util.find_library("SDL2")
            cands += [found] if found else []
            for c in cands:
                try:
                    lib = ctypes.CDLL(c)
                    lib.SDL_GameControllerFromInstanceID.restype = ctypes.c_void_p
                    lib.SDL_GameControllerFromInstanceID.argtypes = [ctypes.c_int32]
                    lib.SDL_GameControllerSetPlayerIndex.argtypes = [ctypes.c_void_p, ctypes.c_int]
                    self._sdl = lib
                    break
                except Exception:
                    continue
            if self._sdl is None:
                raise OSError("библиотека SDL2 не найдена")
        return self._sdl

    def _set_player_index(self, idx: int) -> bool:
        lib = self._sdl_lib()
        ptr = lib.SDL_GameControllerFromInstanceID(self._ctl.as_joystick().get_instance_id())
        if not ptr:
            return False
        lib.SDL_GameControllerSetPlayerIndex(ptr, idx)
        return True

    def set_player_index(self, n: int | None) -> None:
        """Огоньки номера игрока на DualSense: n 1…4 — n точек, None — погасить. Сбой не бросает."""
        if self._ctl is not None:
            idx = -1 if n is None else max(1, min(4, int(n))) - 1
            self._call("set_player_index", self._set_player_index, idx)

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


log_thread = logging.getLogger(__name__ + ".thread")
_NEUTRAL_LED = (0, 0, 0)


class GamepadThread:
    """Опрос геймпада и выход на полосу/вибрацию. Все вызовы backend — из одного потока (tick).

    set_led/set_player/rumble только кладут команду в очередь. tick() публичный: поток просто зовёт его
    с частотой hz, а при необходимости его можно вызывать из таймера главного потока.
    """

    def __init__(self, backend, mapper, on_intents: Callable[[list], None],
                 on_connection: Callable[[dict | None], None], hz: float = 100.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._b, self._mapper = backend, mapper
        self._on_intents, self._on_connection = on_intents, on_connection
        self._period = 1.0 / hz
        self._clock = clock
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._connected = False
        self._dead = False
        self._led: tuple[int, int, int] | None = None
        self._player: int | None = None
        self._rumble: list[tuple[float, float, int]] = []  # оставшиеся сегменты
        self._rumble_next = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._shut = False

    # --- вызовы из любого потока ---
    def set_led(self, rgb: tuple[int, int, int]) -> None:
        self._q.put(("led", tuple(rgb)))

    def set_player(self, n: int | None) -> None:
        self._q.put(("player", n))

    def rumble(self, pattern: tuple[tuple[float, float, int], ...]) -> None:
        """Сегменты (low, high, мс); low=high=0 — пауза. Новый узор заменяет прежний."""
        self._q.put(("rumble", tuple(pattern)))

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="gamepad", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        th = self._thread
        if th is not None:
            th.join(timeout)
            if th.is_alive():
                return  # поток сам завершит shutdown на выходе
        else:
            self.shutdown()

    # --- тело потока ---
    def _run(self) -> None:
        try:
            while not self._stop.is_set() and not self._dead:
                t0 = self._clock()
                self.tick()
                self._stop.wait(max(0.0, self._period - (self._clock() - t0)))
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Disconnected (если был подключён) и нейтральная полоса. Один раз."""
        if self._shut:
            return
        self._shut = True
        if self._connected:
            self._connected = False
            self._safe(lambda: self._on_intents([Disconnected()]))
        self._safe(lambda: self._b.rumble(0.0, 0.0, 0))
        self._safe(lambda: self._b.set_led(_NEUTRAL_LED))

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception:
            log_thread.exception("Геймпад: сбой при выходе")

    def tick(self) -> None:
        if self._dead:
            return
        try:
            self._tick()
        except Exception:
            log_thread.exception("Геймпад: сбой в цикле опроса")
            self._dead = True
            if self._connected:
                self._connected = False
                self._safe(lambda: self._on_intents([Disconnected()]))
                self._safe(lambda: self._on_connection(None))

    def _drain(self) -> None:
        while True:
            try:
                kind, val = self._q.get_nowait()
            except queue.Empty:
                return
            if kind == "led":
                self._led = val
                self._b.set_led(val)
            elif kind == "player":
                self._player = val
                self._b.set_player_index(val)
            else:
                self._rumble = list(val)
                self._rumble_next = 0.0
                if not val:
                    self._b.rumble(0.0, 0.0, 0)

    def _step_rumble(self, now: float) -> None:
        if self._rumble and now >= self._rumble_next:
            low, high, ms = self._rumble.pop(0)
            if low or high:
                self._b.rumble(low, high, ms)
            self._rumble_next = now + ms / 1000.0

    def _tick(self) -> None:
        now = self._clock()
        self._drain()
        pad = self._b.poll()
        intents = self._mapper.update(pad, now)
        if pad is not None and not self._connected:
            self._connected = True
            info = self._b.info()
            if self._led is not None:
                self._b.set_led(self._led)
            if self._player is not None:
                self._b.set_player_index(self._player)
            self._on_connection(info)
        elif pad is None and self._connected:
            self._connected = False
            self._rumble = []
            self._on_connection(None)
        if intents:
            self._on_intents(intents)
        if self._connected:
            self._step_rumble(now)
