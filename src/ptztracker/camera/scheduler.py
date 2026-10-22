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
from ptztracker.camera.protocol import STOP_FOCUS, STOP_PT, STOP_ZOOM, CameraError

log = logging.getLogger(__name__)

MAX_CAMERA_ERROR_RETRIES = 3
UNREACHABLE_BACKOFF_AFTER = 3
UNREACHABLE_BACKOFF_S = 1.0


class Priority(IntEnum):
    EMERGENCY = 0
    MANUAL = 1
    TRACKING = 2
    TRACKING_ZOOM = 3  # зум при слежении уступает повороту (спека §7)
    QUERY = 4


@dataclass
class _Item:
    cmd: str
    priority: Priority
    seq: int
    key: str | None = None
    future: Future | None = None
    errors: int = 0
    expires: float | None = None  # для разовых команд: не выполнять, если связь вернулась слишком поздно
    submitted: float = 0.0  # когда команда встала в очередь (для записи: сколько она ждала)


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
        on_sent: Callable[[dict], None] | None = None,
        queries_only: bool = False,
    ):
        self._send = send
        # режим наблюдения (--lab record при камере в эфире): только запросы, ни движений, ни стопов
        self.queries_only = queries_only
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
        # после каждой отправки: {"cmd", "key", "priority", "queued_s", "reply", "ok"} (для записи)
        self.on_sent = on_sent
        self._last_on_sent_log = float("-inf")
        self._thread: threading.Thread | None = None
        self._running = False
        self._inflight: _Item | None = None

    # --- публичный API -------------------------------------------------

    @property
    def online(self) -> bool:
        return bool(self._online)

    def submit(
        self,
        key: str,
        cmd: str,
        priority: Priority,
        only_if_changed: bool = True,
        ttl: float | None = None,
    ) -> None:
        if self.queries_only and priority != Priority.QUERY:
            log.debug("Режим наблюдения: команда %s не отправлена", cmd)
            return
        with self._cond:
            if only_if_changed and self._st.last_sent.get(key) == cmd:
                self._st.slots.pop(key, None)  # вернулись к уже отправленному — отменить ожидающую
                return
            now = self._clock()
            expires = now + ttl if ttl is not None else None
            self._st.slots[key] = _Item(cmd, priority, next(self._seq), key=key, expires=expires, submitted=now)
            self._cond.notify()

    def query(self, cmd: str, timeout: float = 2.0) -> str:
        fut: Future = Future()
        with self._cond:
            self._st.queries.append(_Item(cmd, Priority.QUERY, next(self._seq), future=fut, submitted=self._clock()))
            self._cond.notify()
        return fut.result(timeout=timeout)

    def emergency_stop(self) -> None:
        if self.queries_only:  # камерой управляет другой пульт: наш стоп заморозил бы её
            return
        with self._cond:
            self._st.slots.clear()
            for key, cmd in (("pt", STOP_PT), ("zoom", STOP_ZOOM), ("focus", STOP_FOCUS)):
                self._st.slots[key] = _Item(cmd, Priority.EMERGENCY, next(self._seq), key=key, submitted=self._clock())
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

    def wait_emergency(self, timeout: float) -> bool:
        """Дождаться отправки аварийных стопов (если камера на связи)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._cond:
                busy = any(i.priority == Priority.EMERGENCY for i in self._st.slots.values()) or (
                    self._inflight is not None and self._inflight.priority == Priority.EMERGENCY
                )
            if not busy:
                return True
            time.sleep(0.01)
        return False

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
        self._drop_expired(now)
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
        self._inflight = item
        return item

    def _drop_expired(self, now: float) -> None:
        for key, item in list(self._st.slots.items()):
            if item.expires is not None and now > item.expires:
                log.warning("Команда %s устарела (не было связи) и не будет выполнена", item.cmd)
                del self._st.slots[key]

    def _execute(self, item: _Item) -> None:
        sent_t = self._clock()
        try:
            ok, reply = self._send_and_record(item)
        finally:
            with self._cond:
                self._inflight = None
        self._report_sent(item, sent_t, ok, reply)

    def _report_sent(self, item: _Item, sent_t: float, ok: bool, reply: str) -> None:
        cb = self.on_sent
        if cb is None:
            return
        try:
            cb({"cmd": item.cmd, "key": item.key, "priority": item.priority.name,
                "queued_s": round(sent_t - item.submitted, 4), "reply": reply, "ok": ok})
        except Exception as e:  # запись — только наблюдатель: её сбой не мешает командам
            now = time.monotonic()
            if now - self._last_on_sent_log >= 10.0:
                self._last_on_sent_log = now
                log.warning("on_sent упал: %s", e)

    def _send_and_record(self, item: _Item) -> tuple[bool, str]:
        """Отправить команду; (успех, ответ камеры или текст ошибки)."""
        try:
            resp = self._send(item.cmd)
        except CameraUnreachable as e:
            self._on_failure(item, e, unreachable=True)
            return False, str(e)
        except (CameraError, AuthRequired) as e:
            self._on_failure(item, e, unreachable=isinstance(e, AuthRequired))
            return False, str(e)
        except Exception as e:  # не даём потоку умереть
            log.exception("Неожиданная ошибка отправки %s", item.cmd)
            self._on_failure(item, e, unreachable=False)
            return False, str(e)
        else:
            with self._cond:
                if item.key is not None:
                    self._st.last_sent[item.key] = item.cmd
                self._unreachable_streak = 0
                self.last_error = None
            if item.future:
                item.future.set_result(resp)
            self._set_online(True)
            return True, resp

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
            elif not (isinstance(err, CameraError) and err.code == "er2") and item.priority != Priority.EMERGENCY:
                # er2 «занята» (например, работает пульт) — повторять, пока команду не заменит новая;
                # аварийный стоп не отбрасывается никогда
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
