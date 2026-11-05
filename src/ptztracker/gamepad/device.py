"""Слой устройства: геймпад через SDL (pygame-ce). Видео — только пустой драйвер dummy (event.pump без него
не работает), окон нет.

pygame импортируется лениво, в SdlBackend.open(): без него модуль импортируется, тесты работают на фейке.
Фоновые события нужны, чтобы стик работал, пока окно программы не в фокусе (оператор в другом окне).

SDL запускается, опрашивается и закрывается в одном фоновом потоке (MultiPadThread), никогда в потоке Qt:
на macOS SDL доставляет события HID в цикл того потока, где его запустили, и запуск из Qt портил память.
Своё HID-подключение к геймпаду не открываем: его закрытие гонялось с потоком чтения SDL (падение на Mac).
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
# MFi-драйвер (GameController) зовёт SDL из своей очереди: при подключении геймпада он брал замок джойстиков SDL,
# а поток опроса ждал этот замок, держа GIL, — программа зависала (Mac, 2026-11-06). DualSense идёт через HIDAPI
os.environ.setdefault("SDL_JOYSTICK_MFI", "0")

BUTTONS = ("cross", "circle", "square", "triangle", "l1", "r1", "l3", "r3",
           "dpad_up", "dpad_down", "dpad_left", "dpad_right", "touchpad", "options", "create")
STICK_AXES = ("leftx", "lefty", "rightx", "righty")  # −1…1, вниз = + (как в SDL)
TRIGGER_AXES = ("l2", "r2")  # 0…1


@dataclass(frozen=True)
class RawPad:
    axes: dict[str, float] = field(default_factory=dict)
    buttons: frozenset[str] = frozenset()
    touch: tuple[float, float] | None = None  # первый палец на тачпаде, x и y 0…1 (x — вправо); нет касания — None


log = logging.getLogger(__name__)
BATTERY_PERIOD_S = 5.0
_POWER_PCT = {"empty": 5, "low": 20, "medium": 70, "full": 100}  # верхние границы уровней SDL
TOUCHPAD_BUTTON = 20  # SDL_CONTROLLER_BUTTON_TOUCHPAD в SDL2; в pygame-ce 2.5 константы нет (15 — это MISC1, кнопка микрофона)


MAX_PADS = 4
SCAN_PERIOD_S = 0.5  # как часто искать новые контроллеры


class SdlHub:
    """Один SDL на процесс: init/quit, event.pump и список подключённых контроллеров (до 4).

    Все вызовы — из одного потока опроса. Новый контроллер занимает первый свободный индекс, отключённый
    освобождает свой. Индекс хаба стабилен, пока контроллер подключён."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.pg = None
        self._failed = False
        self.mapping: dict[str, int] = {}
        self._controller = None
        self._clock = clock
        self._slots: list = [None] * MAX_PADS
        self._scan_next = 0.0

    def backend(self, index: int) -> SdlBackend:
        """Вид одного контроллера. SDL не трогает (создаётся в потоке Qt): pygame берёт у хаба при первом опросе."""
        return SdlBackend(self, index)

    def open(self) -> bool:
        """Запускает joystick/controller и пустое видео dummy (без окон и звука). Любой сбой SDL — False и одна строка в лог."""
        if self.pg is not None:
            return True
        if self._failed:
            return False
        try:
            import pygame
            import pygame._sdl2.controller as controller

            pygame.display.init()  # драйвер dummy: ни окна, ни Cocoa
            pygame.joystick.init()
            controller.init()
            p = pygame
            self.mapping = {
                "cross": p.CONTROLLER_BUTTON_A, "circle": p.CONTROLLER_BUTTON_B, "square": p.CONTROLLER_BUTTON_X,
                "triangle": p.CONTROLLER_BUTTON_Y, "l1": p.CONTROLLER_BUTTON_LEFTSHOULDER,
                "r1": p.CONTROLLER_BUTTON_RIGHTSHOULDER, "l3": p.CONTROLLER_BUTTON_LEFTSTICK,
                "r3": p.CONTROLLER_BUTTON_RIGHTSTICK, "dpad_up": p.CONTROLLER_BUTTON_DPAD_UP,
                "dpad_down": p.CONTROLLER_BUTTON_DPAD_DOWN, "dpad_left": p.CONTROLLER_BUTTON_DPAD_LEFT,
                "dpad_right": p.CONTROLLER_BUTTON_DPAD_RIGHT, "touchpad": TOUCHPAD_BUTTON,
                "options": p.CONTROLLER_BUTTON_START, "create": p.CONTROLLER_BUTTON_BACK,
            }
            self.pg, self._controller = pygame, controller
            return True
        except Exception as e:
            log.warning("Геймпад недоступен: SDL не запустился (%s: %s)", type(e).__name__, e)
            self.pg, self._failed = None, True
            return False

    def controller(self, index: int):
        return self._slots[index] if 0 <= index < MAX_PADS else None

    def pump(self) -> None:
        """Раз за такт: события SDL, снятие отключённых, раз в 0,5 с поиск новых (в первый свободный индекс)."""
        if self.pg is None:
            return
        self.pg.event.pump()
        for i, ctl in enumerate(self._slots):
            if ctl is not None and not self._attached(ctl):
                self._slots[i] = None
                try:
                    ctl.quit()
                except Exception:
                    pass
        now = self._clock()
        if None in self._slots and now >= self._scan_next:
            self._scan_next = now + SCAN_PERIOD_S
            self._scan()

    @staticmethod
    def _attached(ctl) -> bool:
        try:
            return bool(ctl.attached())
        except Exception:
            return False

    def _scan(self) -> None:
        c = self._controller
        known = set()
        for ctl in self._slots:
            if ctl is not None:
                try:
                    known.add(ctl.as_joystick().get_instance_id())
                except Exception:
                    pass
        for i in range(c.get_count()):
            if None not in self._slots:
                return
            if not c.is_controller(i):
                continue
            try:
                ctl = c.Controller(i)
                iid = ctl.as_joystick().get_instance_id()
            except Exception as e:
                log.debug("Контроллер %s не открыт: %s: %s", i, type(e).__name__, e)
                continue
            if any(ctl is s for s in self._slots):
                continue  # pygame отдаёт тот же объект: закрыть его — отключить свой же геймпад
            if iid in known:
                try:
                    ctl.quit()  # тот же контроллер уже в списке
                except Exception:
                    pass
                continue
            known.add(iid)
            self._slots[self._slots.index(None)] = ctl

    def close(self) -> None:
        try:
            for ctl in self._slots:
                if ctl is not None:
                    ctl.quit()
            if self._controller is not None:
                self._controller.quit()
            if self.pg is not None:
                self.pg.joystick.quit()
                self.pg.display.quit()
        except Exception:
            pass
        self._slots = [None] * MAX_PADS
        self.pg = self._controller = None


class SdlBackend:
    """Один контроллер хаба по индексу. Без аргументов — одиночный геймпад со своим хабом (как раньше)."""

    def __init__(self, hub: SdlHub | None = None, index: int = 0) -> None:
        self._own_hub = hub is None
        self._hub = SdlHub() if hub is None else hub
        self._index = index
        self._pg = None
        self._ctl = None
        self._pump_error_logged = False
        self._warned: set[str] = set()
        self._mapping: dict[str, int] = {}
        self._sdl = None

    def open(self) -> bool:
        if not self._hub.open():
            self._pg = None
            return False
        self._pg, self._mapping = self._hub.pg, self._hub.mapping
        return True

    def _attach(self) -> bool:
        self._ctl = self._hub.controller(self._index)
        return self._ctl is not None

    def poll(self) -> RawPad | None:
        if self._pg is None and not self._own_hub and self._hub.pg is not None:
            self._pg, self._mapping = self._hub.pg, self._hub.mapping  # общий хаб открыл поток опроса
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
        if self._own_hub:
            self._hub.pump()  # у общего хаба pump делает MultiPadThread раз за такт
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
        return RawPad(axes, frozenset(k for k, v in self._mapping.items() if ctl.get_button(v)), self._touch())

    def _touch(self) -> tuple[float, float] | None:
        """Первый палец на тачпаде (x, y 0…1) или None. Сбой SDL — None, одна строка в журнал."""
        try:
            lib = self._sdl_lib()
            fn = lib.SDL_GameControllerGetTouchpadFinger
            if isinstance(lib, ctypes.CDLL) and fn.argtypes is None:
                f = ctypes.POINTER(ctypes.c_float)
                fn.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_uint8), f, f, f]
            ptr = lib.SDL_GameControllerFromInstanceID(self._ctl.as_joystick().get_instance_id())
            if not ptr:
                return None
            st, x, y, p = ctypes.c_uint8(), ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
            if fn(ptr, 0, 0, ctypes.byref(st), ctypes.byref(x), ctypes.byref(y), ctypes.byref(p)) != 0:
                return None  # тачпада нет (не DualSense/DualShock)
            return (float(x.value), float(y.value)) if st.value else None
        except Exception as e:
            if "touch" not in self._warned:
                self._warned.add("touch")
                log.debug("Геймпад: тачпад не прочитан: %s: %s", type(e).__name__, e)
            return None

    def info(self) -> dict:
        if self._ctl is None:
            return {"name": None, "type": None, "has_led": False, "has_rumble": False, "power": None,
                    "id": None, "has_serial": False}
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
        serial = self._serial()
        pad_id = serial or f"{self._guid()}:{name}"
        return {"name": name, "type": kind, "has_led": ps, "has_rumble": True, "power": power,
                "id": pad_id, "has_serial": bool(serial)}

    def _serial(self) -> str | None:
        """Серийный номер SDL (у DualSense — адрес Bluetooth); нет или сбой — None. Только для опознания настроек."""
        try:
            lib = self._sdl_lib()
            fn = lib.SDL_GameControllerGetSerial
            if isinstance(lib, ctypes.CDLL):
                fn.restype, fn.argtypes = ctypes.c_char_p, [ctypes.c_void_p]
            ptr = lib.SDL_GameControllerFromInstanceID(self._ctl.as_joystick().get_instance_id())
            raw = fn(ptr) if ptr else None
            return (raw.decode("utf-8", "replace").strip() or None) if raw else None
        except Exception as e:
            if "serial" not in self._warned:
                self._warned.add("serial")
                log.debug("Геймпад: серийный номер не прочитан: %s: %s", type(e).__name__, e)
            return None

    def _guid(self) -> str:
        try:
            return str(self._ctl.as_joystick().get_guid())
        except Exception:
            return "?"

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

    def _read_battery(self) -> tuple[int | None, bool | None]:
        if self._ctl is None:
            return (None, None)
        level = self._ctl.as_joystick().get_power_level()
        if level == "wired":
            return (None, True)
        return (_POWER_PCT.get(level), False) if level in _POWER_PCT else (None, None)

    def battery(self) -> tuple[int | None, bool | None]:
        """(процент — верхняя граница уровня SDL, заряжается); неизвестно или сбой — (None, None). Раз в 5 с."""
        out: list = []
        if self._call("battery", lambda: out.append(self._read_battery()) or True):
            return out[0]
        return (None, None)

    def close(self) -> None:
        self._ctl = self._pg = None
        if self._own_hub:
            self._hub.close()


log_thread = logging.getLogger(__name__ + ".thread")
_NEUTRAL_LED = (0, 0, 0)


class GamepadThread:
    """Опрос геймпада и выход на полосу/вибрацию. Все вызовы backend — из одного потока (tick).

    set_led/set_player/rumble только кладут команду в очередь. tick() публичный: поток просто зовёт его
    с частотой hz, а при необходимости его можно вызывать из таймера главного потока.
    """

    def __init__(self, backend, mapper, on_intents: Callable[[list], None],
                 on_connection: Callable[[dict | None], None], hz: float = 100.0,
                 clock: Callable[[], float] = time.monotonic,
                 on_battery: Callable[[int | None, bool | None], None] | None = None) -> None:
        self._b, self._mapper = backend, mapper
        self._on_intents, self._on_connection = on_intents, on_connection
        self._on_battery = on_battery
        self._batt_next = 0.0
        self._batt_last: tuple | None = None
        self._batt_warned = False
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
        self._lock = threading.Lock()

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
                # поток завис (например, в SDL): оператору всё равно сообщаем об отключении, без обращения к SDL;
                # сам поток доделает shutdown, когда проснётся
                self._send_disconnected()
                return
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
        with self._lock:
            if self._shut:
                return
            self._shut = True
        self._send_disconnected()
        self._safe(lambda: self._b.rumble(0.0, 0.0, 0))
        self._safe(lambda: self._b.set_led(_NEUTRAL_LED))

    def _send_disconnected(self) -> None:
        with self._lock:
            if not self._connected:
                return
            self._connected = False
        self._safe(lambda: self._on_intents([Disconnected()]))

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception:
            log_thread.exception("Геймпад: сбой при выходе")

    def tick(self) -> None:
        """Один шаг опроса. В режиме QTimer после сбоя (_dead) вызовы ничего не делают; stop() всё равно нужно вызвать."""
        if self._dead:
            return
        was = self._connected
        try:
            self._tick()
        except Exception:
            log_thread.exception("Геймпад: сбой в цикле опроса")
            self._dead = True
            with self._lock:
                self._connected = False
            self._safe(lambda: self._on_intents([Disconnected()]))  # дубль безвреден
            if was:
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
            elif not self._connected:
                pass  # узор, заказанный без устройства, не должен прозвучать после переподключения
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
        if self._stop.is_set():
            return  # остановка запрошена (поток мог зависнуть в poll): новых подключений не оформляем
        intents = self._mapper.update(pad, now)
        if pad is not None and not self._connected:
            self._connected = True
            info = self._b.info()
            if self._led is not None:
                self._b.set_led(self._led)
            if self._player is not None:
                self._b.set_player_index(self._player)
            self._batt_next, self._batt_last = 0.0, None
            self._on_connection(info)
        elif pad is None and self._connected:
            with self._lock:
                self._connected = False
            self._rumble = []
            self._publish_battery((None, None))
            if intents:
                self._on_intents(intents)  # сначала Disconnected: сбой обратного вызова связи его не теряет
                intents = []
            self._on_connection(None)
        if intents:
            self._on_intents(intents)
        if self._connected:
            self._step_rumble(now)
            self._poll_battery(now)

    def _publish_battery(self, val: tuple) -> None:
        if self._on_battery is None or val == self._batt_last:
            return
        self._batt_last = val
        self._on_battery(*val)

    def _poll_battery(self, now: float) -> None:
        """Заряд — раз в 5 с; сбой не роняет опрос (одна строка в лог), публикуем только изменения."""
        read = getattr(self._b, "battery", None)
        if read is None or self._on_battery is None or now < self._batt_next:
            return
        self._batt_next = now + BATTERY_PERIOD_S
        try:
            val = tuple(read())
        except Exception as e:
            if not self._batt_warned:
                self._batt_warned = True
                log_thread.debug("Заряд геймпада не прочитан: %s: %s", type(e).__name__, e)
            val = (None, None)
        self._publish_battery(val)


class MultiPadThread:
    """Один поток на все геймпады: за такт hub.pump() один раз, затем tick() каждого GamepadThread.

    Потоки GamepadThread создаются, но не запускаются: SDL однопоточный, все его вызовы идут отсюда —
    и запуск (hub.open), и закрытие. SDL не запустился — поток тихо завершается, геймпадов нет."""

    def __init__(self, hub, threads: list[GamepadThread], hz: float = 100.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._hub, self._threads = hub, threads
        self._period = 1.0 / hz
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pump_warned = False
        self._shut = False
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="gamepads", daemon=True)
            self._thread.start()

    def tick(self) -> None:
        try:
            self._hub.pump()
        except Exception as e:
            if not self._pump_warned:
                self._pump_warned = True
                log_thread.debug("Опрос SDL: %s: %s", type(e).__name__, e)
        for t in self._threads:
            t.tick()

    def _run(self) -> None:
        try:
            if not self._hub.open():
                return
            while not self._stop.is_set():
                t0 = self._clock()
                self.tick()
                self._stop.wait(max(0.0, self._period - (self._clock() - t0)))
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        with self._lock:
            if self._shut:
                return
            self._shut = True
        for t in self._threads:
            t.shutdown()
        try:
            self._hub.close()
        except Exception:
            log_thread.exception("Геймпады: сбой закрытия SDL")

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        th = self._thread
        if th is None:
            self.shutdown()
            return
        th.join(timeout)
        if th.is_alive():
            for t in self._threads:  # завис в SDL: оператору всё равно сообщаем об отключении, без обращения к SDL
                t._send_disconnected()
