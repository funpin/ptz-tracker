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
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MS = Fraction(1, 1000)


def file_stem(camera_name: str, when: datetime) -> str:
    name = _BAD_CHARS.sub("_", camera_name).strip(" .")
    if not name.strip("_ "):
        name = "Камера"
    return f"{name}_{when:%Y-%m-%d_%H-%M}"


def next_path(folder: Path, stem: str) -> Path:
    path = folder / f"{stem}.mp4"
    n = 2
    while path.exists():
        path = folder / f"{stem}_{n:02d}.mp4"
        n += 1
    return path


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
        out = av.open(str(path), "w", format="mp4", options={"movflags": MOVFLAGS})
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

    def _start_segment(self, packet: av.Packet) -> None:
        self._out = self._open_file()
        self._stream = self._out.add_stream_from_template(self._template)
        self._zero = packet.dts

    def write_packet(self, packet: av.Packet) -> None:
        if packet.dts is None:
            return
        if self._out is None:
            self._start_segment(packet)
        elif packet.is_keyframe and self._segment_full():
            self._out.close()
            self._start_segment(packet)
        size = packet.size
        packet.dts -= self._zero
        if packet.pts is not None:
            packet.pts -= self._zero
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
                 bit_rate: int | None = None, split_bytes: int | None = None):
        super().__init__(folder, stem, split_bytes)
        self.width, self.height, self.fps, self.bit_rate = width, height, fps, bit_rate
        self._stream = None
        self._t0: float | None = None
        self._last_pts = -1

    def _start_segment(self) -> None:
        self._out = self._open_file()
        s = self._out.add_stream("libx264", rate=self.fps)
        s.width, s.height, s.pix_fmt = self.width, self.height, "yuv420p"
        s.time_base = s.codec_context.time_base = _MS  # иначе кадры чаще 1/fps получают один и тот же dts
        s.options = {"preset": "veryfast", "g": str(self.fps), "bf": "0"}
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
