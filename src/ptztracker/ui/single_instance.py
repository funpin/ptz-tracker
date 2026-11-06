"""Одна копия программы. Две копии читают один геймпад и ведут каждая «свою» камеру — стик двигает сразу
две камеры (площадка 2026-11-06). Вторая копия не запускается и поднимает окно первой."""

from __future__ import annotations

import getpass

from PySide6.QtNetwork import QLocalServer, QLocalSocket

WAIT_MS = 300


def server_name() -> str:
    return f"ptztracker-{getpass.getuser()}"


def already_running(name: str) -> bool:
    """Первая копия отвечает? Заодно просит её показать окно."""
    s = QLocalSocket()
    s.connectToServer(name)
    if not s.waitForConnected(WAIT_MS):
        return False
    s.write(b"show")
    s.waitForBytesWritten(WAIT_MS)
    s.disconnectFromServer()
    return True


def claim(name: str | None = None) -> QLocalServer | None:
    """Занять имя: сервер первой копии (держать живым, пока работает окно) или None — программа уже запущена."""
    name = name or server_name()
    if already_running(name):
        return None
    QLocalServer.removeServer(name)  # имя от упавшей копии (Unix-сокет остался) — освободить
    server = QLocalServer()
    return server if server.listen(name) else None
