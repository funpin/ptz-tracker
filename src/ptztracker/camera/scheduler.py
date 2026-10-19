"""Очередь команд камере.

Камера требует паузу ≥130 мс между командами, поэтому команды не шлются
сразу, а складываются в «ячейки» по ключу ("pt", "zoom", …). Новая команда
в ту же ячейку заменяет ещё не отправленную (склейка), команды с высоким
приоритетом уходят первыми, а повтор уже отправленного значения не шлётся.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable

from ptztracker.camera.client import AuthRequired, CameraUnreachable
from ptztracker.camera.protocol import STOP_PT, STOP_ZOOM, CameraError

log = logging.getLogger(__name__)

MAX_CAMERA_ERROR_RETRIES = 3
UNREACHABLE_BACKOFF_AFTER = 3
UNREACHABLE_BACKOFF_S = 1.0


class Priority(IntEnum):
    EMERGENCY = 0
    MANUAL = 1
    TRACKING = 2
    QUERY = 3


@dataclass
class _Item:
    cmd: str
    priority: Priority
    seq: int
    key: str | None = None
    future: Future | None = None
    errors: int = 0


@dataclass
class _State:
    slots: dict[str, _Item] = field(default_factory=dict)
    queries: list[_Item] = field(default_factory=list)
    last_sent: dict[str, str] = field(default_factory=dict)


class CommandScheduler:
    def __init__(
        self,
        send: Callable[[str], str],
        min_interval: float = 0.130,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._send = send
        self.min_interval = min_interval
        self._clock = clock
        self._st = _State()
        self._seq = itertools.count()
        self._cond = threading.Condition()
        self._last_send_t: float | None = None
        self._backoff_until = 0.0
        self._unreachable_streak = 0
        self._online: bool | None = None
        self.last_error: str | None = None
        self.on_state_change: Callable[[bool], None] | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    # --- публичный API -------------------------------------------------

    @property
    def online(self) -> bool:
        return bool(self._online)

    def submit(self, key: str, cmd: str, priority: Priority, only_if_changed: bool = True) -> None:
        with self._cond:
            if only_if_changed and self._st.last_sent.get(key) == cmd:
                self._st.slots.pop(key, None)  # вернулись к уже отправленному — отменить ожидающую
                return
            self._st.slots[key] = _Item(cmd, priority, next(self._seq), key=key)
            self._cond.notify()

    def query(self, cmd: str, timeout: float = 2.0) -> str:
        fut: Future = Future()
        with self._cond:
            self._st.queries.append(_Item(cmd, Priority.QUERY, next(self._seq), future=fut))
            self._cond.notify()
        return fut.result(timeout=timeout)

    def emergency_stop(self) -> None:
        with self._cond:
            self._st.slots.clear()
            for key, cmd in (("pt", STOP_PT), ("zoom", STOP_ZOOM)):
                self._st.slots[key] = _Item(cmd, Priority.EMERGENCY, next(self._seq), key=key)
            self._backoff_until = 0.0
            self._cond.notify()

    def pending(self) -> bool:
        with self._cond:
            return bool(self._st.slots or self._st.queries)

    def pump(self) -> str | None:
        """Отправить не больше одной команды, если окно открыто. Возвращает отправленную команду."""
        with self._cond:
            item = self._take_due(self._clock())
        if item is None:
            return None
        self._execute(item)
        return item.cmd

    def start(self) -> None:
        if self._thread:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="cmd-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        with self._cond:
            self._running = False
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None

    def wait_idle(self, timeout: float) -> bool:
        """Ждать, пока очередь опустеет (для остановки камеры перед выходом)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.pending():
                return True
            time.sleep(0.01)
        return not self.pending()

    # --- внутреннее ----------------------------------------------------

    def _wait_s(self, now: float) -> float | None:
        """Сколько ждать до ближайшего окна; None — очередь пуста."""
        if not (self._st.slots or self._st.queries):
            return None
        due = self._backoff_until
        if self._last_send_t is not None:
            due = max(due, self._last_send_t + self.min_interval)
        return max(0.0, due - now)

    def _take_due(self, now: float) -> _Item | None:
        wait = self._wait_s(now)
        if wait is None or wait > 0:
            return None
        candidates = list(self._st.slots.values()) + self._st.queries
        item = min(candidates, key=lambda i: (i.priority, i.seq))
        if item.key is not None:
            del self._st.slots[item.key]
        else:
            self._st.queries.remove(item)
        self._last_send_t = now
        return item

    def _execute(self, item: _Item) -> None:
        try:
            resp = self._send(item.cmd)
        except CameraUnreachable as e:
            self._on_failure(item, e, unreachable=True)
        except (CameraError, AuthRequired) as e:
            self._on_failure(item, e, unreachable=isinstance(e, AuthRequired))
        except Exception as e:  # не даём потоку умереть
            log.exception("Неожиданная ошибка отправки %s", item.cmd)
            self._on_failure(item, e, unreachable=False)
        else:
            with self._cond:
                if item.key is not None:
                    self._st.last_sent[item.key] = item.cmd
                self._unreachable_streak = 0
                self.last_error = None
            if item.future:
                item.future.set_result(resp)
            self._set_online(True)

    def _on_failure(self, item: _Item, err: Exception, unreachable: bool) -> None:
        self.last_error = str(err)
        log.warning("Команда %s не прошла: %s", item.cmd, err)
        if item.future:
            item.future.set_exception(err)
        with self._cond:
            if unreachable:
                self._unreachable_streak += 1
                if self._unreachable_streak >= UNREACHABLE_BACKOFF_AFTER:
                    self._backoff_until = self._clock() + UNREACHABLE_BACKOFF_S
            else:
                item.errors += 1
            if item.key is not None and item.key not in self._st.slots:
                if item.errors < MAX_CAMERA_ERROR_RETRIES:
                    self._st.slots[item.key] = item
                else:
                    log.error("Команда %s отброшена после %d ошибок", item.cmd, item.errors)
        if unreachable:
            self._set_online(False)

    def _set_online(self, value: bool) -> None:
        if self._online is value:
            return
        self._online = value
        cb = self.on_state_change
        if cb:
            try:
                cb(value)
            except Exception:
                log.exception("on_state_change упал")

    def _loop(self) -> None:
        while True:
            with self._cond:
                if not self._running:
                    return
                wait = self._wait_s(self._clock())
                if wait is None or wait > 0:
                    self._cond.wait(timeout=wait if wait is not None else 0.5)
                    continue
                item = self._take_due(self._clock())
            if item is not None:
                self._execute(item)
