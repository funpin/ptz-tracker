"""HTTP-сервер API и передача действий в поток Qt.

Сервер не знает об окне: он проверяет токен, разбирает запрос и зовёт методы `Backend`.
Обработчики работают в потоках сервера. Всё, что трогает окно, `Backend` передаёт в поток Qt
через `QtInvoker` и ждёт ответа не дольше 2 с; сам поток Qt никогда не ждёт сервер.
"""

from __future__ import annotations

import hmac
import json
import logging
import math
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Protocol
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import QObject, Qt, QThread, Signal

log = logging.getLogger(__name__)

HOST = "127.0.0.1"  # только этот компьютер: снаружи API недоступен
CONTROL_TIMEOUT_S = 2.0
STARTED_GRACE_S = 5.0  # действие уже выполняется в Qt — даём ему закончить, а не бросаем на середине
MAX_BODY = 64 * 1024
CONTROL_ENDPOINTS = ("follow", "target", "composition", "effort", "calibrate")


class ApiError(Exception):
    """Ответ с ошибкой: HTTP-код и понятное сообщение по-русски."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Backend(Protocol):
    def status(self) -> dict: ...

    def events(self, since: float, limit: int) -> dict: ...

    def frame_jpeg(self) -> bytes | None: ...

    def save_moment(self) -> dict: ...

    def control(self, name: str, body: dict) -> dict: ...


# --- передача действий в поток Qt -----------------------------------------


class _Job:
    """Одно действие для потока Qt. Если ответ уже не ждут, оно не выполняется вовсе."""

    def __init__(self, fn: Callable[[], Any]):
        self.fn = fn
        self._lock = threading.Lock()
        self._started = False
        self._cancelled = False
        self._done = threading.Event()
        self._value: Any = None
        self._error: Exception | None = None

    def run(self) -> None:  # поток Qt
        with self._lock:
            if self._cancelled:
                return
            self._started = True
        try:
            self._value = self.fn()
        except Exception as e:  # ошибка уходит в поток сервера
            self._error = e
        finally:
            self._done.set()

    def wait(self, timeout: float) -> Any:  # поток сервера
        if not self._done.wait(timeout):
            with self._lock:
                if not self._started:
                    self._cancelled = True
                    raise ApiError(504, "Окно программы не ответило за 2 с — действие не выполнено.")
            if not self._done.wait(STARTED_GRACE_S):
                raise ApiError(504, "Действие выполняется дольше обычного — проверьте окно программы.")
        if self._error is not None:
            raise self._error
        return self._value


class QtInvoker(QObject):
    """Выполняет функцию в потоке Qt (где создан объект) и возвращает результат вызвавшему потоку."""

    _call = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._call.connect(self._run, Qt.QueuedConnection)

    def call(self, fn: Callable[[], Any], timeout: float = CONTROL_TIMEOUT_S) -> Any:
        if QThread.currentThread() is self.thread():  # уже в потоке Qt: ждать самого себя нельзя
            return fn()
        job = _Job(fn)
        try:
            self._call.emit(job)
        except RuntimeError as e:  # окно уже закрыто
            raise ApiError(503, "Окно программы закрывается.") from e
        return job.wait(timeout)

    def _run(self, job: _Job) -> None:
        job.run()


# --- HTTP ----------------------------------------------------------------


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows с SO_REUSEADDR пускает второй сервер на занятый порт — там его не включаем
    allow_reuse_address = os.name != "nt"

    def __init__(self, port: int, backend: Backend, token: str):
        self.backend = backend
        self.token = token.encode()
        super().__init__((HOST, port), _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def log_message(self, format, *args):  # noqa: A002 — без заголовков: токен в лог не попадает
        log.debug("API %s %s", self.command, urlsplit(self.path).path)

    def _handle(self, method: str) -> None:
        self._body_read = method != "POST"
        try:
            if not self._authorized():
                raise ApiError(401, "Нужен заголовок Authorization: Bearer <api_token из config.json>.")
            url = urlsplit(self.path)
            name = url.path.strip("/")
            body = self._body() if method == "POST" else {}
            b = self.server.backend
            if method == "GET" and name == "status":
                self._json(200, b.status())
            elif method == "GET" and name == "events":
                q = parse_qs(url.query)
                since = _number(q.get("since", ["-inf"])[0], "since")
                limit = int(min(2000, max(1, _number(q.get("limit", ["500"])[0], "limit"))))
                self._json(200, b.events(since, limit))
            elif method == "GET" and name == "frame.jpg":
                jpeg = b.frame_jpeg()
                if jpeg is None:
                    raise ApiError(503, "Кадра пока нет.")
                self._send(200, "image/jpeg", jpeg)
            elif method == "POST" and name == "moment":
                self._json(200, b.save_moment())
            elif method == "POST" and name in CONTROL_ENDPOINTS:
                self._json(200, b.control(name, body))
            elif name in ("status", "events", "frame.jpg", "moment", *CONTROL_ENDPOINTS):
                raise ApiError(405, f"Метод {method} для /{name} не поддерживается.")
            else:
                raise ApiError(404, f"Нет такого адреса: /{name}")
        except ApiError as e:
            if not self._body_read:  # непрочитанное тело сбило бы следующий запрос в том же соединении
                self.close_connection = True
            self._json(e.status, {"error": e.message})
        except Exception as e:
            log.exception("API: ошибка обработки %s", urlsplit(self.path).path)
            if not self._body_read:
                self.close_connection = True
            self._json(500, {"error": f"Внутренняя ошибка: {type(e).__name__}"})

    def _authorized(self) -> bool:
        token = self.server.token
        got = self.headers.get("Authorization", "")
        scheme, _, value = got.partition(" ")
        return bool(token) and scheme.lower() == "bearer" and hmac.compare_digest(value.strip().encode(), token)

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError as e:
            raise ApiError(400, "Неправильный Content-Length.") from e
        if n < 0 or n > MAX_BODY:
            raise ApiError(413, "Слишком большой запрос.")
        raw = self.rfile.read(n) if n else b""
        self._body_read = True
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise ApiError(400, "Тело запроса должно быть JSON-объектом.") from e
        if not isinstance(data, dict):
            raise ApiError(400, "Тело запроса должно быть JSON-объектом.")
        return data

    def _json(self, status: int, data: dict) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False).encode())

    def _send(self, status: int, ctype: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)


def _number(s: str, what: str) -> float:
    try:
        v = float(s)
    except (TypeError, ValueError) as e:
        raise ApiError(400, f"{what}: нужно число.") from e
    if math.isnan(v):
        raise ApiError(400, f"{what}: нужно число.")
    return v


class ApiServer:
    """Сервер API в фоновом потоке. `start()` бросает OSError, если порт занят."""

    def __init__(self, backend: Backend, token: str, port: int):
        self.backend = backend
        self._token = token
        self._want_port = port
        self._httpd: _Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._httpd = _Server(self._want_port, self.backend, self._token)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.1}, name="agent-api", daemon=True
        )
        self._thread.start()
        log.info("API агента: http://%s:%s", HOST, self.port)

    @property
    def address(self) -> tuple[str, int]:
        if self._httpd is None:
            raise RuntimeError("сервер не запущен")
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    @property
    def port(self) -> int:
        return self.address[1]

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is None:
            return
        httpd.shutdown()  # serve_forever выходит за ~0,1 с
        httpd.server_close()
