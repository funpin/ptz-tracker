"""Приём видео с камеры по сети (RTSP/HTTP-MJPEG) через FFmpeg (PyAV).

Хранится только последний кадр: для слежения важна свежесть, а не полнота.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable
from urllib.parse import quote, unquote, urlsplit

import av

from ptztracker.video.frames import Frame, FpsMeter, SourceStats

log = logging.getLogger(__name__)

RECONNECT_DELAYS = (1.0, 2.0, 5.0)


def with_credentials(url: str, username: str | None, password: str | None) -> str:
    """Вставить логин/пароль в адрес потока (FFmpeg берёт их только из URL)."""
    if not username:
        return url
    auth = quote(username, safe="") + ":" + quote(password or "", safe="") + "@"
    return re.sub(r"^([a-z]+://)", lambda m: m.group(1) + auth, strip_credentials(url), count=1)


def strip_credentials(url: str) -> str:
    """Убрать логин/пароль из адреса (для сохранения в настройки)."""
    return re.sub(r"^([a-z]+://)[^/@]*@", r"\1", url, count=1)


STREAM_CHOICES = {
    "auto": "Авто",
    "h264_1": "H.264 (1)",
    "h264_2": "H.264 (2)",
    "mjpeg": "JPEG (MJPEG)",
}


def stream_url(host: str, stream: str) -> str:
    """Адрес конкретного потока камеры (без логина)."""
    return {
        "h264_1": f"rtsp://{host}/MediaInput/h264/stream_1",
        "h264_2": f"rtsp://{host}/MediaInput/h264/stream_2",
        "mjpeg": f"http://{host}/cgi-bin/mjpeg?resolution=640x360&framerate=25",
    }[stream]


def stream_urls(
    host: str, stream: str, username: str | None, password: str | None, saved_url: str | None
) -> list[str]:
    """Что пробовать: выбранный оператором поток — ровно он; «Авто» — сохранённый адрес, затем известные варианты."""
    if stream != "auto":
        return [with_credentials(stream_url(host, stream), username, password)]
    urls = rtsp_candidates(host, username, password)
    if saved_url:
        urls.insert(0, with_credentials(saved_url, username, password))
    return list(dict.fromkeys(urls))


def rtsp_candidates(host: str, username: str | None, password: str | None) -> list[str]:
    """Известные варианты адресов потока Panasonic. Рабочий подбирается перебором и запоминается.

    Порядок — по проверке на живой AW-HE42 (2026-10-19): MJPEG даёт задержку ~344 мс
    против ~830 мс у H.264 (stream_1/stream_2), поэтому идёт первым; /MediaInput/h264
    и /MediaInput/stream_1 отвечают 5XX.
    """
    base = [
        stream_url(host, "mjpeg"),
        f"rtsp://{host}/MediaInput/h264/stream_1",
        f"rtsp://{host}:554/MediaInput/h264/stream_1",
        f"rtsp://{host}/MediaInput/h264",
        f"rtsp://{host}/MediaInput/stream_1",
    ]
    return [with_credentials(u, username, password) for u in base]


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
    """Приём потока. Если адресов несколько — пробует по очереди, пока один не заработает,
    и дальше держится за него (сообщая о нём через on_found)."""

    def __init__(
        self,
        urls: str | list[str],
        open_timeout_s: float = 5.0,
        on_found: Callable[[str], None] | None = None,
    ):
        self._urls = [urls] if isinstance(urls, str) else list(urls)
        self._idx = 0
        self._found = False
        self.on_found = on_found
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

    @property
    def urls(self) -> list[str]:
        return list(self._urls)

    @property
    def url(self) -> str:
        return self._urls[self._idx]

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
                where = f" (адрес {self._idx + 1} из {len(self._urls)})" if len(self._urls) > 1 else ""
                with self._lock:
                    self._connected = False
                    self._latest = None
                    self._error = f"Нет видео{where}: {msg}"
                log.info("Поток %s: %s", redact_url(self.url), msg)
                if not self._found and len(self._urls) > 1:
                    # рабочий адрес ещё не найден — сразу пробуем следующий, после полного круга — пауза
                    self._idx = (self._idx + 1) % len(self._urls)
                    if self._idx != 0:
                        continue
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
                if not self._found:
                    self._found = True
                    log.info("Видеопоток найден: %s", redact_url(self.url))
                    if self.on_found:
                        self.on_found(self.url)
                with self._lock:
                    self._seq += 1
                    self._latest = Frame(img, now, self._seq)
                    self._size = (img.shape[1], img.shape[0])
                    self._connected = True
                    self._error = None
                    self._fps.tick(now)
        finally:
            container.close()
