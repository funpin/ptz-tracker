"""Файлы записи: MP4 фрагментами читается и после сбоя; на FAT32 файл делится на части по ключевым кадрам."""

from __future__ import annotations

import re
from datetime import datetime
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

MOVFLAGS = "frag_keyframe+empty_moov+default_base_moof"
FAT32_SPLIT_BYTES = 3_900_000_000  # FAT32 не хранит файлы больше 4 ГиБ
CLOCK_JUMP_S = 1.0  # отметки времени ушли вперёд больше чем на столько без обрыва связи — прыжок часов потока
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MS = Fraction(1, 1000)


def file_stem(camera_name: str, when: datetime) -> str:
    name = _BAD_CHARS.sub("_", camera_name).strip(" .")
    if not name.strip("_ "):
        name = "Камера"
    return f"{name}_{when:%Y-%m-%d_%H-%M}"


def next_path(folder: Path, stem: str) -> Path:
    """Первое свободное имя; файл создаётся сразу, чтобы две камеры с одним именем не взяли один и тот же."""
    path = folder / f"{stem}.mp4"
    n = 2
    while True:
        try:
            path.open("xb").close()
            return path
        except FileExistsError:
            path = folder / f"{stem}_{n:02d}.mp4"
            n += 1


class _Segments:
    """Общее у обоих писателей: файл открывается на первых данных, части, счёт байт."""

    def __init__(self, folder: Path, stem: str, split_bytes: int | None):
        self.folder, self.stem, self.split_bytes = Path(folder), stem, split_bytes
        self.paths: list[Path] = []
        self.bytes_written = 0
        self._segment_bytes = 0
        self._out: av.container.OutputContainer | None = None

    def _open_file(self) -> av.container.OutputContainer:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = next_path(self.folder, self.stem)
        try:
            out = av.open(str(path), "w", format="mp4", options={"movflags": MOVFLAGS})
        except Exception:
            path.unlink(missing_ok=True)
            raise
        self.paths.append(path)
        self._segment_bytes = 0
        return out

    def _count(self, size: int) -> None:
        self.bytes_written += size
        self._segment_bytes += size

    def _segment_full(self) -> bool:
        return self.split_bytes is not None and self._segment_bytes >= self.split_bytes


class CopyWriter(_Segments):
    """Пакеты H.264 камеры как есть, без пережатия."""

    def __init__(self, folder: Path, stem: str, template, split_bytes: int | None = None):
        super().__init__(folder, stem, split_bytes)
        self._template = template
        self._stream = None
        self._zero = 0  # dts первого пакета части: каждая часть начинается с нуля
        self._last_dts: int | None = None  # последний записанный dts части
        self._step: int | None = None  # обычный шаг между пакетами (самый короткий из встреченных)

    def _start_segment(self, packet: av.Packet) -> None:
        self._out = self._open_file()
        self._stream = self._out.add_stream_from_template(self._template)
        self._zero = packet.dts
        self._last_dts = None

    def write_packet(self, packet: av.Packet) -> None:
        if packet.dts is None:
            return
        if self._out is None:
            self._start_segment(packet)
        elif packet.is_keyframe and self._segment_full():
            self._out.close()
            self._start_segment(packet)
        size = packet.size
        dts = packet.dts - self._zero
        if self._last_dts is not None:
            step = dts - self._last_dts
            # Часы потока прыгнули: камера сверяет их по RTCP, и отметки отступают назад — на кадр-другой раз в
            # 30–35 с, а бывает на секунды и насовсем (AW-HE40, клуб, 2026-11-10). MP4 пакет «из прошлого» не
            # принимает, а без него картинка рассыпается до следующего ключевого кадра. Поэтому пакет не
            # выбрасываем: он встаёт через обычный шаг после предыдущего, и дальше время идёт от него. Прыжок
            # вперёд дольше CLOCK_JUMP_S — те же часы, а не пауза в съёмке: в файле дыры не будет.
            jump = CLOCK_JUMP_S / packet.time_base if packet.time_base else None
            if step <= 0 or (jump is not None and step > jump):
                usual = self._step or packet.duration or 1
                self._zero += step - usual
                dts = self._last_dts + usual
            else:
                self._step = step if self._step is None else min(self._step, step)
        if packet.pts is not None:
            packet.pts = max(packet.pts - self._zero, dts)
        packet.dts = self._last_dts = dts
        packet.stream = self._stream
        self._out.mux(packet)
        self._count(size)

    def close(self) -> None:
        if self._out is not None:
            self._out.close()
            self._out = None


class EncodeWriter(_Segments):
    """Кодирование кадров в H.264: «Лёгкий файл» и симулятор."""

    def __init__(self, folder: Path, stem: str, width: int, height: int, fps: int = 25,
                 bit_rate: int | None = None, split_bytes: int | None = None, max_rate: int | None = None):
        super().__init__(folder, stem, split_bytes)
        self.width, self.height, self.fps, self.bit_rate = width, height, fps, bit_rate
        self.max_rate = max_rate
        self._stream = None
        self._t0: float | None = None
        self._last_pts = -1

    def _start_segment(self) -> None:
        self._out = self._open_file()
        s = self._out.add_stream("libx264", rate=self.fps)
        s.width, s.height, s.pix_fmt = self.width, self.height, "yuv420p"
        s.time_base = s.codec_context.time_base = _MS  # иначе кадры чаще 1/fps получают один и тот же dts
        s.options = {"preset": "veryfast", "g": str(self.fps), "bf": "0"}
        if self.max_rate:  # средний битрейт — bit_rate; на движении до max_rate, буфер на полсекунды
            s.options |= {"maxrate": str(self.max_rate), "bufsize": str(self.max_rate // 2)}
        if self.bit_rate:
            s.bit_rate = self.bit_rate
        self._stream = s

    def _mux(self, packets) -> None:
        for p in packets:
            size = p.size
            self._out.mux(p)
            self._count(size)

    def _finish_segment(self) -> None:
        self._mux(self._stream.encode())
        self._out.close()
        self._out = None

    def write_frame(self, image: np.ndarray, t_s: float) -> None:
        if self._out is None:
            self._start_segment()
            self._t0 = t_s
        elif self._segment_full():
            self._finish_segment()  # кодировщик сбрасывается, новая часть начнётся с ключевого кадра
            self._start_segment()
            self._t0, self._last_pts = t_s, -1
        frame = av.VideoFrame.from_ndarray(image, format="bgr24")
        pts = max(self._last_pts + 1, round((t_s - self._t0) * 1000))
        frame.pts, frame.time_base, self._last_pts = pts, _MS, pts
        self._mux(self._stream.encode(frame))

    def close(self) -> None:
        if self._out is not None:
            self._finish_segment()
