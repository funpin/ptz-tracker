"""Запись одной камеры в файл: свой поток, своё подключение. Только читает камеру и пишет на диск."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import av

from ptztracker.video.frames import FrameSource
from ptztracker.video.rtsp import redact_url
from ptztracker.videorec.writer import CopyWriter, EncodeWriter, file_stem

log = logging.getLogger(__name__)

RETRY_DELAYS_S = (2, 5, 10)  # камера отказала в подключении (2026-10-29: на 2–3 с при нескольких клиентах)
LIGHT_BIT_RATE = 3_000_000  # в среднем; на движении до LIGHT_MAX_RATE
LIGHT_MAX_RATE = 6_000_000
LIGHT_FPS = 25
OPEN_TIMEOUT_S = 5.0
COPY_BAD_MAX = 25  # пакетов подряд не прошло запись — поток считается непригодным для копии
COPY_RETRY_S = 10.0  # через столько после перехода на лёгкий файл пробуем копию заново
COPY_RETRY_MAX_S = 300.0
COPY_STABLE_S = 300.0  # копия после возврата продержалась меньше — следующая попытка вдвое позже
FOLDER_CHECK_S = 1.0
RATE_WINDOW_S = 60.0
SHARED_POLL_S = 0.2  # столько ждём пакет от просмотра за раз
SHARED_LOSS_S = 2.0  # пакетов от просмотра нет дольше — «нет сигнала»
SHARED_KEY_WAIT_S = 3.0  # файл начинаем с ключевого кадра; не дождались — с любого
_MODES = ("camera", "light")


@dataclass(frozen=True)
class RecState:
    active: bool = False
    signal: bool = True  # False — камеры нет, рекордер переподключается
    elapsed_s: float = 0.0
    bytes_total: int = 0
    rate_bps: float = 0.0
    error: str | None = None
    degraded: bool = False  # поток нельзя скопировать, идёт лёгкий файл: точка у REC оранжевая
    paths: tuple[Path, ...] = ()


IDLE = RecState()


class _DiskFailure(Exception):
    pass


def default_open_input(url: str):
    options = {"rtsp_transport": "tcp"} if url.startswith("rtsp://") else {}
    return av.open(url, options=options, timeout=(OPEN_TIMEOUT_S, OPEN_TIMEOUT_S))


class VideoRecorder:
    def __init__(self, camera_name: str, folder: Path, mode: str = "camera", *,
                 url: Callable[[], str] | None = None, frames: FrameSource | None = None,
                 open_input=default_open_input, split_bytes: int | None = None,
                 shared: Callable[[], object | None] | None = None):
        """shared() — источник просмотра (video.rtsp.RtspSource), если он уже принимает H.264 этой камеры по RTSP,
        иначе None. Камера отдаёт такой поток одному клиенту (AW-HE40, клуб, 2026-11-10: второму — 5XX, и REC
        показывал «нет сигнала» при выбранном H.264), поэтому запись берёт пакеты у просмотра."""
        if (url is None) == (frames is None):
            raise ValueError("нужен ровно один источник: url или frames")
        self._shared = shared
        self.camera_name, self.folder, self.split_bytes = camera_name, Path(folder), split_bytes
        self._mode = mode if mode in _MODES else "camera"
        self._url, self._frames, self._open_input = url, frames, open_input
        self._stem = file_stem(camera_name, datetime.now())
        self._lock = threading.Lock()
        self._state = IDLE
        self._notes: deque[str] = deque()
        self._t0 = 0.0
        self._stop = threading.Event()
        self._done = threading.Event()
        self._writer = None
        self._done_bytes = 0
        self._done_paths: tuple[Path, ...] = ()
        self._rate: deque[tuple[float, int]] = deque()
        self._next_check = 0.0
        self._announced_loss = False
        self._copy_bad = 0
        self._retry_at: float | None = None  # когда снова пробовать копию; None — лёгкий файл выбран оператором
        self._retry_s = COPY_RETRY_S
        self._tried_at: float | None = None  # последняя попытка вернуться к копии
        self._wait_key = False  # лёгкий файл после копии: декодер начинает с ключевого кадра

    @property
    def is_running(self) -> bool:
        return self.snapshot().active

    def start(self) -> None:
        self._t0 = time.monotonic()
        self._state = RecState(active=True)
        threading.Thread(target=self._run, name=f"video-{self.camera_name}", daemon=True).start()

    def stop(self) -> None:
        """Не ждёт поток: файл закрывается в фоне."""
        self._stop.set()
        self._set(active=False)

    def wait_closed(self, timeout: float) -> bool:
        return self._done.wait(timeout)

    def snapshot(self) -> RecState:
        with self._lock:
            s = self._state
            return replace(s, elapsed_s=time.monotonic() - self._t0) if s.active else s

    def take_note(self) -> str | None:
        """Следующее разовое сообщение для строки состояния."""
        with self._lock:
            return self._notes.popleft() if self._notes else None

    def _set(self, **kw) -> None:
        with self._lock:
            self._state = replace(self._state, **kw)

    def _note(self, text: str) -> None:
        with self._lock:
            self._notes.append(text)

    def _probe_folder(self) -> None:
        """Сразу, а не с первым кадром: иначе у недоступной камеры REC горел бы над папкой, куда нельзя писать."""
        self.folder.mkdir(parents=True, exist_ok=True)
        probe = self.folder / f".ptztracker-{os.getpid()}-{threading.get_ident()}.tmp"
        probe.touch()
        probe.unlink()

    def _run(self) -> None:
        try:
            self._probe_folder()
            if self._frames is not None:
                self._record_frames()
            else:
                self._record_stream()
        except (_DiskFailure, OSError):
            log.exception("Запись камеры %s остановлена", self.camera_name)
            self._set(error=f"«{self.camera_name}»: стоп, диск недоступен", active=False)
        except Exception:
            log.exception("Запись камеры %s: сбой", self.camera_name)
            self._set(error=f"«{self.camera_name}»: запись остановлена из-за ошибки", active=False)
        finally:
            self._close_writer()
            self._set(active=False)
            self._done.set()

    def _close_writer(self) -> None:
        w, self._writer = self._writer, None
        if w is None:
            return
        try:
            w.close()
        except Exception:
            log.exception("Запись камеры %s: файл закрыт с ошибкой", self.camera_name)
        self._done_bytes += w.bytes_written
        self._done_paths += tuple(w.paths)
        self._publish()

    def _publish(self) -> None:
        w = self._writer
        total = self._done_bytes + (w.bytes_written if w else 0)
        paths = self._done_paths + (tuple(w.paths) if w else ())
        now = time.monotonic()
        self._rate.append((now, total))
        while now - self._rate[0][0] > RATE_WINDOW_S:
            self._rate.popleft()
        dt = now - self._rate[0][0]
        rate = (total - self._rate[0][1]) / dt if dt >= 2.0 else 0.0
        self._set(bytes_total=total, paths=paths, rate_bps=rate)

    def _check_folder(self) -> None:
        now = time.monotonic()
        if now < self._next_check:
            return
        self._next_check = now + FOLDER_CHECK_S
        if self._state.paths and not self.folder.is_dir():
            raise _DiskFailure("папка исчезла")

    def _encoder(self, width: int, height: int, fps: int = LIGHT_FPS) -> EncodeWriter:
        return EncodeWriter(self.folder, self._stem, width, height, fps=fps,
                            bit_rate=LIGHT_BIT_RATE if self._mode == "light" else None,
                            max_rate=LIGHT_MAX_RATE if self._mode == "light" else None,
                            split_bytes=self.split_bytes)

    # --- кадры (симулятор) ---

    def _record_frames(self) -> None:
        last = -1
        while not self._stop.is_set():
            self._check_folder()
            f = self._frames.latest()
            if f is None or f.seq == last:
                time.sleep(0.005)
                continue
            last = f.seq
            if self._writer is None:
                self._writer = self._encoder(f.image.shape[1], f.image.shape[0])
            self._writer.write_frame(f.image, f.t_capture)
            self._publish()

    # --- камера по RTSP ---

    def _preview(self):
        return self._shared() if self._shared is not None else None

    def _record_stream(self) -> None:
        tries = 0
        while not self._stop.is_set():
            src = self._preview()
            if src is not None:
                try:
                    self._follow_preview(src)
                finally:
                    self._close_writer()
                continue  # просмотр перестал делиться потоком — дальше своим подключением
            url = self._url()
            try:
                container = self._open_input(url)
            except Exception as e:  # noqa: BLE001 — камера может отказать как угодно
                log.info("Запись %s: нет подключения к %s (%s)", self.camera_name, redact_url(url), type(e).__name__)
            else:
                try:
                    if self._pump(container):
                        tries = 0
                finally:
                    self._close_writer()
                    try:
                        container.close()
                    except Exception:
                        log.debug("Запись: контейнер закрыт с ошибкой", exc_info=True)
            if self._stop.is_set():
                return
            self._lost_signal()
            self._stop.wait(RETRY_DELAYS_S[min(tries, len(RETRY_DELAYS_S) - 1)])
            tries += 1

    # --- просмотр уже держит H.264 камеры: пакеты берём у него ---

    def _follow_preview(self, src) -> None:
        """Писать, пока просмотр `src` принимает H.264. Копия — из его пакетов, лёгкий файл — из его кадров."""
        tap = src.tap()
        try:
            epoch, last, wait_key_until, last_seq = None, time.monotonic(), None, -1
            next_slot: list[float | None] = [None]
            while not self._stop.is_set() and self._preview() is src:
                self._check_folder()
                now = time.monotonic()
                if now - last > SHARED_LOSS_S:
                    self._lost_signal()
                if self._mode != "camera":
                    f = src.latest()
                    fresh = f is not None and f.seq != last_seq
                    if fresh:
                        last, last_seq = now, f.seq
                        self._got_signal()
                        self._write_frame_paced(f, next_slot)
                        self._publish()
                    item = tap.get(0.0 if fresh else 0.005)  # очередь пакетов не копим; заодно пауза
                    if item is not None and self._copy_due(item[1], now):
                        with tap.lock:
                            if tap.alive(item[0]) and self._copy_again(item[1], item[2]):
                                epoch = item[0]
                        self._publish()
                    continue
                item = tap.get(SHARED_POLL_S)
                if item is None:
                    continue
                ep, packet, stream = item
                if ep != epoch:  # просмотр переподключился: у нового подключения свой поток — новый файл
                    self._close_writer()
                    epoch, wait_key_until = ep, None
                with tap.lock:  # источник не закроет подключение, пока по его потоку создаётся файл
                    if self._writer is None:
                        if not tap.alive(ep):
                            continue
                        wait_key_until = wait_key_until or now + SHARED_KEY_WAIT_S
                        if not packet.is_keyframe and now < wait_key_until:
                            continue
                    last = now
                    self._got_signal()
                    self._write_copy(packet, stream)
                self._publish()
        finally:
            src.untap(tap)

    def _write_frame_paced(self, f, next_slot) -> None:
        """Кадр просмотра в лёгкий файл, не чаще LIGHT_FPS."""
        t = f.t_capture
        if next_slot[0] is not None and t + 1e-6 < next_slot[0]:
            return
        step = 1 / LIGHT_FPS
        next_slot[0] = t + step if next_slot[0] is None or t - next_slot[0] > step else next_slot[0] + step
        if self._writer is None:
            self._writer = self._encoder(f.image.shape[1], f.image.shape[0])
        self._writer.write_frame(f.image, t)

    def _lost_signal(self) -> None:
        if self._state.signal or not self._announced_loss:
            self._set(signal=False)
            if not self._announced_loss:
                self._announced_loss = True
                self._note(f"«{self.camera_name}»: нет сигнала, запись ждёт")

    def _got_signal(self) -> None:
        if not self._state.signal:
            self._set(signal=True)
        self._announced_loss = False

    def _pump(self, container) -> bool:
        """Читать поток, пока он идёт. True — хотя бы один пакет пришёл."""
        stream = container.streams.video[0]
        packets = container.demux(stream)
        got = False
        next_slot: list[float | None] = [None]
        while not self._stop.is_set():
            self._check_folder()
            try:
                packet = next(packets, None)
            except OSError as e:
                log.info("Запись %s: обрыв потока (%s)", self.camera_name, type(e).__name__)
                return got
            except av.error.FFmpegError as e:
                log.info("Запись %s: ошибка потока (%s)", self.camera_name, type(e).__name__)
                return got
            if packet is None:
                return got
            if packet.dts is None and packet.size == 0:
                continue
            got = True
            self._got_signal()
            if self._mode == "camera":
                self._write_copy(packet, stream)
            elif not (self._copy_due(packet, time.monotonic()) and self._copy_again(packet, stream)):
                self._write_encoded(packet, next_slot)
            self._publish()
        return got

    def _write_copy(self, packet, stream) -> None:
        if self._writer is None:
            self._writer = CopyWriter(self.folder, self._stem, stream, split_bytes=self.split_bytes)
        try:
            self._writer.write_packet(packet)
            self._copy_bad = 0
        except OSError:
            raise
        except (av.error.FFmpegError, ValueError) as e:
            self._copy_bad += 1
            # разовый сбой отметок времени не повод терять полное качество на всю запись
            if self._writer.bytes_written > 0 and self._copy_bad < COPY_BAD_MAX:
                log.info("Запись %s: пакет пропущен (%s)", self.camera_name, type(e).__name__)
                return
            log.warning("Запись %s: поток нельзя скопировать (%s), перехожу на кодирование",
                        self.camera_name, type(e).__name__)
            self._abandon_copy()
            self._mode, self._wait_key = "light", True
            self._set(degraded=True)
            now = time.monotonic()
            quick = self._tried_at is not None and now - self._tried_at < COPY_STABLE_S
            self._retry_s = min(self._retry_s * 2, COPY_RETRY_MAX_S) if quick else COPY_RETRY_S
            self._retry_at = now + self._retry_s

    # --- возврат к копии: лёгкий файл — страховка на время сбоя, а не до конца записи ---

    def _copy_due(self, packet, now: float) -> bool:
        """Пора ли снова пробовать копию: срок вышел, и пакет — ключевой кадр (с другого файл не начать)."""
        return self._retry_at is not None and now >= self._retry_at and bool(packet.is_keyframe)

    def _copy_again(self, packet, stream) -> bool:
        """Начать с этого пакета файл-копию. Не вышло — лёгкий файл идёт дальше, будто попытки не было (False)."""
        light, self._writer = self._writer, None
        stamps = packet.pts, packet.dts
        self._mode, self._copy_bad, self._tried_at = "camera", 0, time.monotonic()
        try:
            self._write_copy(packet, stream)
        except BaseException:  # диск: запись сейчас остановится, но лёгкий файл должен быть закрыт и в списке
            full, self._writer = self._writer, light
            self._close_writer()
            self._writer = full
            raise
        if self._mode != "camera":  # _write_copy снова отступил и назначил следующую попытку
            self._writer, self._wait_key = light, False
            packet.pts, packet.dts = stamps  # писатель мог их сдвинуть, а пакет ещё пойдёт декодеру
            packet.stream = stream
            return False
        full, self._writer = self._writer, light
        self._close_writer()  # лёгкий файл закрыт и стоит в списке перед новой копией
        self._writer, self._retry_at = full, None
        self._set(degraded=False)
        log.info("Запись %s: поток снова копируется, полное качество", self.camera_name)
        return True

    def _abandon_copy(self) -> None:
        w, self._writer = self._writer, None
        try:
            w.close()
        except Exception:
            log.debug("Запись: копия закрыта с ошибкой", exc_info=True)
        for p in w.paths:  # пустой файл от неудачной копии оператору не нужен
            if p.exists() and p.stat().st_size < 4096:
                p.unlink()
        self._done_paths += tuple(p for p in w.paths if p.exists())
        self._done_bytes += w.bytes_written

    def _write_encoded(self, packet, next_slot) -> None:
        if self._wait_key:  # пока шла копия, декодер кадров не видел: с середины группы он выдал бы мусор
            if not packet.is_keyframe:
                return
            self._wait_key = False
        try:
            frames = packet.decode()
        except av.error.FFmpegError:
            return  # до первого ключевого кадра декодер не может начать
        for frame in frames:
            t = frame.time if frame.time is not None else time.monotonic()
            if next_slot[0] is not None and t + 1e-6 < next_slot[0]:
                continue
            step = 1 / LIGHT_FPS
            next_slot[0] = t + step if next_slot[0] is None or t - next_slot[0] > step else next_slot[0] + step
            if self._writer is None:
                self._writer = self._encoder(frame.width, frame.height)
            self._writer.write_frame(frame.to_ndarray(format="bgr24"), t)
