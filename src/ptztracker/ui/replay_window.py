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
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ptztracker.recorder.reader import Episode, _num, read_frame
from ptztracker.recorder.replay import seen_in_frame
from ptztracker.ui.preview import PreviewWidget
from ptztracker.ui.theme import FS_BUTTON, FS_CAPTION, R_PANEL, SP, C, qcolor
from ptztracker.ui.widgets import BrandMark, SectionHeader
from ptztracker.video.frames import Frame, SourceStats
from ptztracker.vision.analyzer import AnalysisResult
from ptztracker.vision.tracker import Track

SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0)
TICK_MS = 30
CONTROL_NEAR_S = 0.5  # такт регулятора дальше этого от кадра — слежения в этот момент не было
ANALYSIS_STALE_S = 0.5  # анализ старше этого — анализа в этот момент не было, рамки не показываем
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


def _track(d: dict, t_capture: float) -> Track:
    last_seen = float(d["last_seen"])
    if seen_in_frame(last_seen, t_capture):
        last_seen = t_capture  # превью сравнивает точно: виден в кадре — рисуется скелет и курсор
    kp = np.array(d.get("keypoints") or np.zeros((17, 3)), np.float32).reshape(-1, 3)
    return Track(
        id=int(d["id"]), box=tuple(float(v) for v in d["box"][:4]), score=float(d.get("score", 0.0)),
        keypoints=kp, last_seen=last_seen, hits=int(d.get("hits", 1)),
        misses=int(d.get("misses", 0)), velocity=tuple(float(v) for v in d.get("velocity") or (0.0, 0.0)),
        created=float(d.get("created", 0.0)),
    )


def _result(i: int, e: dict) -> AnalysisResult | None:
    """Записанное событие analysis → AnalysisResult (как отдал бы Analyzer). None — событие повреждено."""
    try:
        tc = float(e["t_capture"])
        tracks = []
        for d in e.get("tracks") or []:
            try:
                tracks.append(_track(d, tc))
            except (KeyError, TypeError, ValueError, IndexError, OverflowError):
                continue
        fs = e["frame_size"]
        return AnalysisResult(
            seq=i, t_capture=tc, frame_size=(int(fs[0]), int(fs[1])), tracks=tuple(tracks),
            target_id=e.get("target_id"), target_state=e.get("target_state") or "none",
            infer_ms=float(e.get("infer_ms") or 0.0), fps=0.0, detector="запись", device="—",
        )
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
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
        """Последний анализ кадра, снятого не позже t (у кадра записи он с тем же t_capture).

        Если он старше ANALYSIS_STALE_S, анализ тогда не шёл — None, чтобы рамки не «замерзали».
        """
        i = bisect.bisect_right(self._t, t + 1e-6) - 1
        fresh = i >= 0 and t - self._t[i] <= ANALYSIS_STALE_S
        self._latest = _result(i, self._events[i]) if fresh else None

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
        return "нет"
    return f"№{r.target_id} · {TARGET_STATES.get(r.target_state, r.target_state)}"


def _codes(e: dict | None) -> tuple[str, str, str]:
    try:
        p, t, z = (int(c) for c in e["codes"][:3])
        return str(p), str(t), str(z)
    except (KeyError, TypeError, ValueError):
        return "—", "—", "—"


def state_text(e: dict | None) -> str:
    """Что делал регулятор — словом; тактов нет — слежение было выключено."""
    if e is None:
        return "выключен"
    out = e.get("out")
    state = out.get("state") if isinstance(out, dict) else None
    return STATE_NAMES.get(state, state or "—")


def fmt_clock(s: float) -> str:
    """75,25 → «1:15,2» — минуты и секунды с десятыми."""
    s = max(0.0, s)
    m, sec = divmod(int(s * 10) / 10, 60)
    return f"{int(m)}:{sec:04.1f}".replace(".", ",")


def _duration_text(s: float) -> str:
    return f"{int(s // 60)} мин {int(s % 60)} с" if s >= 60 else f"{round(s)} с"


def header_meta(ep: Episode) -> str:
    """Чья запись: камера, когда, сколько длится, версия программы."""
    meta = ep.meta if isinstance(ep.meta, dict) else {}
    parts = []
    cam = meta.get("camera")
    if isinstance(cam, dict) and cam.get("name"):
        parts.append(str(cam["name"]))
    local = (meta.get("saved") or meta.get("clock") or {}).get("local") if isinstance(
        meta.get("saved") or meta.get("clock"), dict) else None
    if isinstance(local, str) and len(local) >= 16:  # «2026-10-29 21:14:05» → «29.10.2026 21:14»
        parts.append(f"{local[8:10]}.{local[5:7]}.{local[:4]} {local[11:16]}")
    parts.append(_duration_text(ep.duration))
    if meta.get("version"):
        parts.append(f"версия {meta['version']}")
    return " · ".join(parts)


TIMELINE_COLORS = {"on": C.ON, "lost": C.LOST, "idle": C.GREY, "off": C.RAISED_HI}
TIMELINE_LEGEND = (("on", "ведёт"), ("lost", "ищет цель"), ("idle", "стоит / держит"), ("off", "слежение выключено"))


def timeline_segments(ep: Episode) -> list[tuple[float, float, str]]:
    """Дорожка всей записи по кадрам: ведёт / ищет цель / стоит / слежение выключено — отрезками."""
    times = [float(f["t"]) for f in ep.frame_index]
    if not times:
        return []
    analysis, controls = RecordedAnalysis(ep), ControlTimeline(ep)
    kinds = []
    for t in times:
        e = controls.near(t)
        analysis.show(t)
        r = analysis.latest()
        if e is None:
            kinds.append("off")
        elif r is not None and r.target_state == "lost":
            kinds.append("lost")
        elif state_text(e) in ("стоит", "держит"):
            kinds.append("idle")
        else:
            kinds.append("on")
    segs: list[tuple[float, float, str]] = []
    for i, k in enumerate(kinds):
        t1 = times[i + 1] if i + 1 < len(times) else times[i]
        if segs and segs[-1][2] == k:
            segs[-1] = (segs[-1][0], t1, k)
        else:
            segs.append((times[i], t1, k))
    return segs


class TimelineStrip(QWidget):
    """Дорожка над ползунком: цветом — что делало слежение; белая черта — где сейчас; клик — перемотка."""

    seek = Signal(float)

    def __init__(self, segs: list[tuple[float, float, str]], t0: float, t1: float, parent=None):
        super().__init__(parent)
        self._segs, self._t0, self._t1 = segs, t0, max(t1, t0 + 1e-6)
        self._now = t0
        self.setFixedHeight(14)
        self.setCursor(Qt.PointingHandCursor)

    def set_now(self, t: float) -> None:
        self._now = t
        self.update()

    def _x(self, t: float) -> float:
        return (t - self._t0) / (self._t1 - self._t0) * self.width()

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton and self.width() > 0:
            self.seek.emit(self._t0 + e.position().x() / self.width() * (self._t1 - self._t0))

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = self.height()
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 2, self.width(), h - 4), 3, 3)
        p.setClipPath(clip)
        p.fillRect(QRectF(0, 2, self.width(), h - 4), qcolor(C.RAISED))
        for a, b, k in self._segs:
            p.fillRect(QRectF(self._x(a), 2, max(1.0, self._x(b) - self._x(a)), h - 4), qcolor(TIMELINE_COLORS[k]))
        p.setClipping(False)
        x = self._x(self._now)
        p.fillRect(QRectF(x - 1, 0, 2, h), qcolor(C.TEXT))
        p.end()


class ReplayWindow(QWidget):
    """Просмотр записи (макет docs/design/mockups/journal-replay.html, раздел 2): сверху — чья запись,
    справа — «В этот момент», над ползунком — дорожка всей записи, внизу — ▶, время и скорость кнопками."""

    MOMENT_ROWS = (("target", "Цель"), ("state", "Регулятор"), ("pan", "Поворот, код"), ("tilt", "Наклон, код"),
                   ("zoom", "Зум, код"))

    def __init__(self, episode: Episode, parent=None, on_escape: Callable[[], None] | None = None,
                 on_open_other: Callable[[], None] | None = None):
        """on_escape — аварийный стоп камеры главного окна (Esc и здесь означает «стоп»);
        on_open_other — «Открыть другую…» (выбор файла в главном окне)."""
        super().__init__(parent, Qt.Window)
        self.episode = episode
        self._on_escape = on_escape
        self.setWindowTitle(f"Запись — {episode.path.name}")
        self.resize(1100, 700)
        self.source = EpisodeFrames(episode)
        self.analysis = RecordedAnalysis(episode)
        self.controls = ControlTimeline(episode)
        self.index = -1
        self.playing = False
        self._play_t = 0.0  # момент записи, до которого дошло воспроизведение
        self._wall: float | None = None
        self._ticking = False  # ползунок двигает воспроизведение, а не оператор

        # --- шапка: чья запись ---
        self.title_label = QLabel(f"Запись — {episode.path.name}")
        self.title_label.setStyleSheet(f"font-size: {FS_BUTTON}px; font-weight: 700;")
        self.meta_label = QLabel(header_meta(episode))
        self.meta_label.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
        self.reveal_button = QPushButton("Показать файл")
        self.reveal_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(episode.path.parent))))
        self.other_button = QPushButton("Открыть другую…")
        self.other_button.setVisible(on_open_other is not None)
        if on_open_other is not None:
            self.other_button.clicked.connect(on_open_other)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(self.title_label)
        titles.addWidget(self.meta_label)
        head = QHBoxLayout()
        head.setSpacing(SP[2])
        head.addWidget(BrandMark(22))
        head.addLayout(titles, 1)
        head.addWidget(self.reveal_button)
        head.addWidget(self.other_button)

        # --- превью и «В этот момент» ---
        self.preview = PreviewWidget()
        self.preview.set_source(self.source)
        self.preview.set_analyzer(self.analysis)
        self.moment: dict[str, QLabel] = {}
        side = QFrame()
        side.setObjectName("moment")
        side.setFixedWidth(230)
        side.setStyleSheet(f"QFrame#moment {{ background: {C.SURFACE}; border: 1px solid {C.LINE};"
                           f" border-radius: {R_PANEL}px; }}")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(SP[2], SP[2], SP[2], SP[2])
        sl.setSpacing(SP[1])
        sl.addWidget(SectionHeader("В этот момент"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(SP[2])
        grid.setVerticalSpacing(SP[1])
        for r, (key, title) in enumerate(self.MOMENT_ROWS):
            t = QLabel(title)
            t.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px; background: transparent;")
            v = QLabel("—")
            v.setStyleSheet("font-weight: 600; background: transparent;")
            v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(t, r, 0)
            grid.addWidget(v, r, 1)
            self.moment[key] = v
        sl.addLayout(grid)
        sl.addStretch(1)
        mid = QHBoxLayout()
        mid.setSpacing(SP[2])
        mid.addWidget(self.preview, 1)
        mid.addWidget(side)

        # --- дорожка, ползунок, ▶, время, скорость ---
        times = self.source.times
        self.strip = TimelineStrip(timeline_segments(episode), times[0] if times else 0.0,
                                   times[-1] if times else 0.0)
        self.strip.seek.connect(self._seek_time)
        legend = QHBoxLayout()
        legend.setSpacing(SP[2])
        for k, text in TIMELINE_LEGEND:
            legend.addWidget(_legend_item(TIMELINE_COLORS[k], text))
        legend.addStretch(1)
        self.play_btn = QPushButton("▶")
        self.play_btn.setProperty("variant", "primary")
        self.play_btn.setFixedWidth(48)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, max(0, len(self.source) - 1))
        self.time_label = QLabel()
        self.time_label.setStyleSheet("font-weight: 600;")
        hint = QLabel("Пробел ▶/⏸ · ← → кадр")
        hint.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
        self.speed_combo = QComboBox()  # модель скорости; на экране — кнопки
        for sp in SPEEDS:
            self.speed_combo.addItem(f"×{sp:g}".replace(".", ","), sp)
        self.speed_combo.setVisible(False)
        self.speed_buttons: dict[float, QPushButton] = {}
        speeds = QHBoxLayout()
        speeds.setSpacing(0)
        group = QButtonGroup(self)
        for i, sp in enumerate(SPEEDS):
            b = QPushButton(self.speed_combo.itemText(i))
            b.setCheckable(True)
            b.setProperty("variant", "seg")
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, i=i: self.speed_combo.setCurrentIndex(i))
            group.addButton(b)
            speeds.addWidget(b)
            self.speed_buttons[sp] = b
        self.speed_combo.currentIndexChanged.connect(
            lambda i: self.speed_buttons[SPEEDS[i]].setChecked(True))
        self.speed_combo.setCurrentIndex(SPEEDS.index(1.0))
        self.speed_buttons[1.0].setChecked(True)
        self.status = QLabel()  # «В записи нет кадров.» и прочие сбои
        self.status.setStyleSheet(f"color: {C.TEXT_DIM};")
        self.status.setVisible(False)

        bar = QHBoxLayout()
        bar.setSpacing(SP[2])
        bar.addWidget(self.play_btn)
        bar.addWidget(self.time_label)
        bar.addWidget(hint)
        bar.addStretch(1)
        bar.addLayout(speeds)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP[3], SP[2], SP[3], SP[3])
        lay.setSpacing(SP[2])
        lay.addLayout(head)
        lay.addLayout(mid, 1)
        lay.addWidget(self.strip)
        lay.addWidget(self.slider)
        lay.addLayout(legend)
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
            self.status.setVisible(True)

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

    def _seek_time(self, t: float) -> None:
        times = self.source.times
        if times:
            self.slider.setValue(max(0, min(len(times) - 1, bisect.bisect_right(times, t) - 1)))

    # --- показ кадра ---

    def _show(self, i: int) -> None:
        self.index = i
        t = self.source.times[i]
        self.source.show(i)
        self.analysis.show(t)
        t0 = self.source.times[0]
        self.time_label.setText(f"{fmt_clock(t - t0)} / {fmt_clock(self.source.times[-1] - t0)}")
        self.strip.set_now(t)
        e = self.controls.near(t)
        pan, tilt, zoom = _codes(e)
        for key, text in (("target", target_text(self.analysis.latest())), ("state", state_text(e)),
                          ("pan", pan), ("tilt", tilt), ("zoom", zoom)):
            self.moment[key].setText(text)

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key_Escape:
            # оператор жмёт Esc в эфире — значит «стоп камере», где бы ни был фокус
            if self._on_escape is not None:
                self._on_escape()
            self.close()
            return
        if e.key() == Qt.Key_Space:
            self.toggle_play()
            return
        if e.key() in (Qt.Key_Left, Qt.Key_Right) and len(self.source):
            self.slider.setValue(self.index + (1 if e.key() == Qt.Key_Right else -1))
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):  # noqa: N802
        self._set_playing(False)
        self.source.stop()
        super().closeEvent(e)


def _legend_item(color: str, text: str) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(5)
    sw = QLabel()
    sw.setFixedSize(10, 10)
    sw.setStyleSheet(f"background: {color}; border-radius: 3px;")
    lab = QLabel(text)
    lab.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
    h.addWidget(sw)
    h.addWidget(lab)
    return w
