"""Приём видео с камеры по сети (RTSP/HTTP-MJPEG) через FFmpeg (PyAV).

Хранится только последний кадр: для слежения важна свежесть, а не полнота.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from typing import Callable
from urllib.parse import quote, unquote, urlsplit

import av

from ptztracker.video.frames import FpsMeter, Frame, SourceStats

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
    "mjpeg_1": "MJPEG (1) · 640×360, 25 к/с",
    "mjpeg_2": "MJPEG (2) · 1280×720, 12 к/с",
    "h264_1": "H.264 (1)",
}


def stream_url(host: str, stream: str) -> str:
    """Адрес конкретного потока камеры (без логина). «h264_2» оператору не предлагается — нужен диагностике."""
    return {
        "h264_1": f"rtsp://{host}/MediaInput/h264/stream_1",
        "h264_2": f"rtsp://{host}/MediaInput/h264/stream_2",
        "mjpeg_1": f"http://{host}/cgi-bin/mjpeg?resolution=640x360&framerate=25",
        # замер 2026-10-29: без framerate камера отдаёт 5 к/с, с framerate=25 — 12 к/с (максимум для 1280×720)
        "mjpeg_2": f"http://{host}/cgi-bin/mjpeg?resolution=1280x720&framerate=25",
    }[stream]


def with_mjpeg_framerate(url: str) -> str:
    """Адрес MJPEG камеры без framerate дополнить: без него камера даёт 5 к/с (замер 2026-10-29)."""
    if "/cgi-bin/mjpeg" not in url or "framerate=" in url:
        return url
    return url + ("&" if "?" in url else "?") + "framerate=25"


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
        stream_url(host, "mjpeg_1"),
        f"rtsp://{host}/MediaInput/h264/stream_1",
        f"rtsp://{host}:554/MediaInput/h264/stream_1",
        f"rtsp://{host}/MediaInput/h264",
        f"rtsp://{host}/MediaInput/stream_1",
    ]
    return [with_credentials(u, username, password) for u in base]


def redact_url(url: str) -> str:
    """rtsp://user:pass@host/… → rtsp://user:***@host/… (для экрана и логов).

    Пароль может содержать «@», поэтому он тянется до ПОСЛЕДНЕЙ «@» перед адресом камеры.
    Пробелы и кавычки ограничивают адрес, если он стоит внутри текста или JSON.
    """
    return re.sub(r"(//[^/@:\s\"']*:)[^/\s\"']*@", r"\1***@", url)


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


TAP_QUEUE = 600  # пакетов: ~12 с при 50 к/с; запись отстала сильнее (диск) — пакеты теряются, просмотр не ждёт


class PacketTap:
    """Пакеты потока для второго получателя — записи в файл. Камера отдаёт H.264 по RTSP одному клиенту
    (AW-HE40, клуб, 2026-11-10: второму — «5XX Server Error»), поэтому запись не подключается сама, а берёт
    пакеты у просмотра.

    Элемент очереди — (номер подключения, пакет, поток). Поток закрытого подключения трогать нельзя («Container is
    not open»): получатель под `lock` проверяет `alive(номер)`, прежде чем создавать по нему файл, а источник под
    тем же замком отмечает подключение закрытым до того, как закроет его."""

    def __init__(self, maxsize: int = TAP_QUEUE):
        self._q: queue.Queue = queue.Queue(maxsize)
        self.lock = threading.Lock()
        self.retired = 0  # подключения с номером до этого включительно закрыты
        self.dropped = 0

    def put(self, epoch: int, packet, stream) -> None:
        try:
            self._q.put_nowait((epoch, packet, stream))
        except queue.Full:
            self.dropped += 1

    def get(self, timeout: float):
        """Следующий (номер подключения, пакет, поток) или None, если за timeout ничего не пришло."""
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def alive(self, epoch: int) -> bool:
        return epoch > self.retired

    def retire(self, epoch: int) -> None:
        with self.lock:
            self.retired = max(self.retired, epoch)


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
        self._taps: tuple[PacketTap, ...] = ()
        self._epoch = 0  # номер подключения: растёт с каждым открытием потока

    @property
    def urls(self) -> list[str]:
        return list(self._urls)

    @property
    def url(self) -> str:
        return self._urls[self._idx]

    @property
    def shares_h264(self) -> bool:
        """Просмотр идёт по RTSP (H.264 камеры): записи нельзя подключаться вторым клиентом — пусть берёт пакеты
        отсюда. В «Авто» — только когда адрес уже найден: до этого источник перебирает адреса."""
        return self.url.startswith("rtsp://") and (self._found or len(self._urls) == 1)

    def tap(self) -> PacketTap:
        """Подписаться на пакеты потока (как есть, без пережатия)."""
        t = PacketTap()
        with self._lock:
            self._taps += (t,)
        return t

    def untap(self, tap: PacketTap) -> None:
        with self._lock:
            self._taps = tuple(t for t in self._taps if t is not tap)

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
        self._epoch += 1
        epoch = self._epoch
        try:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            # пакеты, а не container.decode(): те же пакеты уходят подписчикам (запись в файл). Последний пустой
            # пакет demux выталкивает из декодера оставшиеся кадры — как делал decode()
            for packet in container.demux(stream):
                if self._stop.is_set():
                    return
                for vf in packet.decode():
                    self._publish(vf)
                taps = self._taps
                if taps and not (packet.dts is None and packet.size == 0):
                    for t in taps:
                        t.put(epoch, packet, stream)
        finally:
            for t in self._taps:
                t.retire(epoch)  # до close(): получатель не начнёт файл по потоку закрытого подключения
            container.close()

    def _publish(self, vf) -> None:
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
