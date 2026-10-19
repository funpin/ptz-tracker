"""HTTP-сервер, изображающий CGI камеры Panasonic (aw_ptz / aw_cam) поверх SimPtz."""

from __future__ import annotations

import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ptztracker.sim.ptz_model import SimPtz

log = logging.getLogger(__name__)

PHYSICS_HZ = 100


class SimHttpServer:
    def __init__(self, sim: SimPtz, host: str = "127.0.0.1", port: int = 0):
        self.sim = sim
        self._host = host
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._threads: list[threading.Thread] = []
        self._running = threading.Event()

    @property
    def url_host(self) -> str:
        assert self._httpd is not None, "сервер не запущен"
        return f"{self._host}:{self._httpd.server_address[1]}"

    def start(self) -> int:
        sim = self.sim

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                u = urlparse(self.path)
                if u.path not in ("/cgi-bin/aw_ptz", "/cgi-bin/aw_cam"):
                    self.send_error(404)
                    return
                cmd = parse_qs(u.query).get("cmd", [""])[0]
                body = sim.handle(cmd).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                log.debug("sim http: " + fmt, *args)

        self._httpd = ThreadingHTTPServer((self._host, self._port), Handler)
        self._httpd.daemon_threads = True
        self._running.set()
        self._threads = [
            threading.Thread(target=self._httpd.serve_forever, name="sim-http", daemon=True),
            threading.Thread(target=self._physics, name="sim-physics", daemon=True),
        ]
        for t in self._threads:
            t.start()
        return self._httpd.server_address[1]

    def _physics(self) -> None:
        last = time.monotonic()
        while self._running.is_set():
            time.sleep(1 / PHYSICS_HZ)
            now = time.monotonic()
            self.sim.update(now - last)
            last = now

    def stop(self) -> None:
        self._running.clear()
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        for t in self._threads:
            t.join(2)
        self._threads = []
