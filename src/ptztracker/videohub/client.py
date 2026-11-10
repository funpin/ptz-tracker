"""Клиент Blackmagic Videohub по TCP (порт 9990) в фоновом потоке.

Протокол — по описанию Blackmagic Videohub Ethernet Protocol v2.3; на живом хабе не проверено.
Поток Qt клиента не ждёт: route() только кладёт желаемое в одно место, UI читает snapshot() по таймеру.
Команда, поданная без связи, отбрасывается и позже не отправляется: выход мог уже уйти в эфир.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from ptztracker.videohub.protocol import PING, BlockReader, HubState, apply_block, route_command

log = logging.getLogger(__name__)

VIDEOHUB_PORT = 9990
CONNECT_TIMEOUT_S = 1.0
RETRY_S = 2.0  # нет связи — новая попытка раз в 2 с
PING_AFTER_S = 3.0  # тишина 3 с — PING
DEAD_AFTER_S = 6.0  # ни байта 6 с — связь потеряна, переподключаемся
STEP_S = 0.05  # шаг цикла: задержка отправки команды и реакции на stop()


@dataclass(frozen=True)
class HubSnapshot:
    online: bool = False
    state: HubState = field(default_factory=HubState)
    last_error: str | None = None  # "nak" — хаб отказал в последнем переключении


_OFFLINE = HubSnapshot()


class VideohubClient:
    def __init__(self, host: str, port: int = VIDEOHUB_PORT,
                 on_change: Callable[[HubSnapshot], None] | None = None):
        self.host = "" if host is None else str(host).strip()
        self.port = port
        self.on_change = on_change
        self._lock = threading.Lock()
        self._snap = _OFFLINE
        self._pending: tuple[int, int] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- публичный API -------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.host:
            log.info("Адрес Videohub не задан — хаб не подключаю")
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="videohub", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(0.2)  # поток-демон: застрявший в подключении сам доделает выход, онлайн не опубликует
        with self._lock:
            self._snap = _OFFLINE
            self._pending = None

    def snapshot(self) -> HubSnapshot:
        with self._lock:
            return self._snap

    def route(self, output: int, input: int) -> bool:
        """Подать на выход вход (оба с 1). Не ждёт сеть. False — связи нет, команда отброшена."""
        with self._lock:
            if not self._snap.online or not self._snap.state.present:
                return False
            self._pending = (output, input)  # быстрые переключения: уходит последнее
            return True

    # --- поток ---------------------------------------------------------

    def _run(self, stop: threading.Event) -> None:
        try:
            while not stop.is_set():
                sock = self._connect()
                if sock is None:
                    stop.wait(RETRY_S)
                    continue
                try:
                    self._session(sock, stop)
                finally:
                    sock.close()
                    with self._lock:
                        self._pending = None
                    self._publish(_OFFLINE)
                if not stop.is_set():
                    log.warning("Videohub %s: нет связи", self.host)
                    stop.wait(STEP_S)
        finally:
            self._publish(_OFFLINE)

    def _connect(self) -> socket.socket | None:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT_S)
            sock.settimeout(STEP_S)
            return sock
        except Exception as e:  # OSError сети, а также ValueError/UnicodeError от кривого адреса
            log.debug("Videohub %s: не подключился: %s", self.host, e)
            return None

    def _session(self, sock: socket.socket, stop: threading.Event) -> None:
        reader = BlockReader()
        state = HubState()
        error: str | None = None
        online = False
        sent: deque[str] = deque()  # что ждёт ACK/NAK: "route" | "ping"
        now = time.monotonic()
        last_rx = last_tx = now
        with self._lock:
            self._pending = None  # старое желание до связи не отправляем
        while not stop.is_set():
            now = time.monotonic()
            if online:
                with self._lock:
                    want, self._pending = self._pending, None
                if want is not None:
                    try:
                        sock.sendall(route_command(*want))
                    except OSError as e:
                        log.debug("Videohub %s: команда не отправлена: %s", self.host, e)
                        return
                    sent.append("route")
                    last_tx = now
                    log.info("Videohub %s: выход %d ← вход %d", self.host, *want)
            if now - last_rx > DEAD_AFTER_S:
                return
            if now - max(last_rx, last_tx) > PING_AFTER_S:
                try:
                    sock.sendall(PING)
                except OSError:
                    return
                sent.append("ping")
                last_tx = now
            try:
                data = sock.recv(65536)
            except TimeoutError:
                continue
            except OSError as e:
                log.debug("Videohub %s: ошибка приёма: %s", self.host, e)
                return
            if not data:
                return
            last_rx = time.monotonic()
            for b in reader.feed(data):
                if b.header in ("ACK", "NAK"):
                    kind = sent.popleft() if sent else None
                    if kind == "route":
                        if b.header == "NAK":
                            log.warning("Videohub %s: хаб отказал в переключении (NAK)", self.host)
                        error = "nak" if b.header == "NAK" else None
                    continue
                state = apply_block(state, b)
            if not online and state.present is not None:
                online = True
                log.info("Videohub %s: на связи, %s", self.host, state.model or "модель не сообщена")
            if online:
                self._publish(HubSnapshot(True, state, error), stop)

    def _publish(self, snap: HubSnapshot, stop: threading.Event | None = None) -> None:
        with self._lock:
            if snap.online and stop is not None and stop.is_set():
                return  # остановленный поток не оживляет снимок
            if snap == self._snap:
                return
            self._snap = snap
        cb = self.on_change
        if cb is None:
            return
        try:
            cb(snap)
        except Exception:
            log.exception("on_change Videohub упал")
