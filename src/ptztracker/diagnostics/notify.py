"""Приёмник уведомлений камеры об изменениях (камера сама подключается к ПК по TCP).

Формат сообщений Panasonic здесь не разбирается — диагностика сохраняет
сырые байты, чтобы по ним написать разбор на следующем этапе.
"""

from __future__ import annotations

import socket
import threading
import time


class NotificationListener:
    def __init__(self, port: int = 31004):
        self._want_port = port
        self._sock: socket.socket | None = None
        self._events: list[tuple[float, bytes]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    @property
    def port(self) -> int:
        assert self._sock is not None, "приёмник не запущен"
        return self._sock.getsockname()[1]

    def start(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", self._want_port))
        s.listen(5)
        s.settimeout(0.2)
        self._sock = s
        t = threading.Thread(target=self._accept_loop, name="notify-accept", daemon=True)
        t.start()
        self._threads.append(t)

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            t = threading.Thread(target=self._read_loop, args=(conn,), name="notify-read", daemon=True)
            t.start()
            self._threads.append(t)

    def _read_loop(self, conn: socket.socket) -> None:
        conn.settimeout(0.2)
        with conn:
            while not self._stop.is_set():
                try:
                    data = conn.recv(4096)
                except (TimeoutError, socket.timeout):
                    continue
                except OSError:
                    return
                if not data:
                    return
                with self._lock:
                    self._events.append((time.monotonic(), data))

    def events(self) -> list[tuple[float, bytes]]:
        with self._lock:
            return list(self._events)

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            self._sock.close()
        for t in self._threads:
            t.join(1.0)
