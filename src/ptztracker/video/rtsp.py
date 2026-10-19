"""Приём видео с камеры по сети (RTSP/HTTP-MJPEG) через FFmpeg (PyAV).

Хранится только последний кадр: для слежения важна свежесть, а не полнота.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from urllib.parse import quote, unquote, urlsplit

import av

from ptztracker.video.frames import Frame, FpsMeter, SourceStats

log = logging.getLogger(__name__)

RECONNECT_DELAYS = (1.0, 2.0, 5.0)


def rtsp_candidates(host: str, username: str | None, password: str | None) -> list[str]:
    """Известные варианты адресов потока Panasonic (⚠ точный проверяется диагностикой)."""
    auth = ""
    if username:
        auth = quote(username, safe="") + ":" + quote(password or "", safe="") + "@"
    return [
        f"rtsp://{auth}{host}/MediaInput/h264",
        f"rtsp://{auth}{host}/MediaInput/h264/stream_1",
        f"rtsp://{auth}{host}:554/MediaInput/h264/stream_1",
        f"rtsp://{auth}{host}/MediaInput/stream_1",
        f"http://{auth}{host}/cgi-bin/mjpeg?resolution=640x360&framerate=30",
    ]


def redact_url(url: str) -> str:
    """rtsp://user:pass@host/… → rtsp://user:***@host/… (для экрана и логов)."""
    return re.sub(r"(//[^/@:]*:)[^/@]*@", r"\1***@", url)


def _redact_text(text: str, url: str) -> str:
    text = text.replace(url, redact_url(url))
    try:
        pw = urlsplit(url).password
    except ValueError:
        pw = None
    if pw:
        for s in {pw, unquote(pw)}:
            text = text.replace(s, "***")
    return text


def _options(url: str) -> dict[str, str]:
    opts = {"fflags": "nobuffer", "flags": "low_delay", "max_delay": "0"}
    if url.startswith("rtsp://"):
        opts["rtsp_transport"] = "tcp"
    return opts


class RtspSource:
    def __init__(self, url: str, open_timeout_s: float = 5.0):
        self.url = url
        self.open_timeout_s = open_timeout_s
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._seq = 0
        self._fps = FpsMeter()
        self._connected = False
        self._error: str | None = None
        self._size = (0, 0)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="rtsp", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Остановить приём. timeout=0 — не ждать потока (он может висеть в сетевом таймауте)."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None

    def latest(self) -> Frame | None:
        with self._lock:
            return self._latest

    def stats(self) -> SourceStats:
        with self._lock:
            return SourceStats(
                connected=self._connected,
                fps=self._fps.fps(time.monotonic()),
                width=self._size[0],
                height=self._size[1],
                error=self._error,
                url=redact_url(self.url),
            )

    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                self._read_stream()
                attempt = 0  # поток закончился штатно (файл) — переоткрыть сразу
            except Exception as e:
                msg = _redact_text(str(e), self.url)
                with self._lock:
                    self._connected = False
                    self._latest = None
                    self._error = f"Нет видео: {msg}"
                log.info("Поток %s: %s", redact_url(self.url), msg)
                delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
                attempt += 1
                self._stop.wait(delay)

    def _read_stream(self) -> None:
        container = av.open(
            self.url,
            options=_options(self.url),
            timeout=(self.open_timeout_s, self.open_timeout_s),
        )
        try:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            for vf in container.decode(stream):
                if self._stop.is_set():
                    return
                img = vf.to_ndarray(format="bgr24")
                now = time.monotonic()
                with self._lock:
                    self._seq += 1
                    self._latest = Frame(img, now, self._seq)
                    self._size = (img.shape[1], img.shape[0])
                    self._connected = True
                    self._error = None
                    self._fps.tick(now)
        finally:
            container.close()
