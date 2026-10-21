"""Окно просмотра записи (чёрного ящика): кадры с рамками записанного анализа, ползунок, воспроизведение.

Превью — тот же PreviewWidget, что в главном окне: ему подставляются источник кадров из zip и
«анализатор», который отдаёт записанный результат анализа для текущего кадра. Только просмотр:
камера и трекер не участвуют.
"""

from __future__ import annotations

import bisect
import time
import zipfile
from collections import OrderedDict

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from ptztracker.recorder.reader import Episode, _num, read_frame
from ptztracker.ui.preview import PreviewWidget
from ptztracker.video.frames import Frame, SourceStats
from ptztracker.vision.analyzer import AnalysisResult
from ptztracker.vision.tracker import Track

SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0)
TICK_MS = 30
CONTROL_NEAR_S = 0.5  # такт регулятора дальше этого от кадра — слежения в этот момент не было
TARGET_STATES = {"tracking": "веду", "lost": "потеряна"}
STATE_NAMES = {"idle": "стоит", "holding": "держит", "moving": "ведёт", "diverged": "раскачка"}


class EpisodeFrames:
    """Источник кадров (протокол FrameSource) из записи: отдаёт выбранный кадр.

    Декодируется только текущий кадр, несколько последних хранятся. Номер кадра (`seq`) — его
    позиция в записи: номера камеры сбрасываются при смене источника, а превью обновляется по смене seq.
    """

    CACHE_SIZE = 8

    def __init__(self, ep: Episode):
        self._index = ep.frame_index
        self._zip = zipfile.ZipFile(ep.path)
        self._cache: OrderedDict[int, np.ndarray | None] = OrderedDict()
        self._current: Frame | None = None
        self.times = [float(f["t"]) for f in self._index]

    def __len__(self) -> int:
        return len(self._index)

    def show(self, i: int) -> None:
        img = self._image(i)
        self._current = None if img is None else Frame(image=img, t_capture=self.times[i], seq=i)

    def _image(self, i: int) -> np.ndarray | None:
        if i in self._cache:
            self._cache.move_to_end(i)
            return self._cache[i]
        img = read_frame(self._zip, self._index[i])
        self._cache[i] = img
        while len(self._cache) > self.CACHE_SIZE:
            self._cache.popitem(last=False)
        return img

    # --- FrameSource ---

    def start(self) -> None:
        pass

    def stop(self, timeout: float = 2.0) -> None:
        self._zip.close()

    def latest(self) -> Frame | None:
        return self._current

    def stats(self) -> SourceStats:
        fr = self._current
        h, w = fr.image.shape[:2] if fr is not None else (0, 0)
        return SourceStats(True, 0.0, w, h, None if fr is not None else "кадр записи не читается", "запись")


def _track(d: dict) -> Track:
    kp = np.array(d.get("keypoints") or np.zeros((17, 3)), np.float32).reshape(-1, 3)
    return Track(
        id=int(d["id"]), box=tuple(float(v) for v in d["box"][:4]), score=float(d.get("score", 0.0)),
        keypoints=kp, last_seen=float(d["last_seen"]), hits=int(d.get("hits", 1)),
        misses=int(d.get("misses", 0)), velocity=tuple(float(v) for v in d.get("velocity") or (0.0, 0.0)),
        created=float(d.get("created", 0.0)),
    )


def _result(i: int, e: dict) -> AnalysisResult | None:
    """Записанное событие analysis → AnalysisResult (как отдал бы Analyzer). None — событие повреждено."""
    try:
        tracks = []
        for d in e.get("tracks") or []:
            try:
                tracks.append(_track(d))
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        fs = e["frame_size"]
        return AnalysisResult(
            seq=i, t_capture=float(e["t_capture"]), frame_size=(int(fs[0]), int(fs[1])), tracks=tuple(tracks),
            target_id=e.get("target_id"), target_state=e.get("target_state") or "none",
            infer_ms=float(e.get("infer_ms") or 0.0), fps=0.0, detector="запись", device="—",
        )
    except (KeyError, TypeError, ValueError, IndexError):
        return None


class RecordedAnalysis:
    """«Анализатор» для PreviewWidget: записанный результат анализа на момент показанного кадра."""

    def __init__(self, ep: Episode):
        items = []
        for e in ep.events("analysis"):
            tc = _num(e.get("t_capture"))
            if tc is not None:
                items.append((tc, e))
        items.sort(key=lambda te: te[0])
        self._t = [tc for tc, _ in items]
        self._events = [e for _, e in items]
        self._latest: AnalysisResult | None = None

    def show(self, t: float) -> None:
        """Последний анализ кадра, снятого не позже t (у кадра записи он с тем же t_capture)."""
        i = bisect.bisect_right(self._t, t + 1e-6) - 1
        self._latest = _result(i, self._events[i]) if i >= 0 else None

    def latest(self) -> AnalysisResult | None:
        return self._latest

    def select_at(self, x: float, y: float) -> int | None:
        return None  # только просмотр


class ControlTimeline:
    """Такты регулятора (`control`) по времени: ближайший к моменту кадра."""

    def __init__(self, ep: Episode):
        items = []
        for e in ep.events("control"):
            t = _num(e.get("t"))
            if t is not None:
                items.append((t, e))
        items.sort(key=lambda te: te[0])
        self._t = [t for t, _ in items]
        self._events = [e for _, e in items]

    def near(self, t: float) -> dict | None:
        i = bisect.bisect_left(self._t, t)
        best = min((j for j in (i - 1, i) if 0 <= j < len(self._t)), key=lambda j: abs(self._t[j] - t), default=None)
        if best is None or abs(self._t[best] - t) > CONTROL_NEAR_S:
            return None
        return self._events[best]


def target_text(r: AnalysisResult | None) -> str:
    if r is None or r.target_id is None:
        return "цель: нет"
    return f"цель: №{r.target_id} ({TARGET_STATES.get(r.target_state, r.target_state)})"


def control_text(e: dict | None) -> str:
    if e is None:
        return "регулятор: тактов нет (слежение выключено)"
    try:
        p, t, z = (int(c) for c in e["codes"][:3])
        codes = f"{p}/{t}/{z}"
    except (KeyError, TypeError, ValueError):
        codes = "—"
    out = e.get("out")
    state = out.get("state") if isinstance(out, dict) else None
    name = STATE_NAMES.get(state, "")
    state_text = f"{state} ({name})" if name else (state or "—")
    return f"коды pan/tilt/zoom: {codes} · регулятор: {state_text}"


class ReplayWindow(QWidget):
    """Просмотр записи: превью с рамками, ползунок времени, ▶/⏸, скорость, коды и состояние регулятора."""

    def __init__(self, episode: Episode, parent=None):
        super().__init__(parent, Qt.Window)
        self.episode = episode
        self.setWindowTitle(f"Запись — {episode.path.name}")
        self.resize(1000, 680)
        self.source = EpisodeFrames(episode)
        self.analysis = RecordedAnalysis(episode)
        self.controls = ControlTimeline(episode)
        self.index = -1
        self.playing = False
        self._play_t = 0.0  # момент записи, до которого дошло воспроизведение
        self._wall: float | None = None
        self._ticking = False  # ползунок двигает воспроизведение, а не оператор

        self.preview = PreviewWidget()
        self.preview.set_source(self.source)
        self.preview.set_analyzer(self.analysis)
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(44)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, max(0, len(self.source) - 1))
        self.time_label = QLabel()
        self.speed_combo = QComboBox()
        for s in SPEEDS:
            self.speed_combo.addItem(f"×{s:g}".replace(".", ","), s)
        self.speed_combo.setCurrentIndex(SPEEDS.index(1.0))
        self.status = QLabel()
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)

        bar = QHBoxLayout()
        bar.addWidget(self.play_btn)
        bar.addWidget(self.slider, 1)
        bar.addWidget(self.time_label)
        bar.addWidget(self.speed_combo)
        lay = QVBoxLayout(self)
        lay.addWidget(self.preview, 1)
        lay.addLayout(bar)
        lay.addWidget(self.status)

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self.slider.valueChanged.connect(self._on_slider)
        self.play_btn.clicked.connect(self.toggle_play)
        if len(self.source):
            self._show(0)
        else:
            self.play_btn.setEnabled(False)
            self.status.setText("В записи нет кадров.")

    @property
    def speed(self) -> float:
        return float(self.speed_combo.currentData())

    # --- воспроизведение ---

    def toggle_play(self) -> None:
        if self.playing:
            self._set_playing(False)
            return
        if not len(self.source):
            return
        if self.index >= len(self.source) - 1:
            self.slider.setValue(0)  # ▶ в конце — с начала
        self._play_t = self.source.times[self.index]
        self._set_playing(True)

    def _set_playing(self, on: bool) -> None:
        self.playing = on
        self.play_btn.setText("⏸" if on else "▶")
        self._wall = time.monotonic() if on else None
        if on:
            self._timer.start()
        else:
            self._timer.stop()

    def _tick(self) -> None:
        now = time.monotonic()
        self._play_t += (now - self._wall) * self.speed
        self._wall = now
        times = self.source.times
        i = max(self.index, bisect.bisect_right(times, self._play_t) - 1)
        if i != self.index:
            self._ticking = True
            try:
                self.slider.setValue(i)  # → _on_slider → _show
            finally:
                self._ticking = False
        if self.index >= len(times) - 1:
            self._set_playing(False)

    def _on_slider(self, i: int) -> None:
        if i == self.index:
            return
        self._show(i)
        if not self._ticking:
            self._play_t = self.source.times[i]  # ползунок сдвинули руками — играть отсюда

    # --- показ кадра ---

    def _show(self, i: int) -> None:
        self.index = i
        t = self.source.times[i]
        self.source.show(i)
        self.analysis.show(t)
        t0 = self.source.times[0]
        self.time_label.setText(f"{t - t0:5.1f} / {self.source.times[-1] - t0:.1f} с")
        self.status.setText(f"{target_text(self.analysis.latest())} · {control_text(self.controls.near(t))}")

    def closeEvent(self, e):  # noqa: N802
        self._set_playing(False)
        self.source.stop()
        super().closeEvent(e)
