"""Фальшивый микшер ATEM для тестов клиента Tally (не тестовый модуль).

Байты собраны здесь вручную, независимо от ptztracker.tally.atem_protocol.
"""

from __future__ import annotations

import socket
import struct
import threading
import time

SERVER_SESSION = 0x8001
TICK_S = 0.2


def _packet(flags: int, session: int, ack_id: int, packet_id: int, payload: bytes = b"") -> bytes:
    length = 12 + len(payload)
    return bytes([flags | (length >> 8 & 0x07), length & 0xFF]) + struct.pack(
        ">HH4xH", session, ack_id, packet_id
    ) + payload


def _tlin(flags: tuple[int, ...]) -> bytes:
    body = struct.pack(">H", len(flags)) + bytes(flags)
    return struct.pack(">HH4s", 8 + len(body), 0, b"TlIn") + body


class FakeAtem:
    """TlIn шлёт, как настоящий ATEM: в полном состоянии после hello и при смене Tally.
    Периодически — только пустые keepalive. dump_on_hello=False — ответ на hello без состояния."""

    def __init__(self, dump_on_hello: bool = True):
        self.dump_on_hello = dump_on_hello
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.settimeout(0.05)
        self._lock = threading.Lock()
        self._flags: tuple[int, ...] = ()
        self._client: tuple[str, int] | None = None
        self._paused = False
        self._next_id = 1
        self.sent_ids: set[int] = set()
        self.acked_ids: list[int] = []
        self.ack_sessions: set[int] = set()
        self.hellos = 0
        self.hello_addrs: list[tuple[str, int]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="fake-atem", daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return self._sock.getsockname()[1]

    @property
    def acked_count(self) -> int:
        """Подтверждения пакетов сессии микшера."""
        with self._lock:
            return len(self.acked_ids)

    @property
    def unknown_acks(self) -> int:
        """Подтверждения номеров, которых микшер не посылал."""
        with self._lock:
            return sum(1 for i in self.acked_ids if i not in self.sent_ids)

    @property
    def hello_acked(self) -> bool:
        """Клиент подтвердил ответ на hello (session 0x1337)."""
        with self._lock:
            return 0x1337 in self.ack_sessions

    def set_tally(self, flags: tuple[int, ...]) -> None:
        with self._lock:
            self._flags = tuple(flags)
            send = self._client is not None and not self._paused
        if send:
            self._send_tlin()

    def send_tlin_raw(self, flags: tuple[int, ...], packet_id: int, resend: bool = False) -> None:
        """TlIn с заданным номером пакета: запоздалый или повторный пакет."""
        with self._lock:
            client = self._client
            self.sent_ids.add(packet_id)
        assert client is not None, "клиент ещё не подключился"
        fl = 0x08 | (0x20 if resend else 0)
        self._sock.sendto(_packet(fl, SERVER_SESSION, 0, packet_id, _tlin(tuple(flags))), client)

    @property
    def last_id(self) -> int:
        """Номер последнего отправленного пакета."""
        with self._lock:
            return self._next_id - 1

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    def send_garbage(self) -> None:
        with self._lock:
            client = self._client
        assert client is not None, "клиент ещё не подключился"
        good_header = _packet(0x08, SERVER_SESSION, 0, 0xFFF0)
        for data in (
            b"\xff" * 7,  # короче заголовка
            b"\x00" * 300,  # длина в заголовке не совпадает
            good_header[:-1],  # обрезанный заголовок
            _packet(0x08, SERVER_SESSION, 0, 0xFFF1, struct.pack(">HH4s", 40, 0, b"TlIn") + b"\x00"),  # обрезанная команда
            _packet(0x08, SERVER_SESSION, 0, 0xFFF2, struct.pack(">HH4s", 9, 0, b"TlIn") + b"\x00"),  # короткий TlIn
            _packet(0x08, SERVER_SESSION, 0, 0xFFF3, struct.pack(">HH4s", 0, 0, b"\xff\xfe\xfd\xfc")),
            bytes(range(256)) * 3,
        ):
            self._sock.sendto(data, client)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(1.0)
        self._sock.close()

    # --- поток ---------------------------------------------------------

    def _loop(self) -> None:
        next_tick = 0.0
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(2048)
            except TimeoutError:
                data, addr = None, None
            except OSError:
                if self._stop.is_set():
                    return
                data, addr = None, None
            with self._lock:
                paused = self._paused
            if data is not None and not paused:
                self._handle(data, addr)
            now = time.monotonic()
            if now >= next_tick:
                next_tick = now + TICK_S
                if not paused:
                    self._tick()

    def _handle(self, data: bytes, addr) -> None:
        if len(data) < 12:
            return
        flags = data[0] & 0xF8
        session, ack_id = struct.unpack_from(">HH", data, 2)
        if flags & 0x10 and len(data) == 20 and data[12] == 0x01:
            with self._lock:
                self._client = addr
                self.hellos += 1
                self.hello_addrs.append(addr)
            reply = _packet(0x10, session, 0, 0, bytes([0x02]) + bytes(7))
            self._sock.sendto(reply, addr)
            if self.dump_on_hello:
                self._send_tlin()
        elif flags & 0x80:
            with self._lock:
                if session == SERVER_SESSION:
                    self.acked_ids.append(ack_id)
                self.ack_sessions.add(session)

    def _next_ids(self, n: int) -> tuple[int, ...]:
        with self._lock:
            ids = tuple(range(self._next_id, self._next_id + n))
            self._next_id += n
            self.sent_ids.update(ids)
            return ids

    def _send_tlin(self) -> None:
        with self._lock:
            client, flags = self._client, self._flags
        if client is None:
            return
        (pid,) = self._next_ids(1)
        self._sock.sendto(_packet(0x08, SERVER_SESSION, 0, pid, _tlin(flags)), client)

    def _tick(self) -> None:
        with self._lock:
            client = self._client
        if client is None:
            return
        (pid,) = self._next_ids(1)
        self._sock.sendto(_packet(0x08, SERVER_SESSION, 0, pid), client)  # keepalive
