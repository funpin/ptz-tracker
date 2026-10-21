"""Клиент Tally микшера Blackmagic ATEM по UDP (порт 9910) в фоновом потоке.

Протокол реализован по открытым описаниям; на живом ATEM не проверено.
Поток Qt клиента не ждёт: UI читает snapshot() по таймеру. Qt здесь не импортируется.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ptztracker.tally.atem_protocol import (
    FLAG_ACK_REQUEST,
    FLAG_HELLO,
    HEADER_LEN,
    HELLO_ACCEPTED,
    TALLY_PGM,
    TALLY_PVW,
    ack_packet,
    hello_packet,
    iter_commands,
    parse_header,
    parse_tlin,
)

log = logging.getLogger(__name__)

ATEM_PORT = 9910
HELLO_INTERVAL_S = 1.0  # пока нет связи — hello раз в секунду
RECV_TIMEOUT_S = 0.2  # шаг цикла: от него зависит, как быстро срабатывают stop() и «нет связи»

TallyState = Literal["pgm", "pvw"] | None


@dataclass(frozen=True)
class TallySnapshot:
    online: bool
    flags: tuple[int, ...]  # элемент i — вход i+1

    def state(self, input: int) -> TallyState:
        if not isinstance(input, int) or not 1 <= input <= len(self.flags):
            return None
        f = self.flags[input - 1]
        if f & TALLY_PGM:
            return "pgm"  # оба бита — тоже эфир
        if f & TALLY_PVW:
            return "pvw"
        return None


_OFFLINE = TallySnapshot(online=False, flags=())


class AtemTally:
    def __init__(
        self,
        host: str,
        port: int = ATEM_PORT,
        timeout_s: float = 2.0,
        on_change: Callable[[TallySnapshot], None] | None = None,
    ):
        self.host = "" if host is None else str(host).strip()  # из конфига руками может прийти что угодно
        self.port = port
        self.timeout_s = timeout_s
        self.on_change = on_change
        self._lock = threading.Lock()
        self._snap = _OFFLINE
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- публичный API -------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.host:
            log.info("Адрес ATEM не задан — Tally не запускаю")
            return
        self._stop = threading.Event()  # своё событие у каждого потока: старый не оживёт после stop()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="atem-tally", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(0.45)
        with self._lock:
            self._snap = _OFFLINE

    def snapshot(self) -> TallySnapshot:
        with self._lock:
            return self._snap

    def tally(self, input: int) -> TallyState:
        return self.snapshot().state(input)

    @property
    def online(self) -> bool:
        """На связи и уже получен TlIn после последнего hello: только тогда tally() можно верить."""
        return self.snapshot().online

    # --- поток ---------------------------------------------------------
    #
    # Безопасность: снимок online=True только после TlIn, полученного с последнего hello.
    # Пустые флаги до этого означали бы «не в эфире», хотя вход может быть в PGM.
    # Пока TlIn нет — hello раз в 1 с: микшер в ответ шлёт полное состояние.

    def _run(self, stop: threading.Event) -> None:
        sock: socket.socket | None = None
        self._last_rx: float | None = None
        self._synced = False
        last_hello = float("-inf")
        fresh_socket = True  # после обрыва новая сессия идёт с нового порта
        try:
            while not stop.is_set():
                now = time.monotonic()
                stale = self._last_rx is None or now - self._last_rx > self.timeout_s
                if stale and self._synced:
                    log.warning("ATEM %s: нет связи", self.host)
                if stale:
                    self._synced = False
                    fresh_socket = True
                    self._publish(_OFFLINE)
                if not self._synced and now - last_hello >= HELLO_INTERVAL_S:
                    last_hello = now
                    if fresh_socket and sock is not None:
                        sock.close()
                        sock = None
                    sock = self._send_hello(sock)
                    fresh_socket = False
                if sock is None:
                    stop.wait(RECV_TIMEOUT_S)
                    continue
                try:
                    data = sock.recv(2048)
                except TimeoutError:
                    continue
                except OSError as e:  # например, ICMP «порт недоступен» на прошлый hello
                    log.debug("ATEM %s: ошибка приёма: %s", self.host, e)
                    stop.wait(RECV_TIMEOUT_S)
                    continue
                try:
                    self._handle(sock, data)
                except Exception:  # мусор в пакете не должен ронять поток
                    log.debug("ATEM %s: не разобран пакет %r", self.host, data[:32], exc_info=True)
        finally:
            if sock is not None:
                sock.close()
            self._publish(_OFFLINE)

    def _send_hello(self, sock: socket.socket | None) -> socket.socket | None:
        try:
            if sock is None:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(RECV_TIMEOUT_S)
                sock.connect((self.host, self.port))  # принимаем пакеты только от микшера
            sock.send(hello_packet())
            return sock
        except Exception as e:  # OSError сети, а также TypeError/ValueError от кривого адреса
            log.debug("ATEM %s: hello не отправлен: %s", self.host, e)
            if sock is not None:
                sock.close()
            return None

    def _handle(self, sock: socket.socket, data: bytes) -> None:
        """Разобрать пакет микшера: подтвердить, обновить флаги."""
        h = parse_header(data)
        if h is None:
            return
        if h.flags & FLAG_HELLO:
            if len(data) > HEADER_LEN and data[HEADER_LEN] == HELLO_ACCEPTED:
                sock.send(ack_packet(h.session, 0))
                self._last_rx = time.monotonic()  # связь есть, но до TlIn — не online
            return
        self._last_rx = time.monotonic()
        if h.flags & FLAG_ACK_REQUEST:
            sock.send(ack_packet(h.session, h.packet_id))
        flags = None
        for name, body in iter_commands(data[HEADER_LEN:]):
            if name == "TlIn":
                parsed = parse_tlin(body)
                if parsed:
                    flags = parsed
        if flags is not None:
            if not self._synced:
                log.info("ATEM %s: на связи, Tally получен", self.host)
            self._synced = True
            self._publish(TallySnapshot(online=True, flags=flags))

    def _publish(self, snap: TallySnapshot) -> None:
        with self._lock:
            if snap == self._snap:
                return
            self._snap = snap
        cb = self.on_change
        if cb is None:
            return
        try:
            cb(snap)
        except Exception:
            log.exception("on_change Tally упал")
