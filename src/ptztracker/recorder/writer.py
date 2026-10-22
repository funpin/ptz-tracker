"""BlackBox — запись последних минут в память и сохранение их в zip.

Вызывающие потоки (анализ, регулятор, планировщик, Qt) не ждут: кадр кладётся в очередь
ссылкой, а событие вызывающий поток сам превращает в строку JSON (`event_line`) и кладёт
в очередь готовой строкой. Фоновый поток кодирует кадры в JPEG и складывает всё
в кольцевые буферы; zip пишется ещё одним потоком.

Формат zip: meta.json, events.jsonl (строка — событие), frames.jsonl
(строка — кадр: t, seq, размер, имя файла) и frames/{seq:06d}.jpg.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
import zipfile
from concurrent.futures import Future
from pathlib import Path

import cv2

from ptztracker.recorder.events import event_line, redact_meta
from ptztracker.recorder.ring import RingBuffer
from ptztracker.video.frames import Frame

log = logging.getLogger(__name__)

FORMAT_VERSION = 1
# очередь полных (до сжатия) кадров: 50 — ~10 с при 5 кадрах/с; фоновый поток встал — новые кадры
# выбрасываются, а не копятся в памяти (полный кадр 1920×1080 — ~6 МБ). Очередь событий без предела.
FRAME_QUEUE_SIZE = 50
MAX_FRAME_SIZE = (640, 360)  # кадры больше этого уменьшаются (спека §2: JPEG 640×360)
CHUNK_S = 60.0  # постоянная запись на диск — кусками по минуте
LOG_EVERY_S = 10.0  # ошибки записи — в лог не чаще раза в 10 с
_PUMP_FRAMES = 10  # кадров за проход фонового потока: события не ждут кодирования всей очереди
_IDLE_S = 0.02

# (t, строка events.jsonl) и (t, (seq, ширина, высота, jpeg))
EventItem = tuple[float, str]
FrameItem = tuple[float, tuple[int, int, int, bytes]]


def _clock() -> dict:
    """Пара часов для привязки monotonic-меток записи к настенному времени."""
    return {"monotonic": time.monotonic(), "wallclock": time.time(), "local": time.strftime("%Y-%m-%d %H:%M:%S")}


def _safe_reason(reason: str) -> str:
    s = re.sub(r"[^\w]+", "-", reason).strip("-_")[:40]
    return s or "снимок"


def write_episode(path: Path, meta: dict, events: list[EventItem], frames: list[FrameItem]) -> Path:
    """Пишет эпизод в zip атомарно: сначала во временный файл, затем переименование."""
    tmp = path.with_name(path.name + ".part")
    used: set[str] = set()
    index = []
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=1))
        z.writestr("events.jsonl", "".join(line + "\n" for _, line in events))
        for t, (seq, w, h, jpeg) in frames:
            name = f"frames/{seq:06d}.jpg"
            n = 1
            while name in used:  # номера кадров сбрасываются при смене источника
                n += 1
                name = f"frames/{seq:06d}_{n}.jpg"
            used.add(name)
            z.writestr(name, jpeg, compress_type=zipfile.ZIP_STORED)
            index.append(json.dumps({"t": t, "seq": seq, "w": w, "h": h, "file": name}))
        z.writestr("frames.jsonl", "".join(line + "\n" for line in index))
    os.replace(tmp, path)
    return path


class BlackBox:
    """Чёрный ящик: последние `buffer_s` секунд кадров (`frame_fps` к/с, JPEG) и событий."""

    active = True

    def __init__(
        self,
        out_dir: Path,
        meta: dict,
        buffer_s: float = 300,
        frame_fps: float = 5,
        jpeg_quality: int = 70,
        to_disk: bool = False,
        *,
        start_thread: bool = True,  # False — без фонового потока (для тестов; кадры обрабатывает flush())
    ):
        self.out_dir = Path(out_dir)
        self.meta = redact_meta(dict(meta))
        self.clock = _clock()
        self.jpeg_quality = int(jpeg_quality)
        self.to_disk = to_disk
        self.dropped_frames = 0
        self._min_dt = 0.9 / frame_fps  # допуск на дрожание моментов прихода кадров
        self._last_frame_t: float | None = None
        self._frames_q: queue.Queue[Frame] = queue.Queue(maxsize=FRAME_QUEUE_SIZE)
        self._events_q: queue.SimpleQueue[EventItem] = queue.SimpleQueue()
        self._frames = RingBuffer(buffer_s)
        self._events = RingBuffer(buffer_s)
        self._lock = threading.Lock()  # кольцевые буферы и имена файлов
        self._pump_lock = threading.Lock()  # один обработчик очередей за раз — порядок сохраняется
        self._reserved: set[Path] = set()
        self._last_log = float("-inf")
        self._closed = False
        self._stop = threading.Event()
        # постоянная запись на диск
        self._session_dir: Path | None = None
        self._chunk_origin: float | None = None
        self._chunk_idx = 0
        self._chunk_no = 0
        self._chunk_events: list[EventItem] = []
        self._chunk_frames: list[FrameItem] = []
        self._thread: threading.Thread | None = None
        if start_thread:
            self._thread = threading.Thread(target=self._run, name="blackbox", daemon=True)
            self._thread.start()

    # --- вызовы из рабочих потоков: не блокируют и не бросают ---

    def frame(self, fr: Frame) -> None:
        try:
            if self._closed:
                return
            t = fr.t_capture
            if self._last_frame_t is not None and t - self._last_frame_t < self._min_dt:
                return
            try:
                self._frames_q.put_nowait(fr)  # ссылка: кадры неизменяемы
            except queue.Full:
                self.dropped_frames += 1
                return
            self._last_frame_t = t
        except Exception as e:
            self._warn("кадр не записан", e)

    def event(self, kind: str, **data) -> None:
        try:
            if self._closed:
                return
            t = data.pop("t", None)
            t = time.monotonic() if t is None else float(t)
            try:
                line = event_line(t, kind, data)
            except Exception as e:
                line = event_line(t, "note", {"error": f"событие {kind} не записано: {e}"})
            self._events_q.put_nowait((t, line))
        except Exception as e:
            self._warn("событие не записано", e)

    def save_snapshot(self, reason: str) -> Future[Path]:
        """Сохраняет буфер в zip в фоне; возвращается сразу."""
        fut: Future[Path] = Future()
        try:
            with self._lock:
                events = self._events.items()
                frames = self._frames.items()
                path = self._reserve_name(reason)
            meta = {**self.meta, "format": FORMAT_VERSION, "reason": reason, "clock": self.clock, "saved": _clock()}

            def write():
                try:
                    self.out_dir.mkdir(parents=True, exist_ok=True)
                    fut.set_result(write_episode(path, meta, events, frames))
                except Exception as e:
                    log.exception("Не удалось сохранить запись %s", path)
                    fut.set_exception(e)
                finally:
                    with self._lock:
                        self._reserved.discard(path)

            threading.Thread(target=write, name="blackbox-save", daemon=True).start()
        except Exception as e:
            self._warn("снимок не сохранён", e)
            if not fut.done():
                fut.set_exception(e)
        return fut

    def recent_events(self, since: float, limit: int = 500) -> list[dict]:
        """События новее `since` из буфера (без кадров) — для API агента."""
        with self._lock:
            lines = self._events.recent(since, limit)
        out = []
        for _, line in lines:  # строки уже без паролей (event_line)
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def close(self) -> None:
        """Останавливает фоновый поток; остаток постоянной записи дописывается на диск."""
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                log.warning("Поток записи не остановился за 5 с")
                return
        self._finish()

    # --- для тестов и отладки ---

    def flush(self) -> None:
        """Обрабатывает всё, что сейчас в очередях (кодирует кадры в вызывающем потоке)."""
        while self._pump(None):
            pass

    def queued_frames(self) -> int:
        return self._frames_q.qsize()

    def buffered_frames(self) -> int:
        with self._lock:
            return len(self._frames)

    # --- фоновый поток ---

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if not self._pump(_PUMP_FRAMES):
                    self._stop.wait(_IDLE_S)
            except Exception as e:
                self._warn("ошибка потока записи", e)
                self._stop.wait(_IDLE_S)

    def _finish(self) -> None:
        try:
            self.flush()
            if self.to_disk:
                self._write_chunk()
        except Exception as e:
            self._warn("не удалось дописать запись", e)

    def _pump(self, max_frames: int | None) -> bool:
        with self._pump_lock:
            events: list[EventItem] = []
            while True:
                try:
                    events.append(self._events_q.get_nowait())
                except queue.Empty:
                    break
            if events:  # сначала события: ошибка с кадром не должна их потерять
                with self._lock:
                    for t, line in events:
                        self._events.add(t, line)
                if self.to_disk:
                    for e in events:
                        self._next_chunk_if_due(e[0])
                        self._chunk_events.append(e)
            n = 0
            while max_frames is None or n < max_frames:
                try:
                    fr = self._frames_q.get_nowait()
                except queue.Empty:
                    break
                n += 1
                item = self._encode(fr)
                if item is None:
                    continue
                with self._lock:
                    self._frames.add(item[0], item[1])
                if self.to_disk:
                    self._next_chunk_if_due(item[0])
                    self._chunk_frames.append(item)
            return bool(events) or n > 0

    def _encode(self, fr: Frame) -> FrameItem | None:
        """Кадр → JPEG не больше MAX_FRAME_SIZE (с сохранением пропорций). None — кадр пропущен."""
        try:
            img = fr.image
            h, w = img.shape[:2]
            scale = min(MAX_FRAME_SIZE[0] / w, MAX_FRAME_SIZE[1] / h)
            if scale < 1.0:
                w, h = max(1, round(w * scale)), max(1, round(h * scale))
                img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
            if not ok:
                raise RuntimeError("cv2.imencode вернул ошибку")
            return fr.t_capture, (int(fr.seq), int(w), int(h), buf.tobytes())
        except Exception as e:
            self._warn(f"кадр seq={getattr(fr, 'seq', '?')} не закодирован", e)
            return None

    # --- постоянная запись на диск ---

    def _next_chunk_if_due(self, t: float) -> None:
        if self._chunk_origin is None:
            self._chunk_origin = t
        idx = int((t - self._chunk_origin) // CHUNK_S)
        if idx > self._chunk_idx:  # опоздавшие записи уходят в текущий кусок
            self._write_chunk()
            self._chunk_idx = idx

    def _write_chunk(self) -> None:
        events, frames = self._chunk_events, self._chunk_frames
        self._chunk_events, self._chunk_frames = [], []
        if not events and not frames:
            return
        try:
            if self._session_dir is None:
                self._session_dir = self.out_dir / f"ПТЗ_сеанс_{time.strftime('%Y-%m-%d_%H-%M-%S')}"
                self._session_dir.mkdir(parents=True, exist_ok=True)
            self._chunk_no += 1
            meta = {**self.meta, "format": FORMAT_VERSION, "chunk": self._chunk_no, "clock": self.clock}
            write_episode(self._session_dir / f"часть_{self._chunk_no:04d}.zip", meta, events, frames)
        except Exception as e:
            self._warn("кусок записи не сохранён", e)

    # --- служебное ---

    def _reserve_name(self, reason: str) -> Path:
        base = f"ПТЗ_{time.strftime('%Y-%m-%d_%H-%M-%S')}_{_safe_reason(reason)}"
        path = self.out_dir / f"{base}.zip"
        n = 1
        while path in self._reserved or path.exists():
            n += 1
            path = self.out_dir / f"{base}_{n}.zip"
        self._reserved.add(path)
        return path

    def _warn(self, what: str, e: Exception) -> None:
        now = time.monotonic()
        if now - self._last_log >= LOG_EVERY_S:
            self._last_log = now
            log.warning("Запись: %s: %s", what, e)
