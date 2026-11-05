from __future__ import annotations

import math
import threading
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ptztracker.camera import he42
from ptztracker.camera import protocol as P
from ptztracker.control.calibration import CalibrationCancelled
from ptztracker.ui.theme import C, qcolor

REFRESH_MS = 100  # график и цифры обновляются ~10 раз в секунду
GRAPH_WINDOW_S = 45.0  # график показывает последние секунды: весь мастер (3–4 минуты) не уместился бы читаемо
MAX_SAMPLES = 20000  # запас по памяти: мастер даёт ~20 опросов #APC в секунду, 4 минуты — меньше 5000
MIN_RANGE_DEG = 2.0

CANCELLING_TEXT = "Останавливаю камеру и возвращаю на место…"
CANCELLED_TEXT = "Калибровка прервана: камера возвращена на место, ничего не сохранено, прежние настройки целы."


def fmt(x: float, digits: int = 2) -> str:
    """Число по-русски: десятичная запятая."""
    return f"{x:.{digits}f}".replace(".", ",")


def cmd_label(cmd: str) -> str:
    """Короткая подпись команды для графика: «П70/50» (поворот/наклон), «З70» (зум), «стоп», «домой»."""
    if cmd == P.STOP_PT or cmd == P.STOP_ZOOM:
        return "стоп"
    if cmd.startswith("#PTS") and len(cmd) == 8:
        return f"П{int(cmd[4:6])}/{int(cmd[6:8])}"
    if cmd.startswith("#Z") and len(cmd) == 4:
        return f"З{int(cmd[2:4])}"
    if cmd.startswith("#APC") or cmd.startswith("#AXZ"):
        return "домой"
    return cmd.lstrip("#")


class LiveData:
    """Замеры мастера для окна. Пишет рабочий поток мастера (on_sample), читает Qt-поток по таймеру:
    замок держится только на добавление и копирование, так что Qt не ждёт мастера."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.apc: list[tuple[float, float, float, float | None]] = []  # t, pan, tilt (ед. #APC), зум #GZ
        self.cmds: list[tuple[float, str]] = []
        self.step: dict | None = None
        self.results: dict = {}
        self.pan: dict[int, list] = {}
        self.version = 0  # растёт при смене цифр (не точек графика)

    def on_sample(self, kind: str, data: dict) -> None:
        with self._lock:
            if kind == "apc":
                self.apc.append((data["t"], data["pan"], data["tilt"], data.get("zoom")))
                if len(self.apc) > MAX_SAMPLES:
                    del self.apc[: MAX_SAMPLES // 4]
            elif kind == "cmd":
                self.cmds.append((data["t"], data["cmd"]))
                if len(self.cmds) > MAX_SAMPLES:
                    del self.cmds[: MAX_SAMPLES // 4]
            elif kind == "step":
                self.step = dict(data)
                self.version += 1
            elif kind == "result":
                if data.get("name") == "pan":
                    self.pan[data["zoom"]] = data["codes"]
                else:
                    self.results[data["name"]] = dict(data)
                self.version += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "apc": list(self.apc), "cmds": list(self.cmds), "step": self.step,
                "results": dict(self.results), "pan": dict(self.pan), "version": self.version,
            }


def step_text(step: dict | None) -> str:
    if not step:
        return ""
    return f"Шаг {step['index'] + 1} из {step['total']}: {step['title']}"


def numbers_text(snap: dict) -> str:
    """Что уже измерено: мёртвое время, разгон, задержка видео, скорости поворота (измерено / по модели),
    наклон, зум. Строки появляются по мере готовности."""
    res, lines = snap["results"], []
    if "dead_time" in res:
        lines.append(f"Мёртвое время: {fmt(res['dead_time']['value'])} с")
    if "tau" in res:
        lines.append(f"Разгон τ: {fmt(res['tau']['value'])} с")
    if "video_latency" in res:
        r = res["video_latency"]
        stream = f" ({r['stream']})" if r.get("stream") else ""
        lines.append(f"Задержка видео: {fmt(r['value'])} с{stream}")
    if snap["pan"]:
        lines.append("")
        lines.append("Поворот, °/с: измерено / по модели")
        for zoom in sorted(snap["pan"]):
            lines.append(f"Зум {zoom:03X}")
            for c, measured, model in snap["pan"][zoom]:
                lines.append(f"  код {c:>2d}: {fmt(measured, 1):>6} / {fmt(model, 1):>6}")
    if "tilt" in res and res["tilt"]["codes"]:
        lines.append("")
        lines.append("Наклон, °/с (доля от поворота):")
        for c, deg, ratio in res["tilt"]["codes"]:
            lines.append(f"  код {c}: {fmt(deg, 1)} (×{fmt(ratio)})")
    if "zoom" in res and res["zoom"]["points"]:
        lines.append("")
        lines.append("Скорость зума (во сколько раз в секунду):")
        for c, rate in res["zoom"]["points"]:
            lines.append(f"  код {c}: ×{fmt(math.exp(rate))}")
    return "\n".join(lines)


class CalibrationGraph(QWidget):
    """Живой график: поворот и наклон (° от исходной точки), под ним зум; вертикальные метки — отправленные
    команды с кодом. Рисует по снимку LiveData, который подставляет таймер окна."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 260)
        self._apc: list = []
        self._cmds: list = []

    def set_data(self, apc: list, cmds: list) -> None:
        self._apc, self._cmds = apc, cmds
        self.update()

    def sample_count(self) -> int:
        return len(self._apc)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), qcolor(C.SURFACE))
        left, right, top, bottom, gap = 46, 10, 18, 8, 10
        zoom_h = max(40, int((h - top - bottom) * 0.22))
        plot = QRectF(left, top, w - left - right, h - top - bottom - zoom_h - gap)
        zplot = QRectF(left, plot.bottom() + gap, plot.width(), zoom_h)
        small = QFont(self.font())
        small.setPixelSize(11)
        p.setFont(small)
        for r in (plot, zplot):
            p.setPen(QPen(qcolor(C.LINE), 1))
            p.drawRect(r)
        apc, cmds = self._apc, self._cmds
        if len(apc) < 2:
            p.setPen(qcolor(C.TEXT_FAINT))
            p.drawText(plot, Qt.AlignCenter, "Замеры появятся, когда мастер начнёт")
            return
        t_end = apc[-1][0]
        t_start = max(apc[0][0], t_end - GRAPH_WINDOW_S)
        span = max(t_end - t_start, 1.0)
        t_end = t_start + span  # ось не короче секунды
        start_pan, start_tilt = apc[0][1], apc[0][2]
        k = he42.APC_DEG_PER_UNIT
        pts = [(t, (pn - start_pan) * k, (tl - start_tilt) * k, z) for t, pn, tl, z in apc if t >= t_start]
        lim = max(MIN_RANGE_DEG, max(max(abs(a), abs(b)) for _, a, b, _ in pts) * 1.1)

        def x_of(t: float) -> float:
            return plot.left() + (t - t_start) / span * plot.width()

        def y_of(deg: float) -> float:
            return plot.center().y() - deg / lim * plot.height() / 2

        # сетка и подписи осей
        p.setPen(QPen(qcolor(C.LINE, 120), 1, Qt.DashLine))
        for v in (-lim / 2, 0.0, lim / 2):
            p.drawLine(QPointF(plot.left(), y_of(v)), QPointF(plot.right(), y_of(v)))
        p.setPen(qcolor(C.TEXT_DIM))
        for v in (-lim, 0.0, lim):
            p.drawText(QRectF(0, y_of(v) - 8, left - 4, 16), Qt.AlignRight | Qt.AlignVCenter, f"{fmt(v, 0)}°")
        p.drawText(QRectF(0, zplot.top(), left - 4, 16), Qt.AlignRight | Qt.AlignVCenter, "зум")

        # метки команд
        for t, cmd in cmds:
            if t < t_start:
                continue
            x = x_of(t)
            stop = cmd_label(cmd) == "стоп"
            p.setPen(QPen(qcolor(C.FAULT if stop else C.TEXT_FAINT, 160), 1))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, zplot.bottom()))
        p.setPen(qcolor(C.TEXT_DIM))
        last_label_x = -1e9
        for t, cmd in cmds:
            if t < t_start:
                continue
            x = x_of(t)
            if x - last_label_x < 34:  # подписи не налезают друг на друга
                continue
            last_label_x = x
            p.drawText(QRectF(x + 2, plot.top() + 1, 60, 14), Qt.AlignLeft | Qt.AlignVCenter, cmd_label(cmd))

        def trace(color: str, getter, y_fn) -> None:
            p.setPen(QPen(qcolor(color), 2))
            prev = None
            for item in pts:
                v = getter(item)
                if v is None:
                    prev = None
                    continue
                cur = QPointF(x_of(item[0]), y_fn(v))
                if prev is not None:
                    p.drawLine(prev, cur)
                prev = cur

        trace(C.ACCENT, lambda s: s[1], y_of)
        trace(C.LOST, lambda s: s[2], y_of)
        span_z = P.LENS_MAX - P.LENS_MIN
        trace(
            C.OK,
            lambda s: None if s[3] is None else (s[3] - P.LENS_MIN) / span_z,
            lambda f: zplot.bottom() - max(0.0, min(1.0, f)) * zplot.height(),
        )
        # легенда
        x = plot.left() + 6
        for color, name in ((C.ACCENT, "поворот"), (C.LOST, "наклон")):
            p.setPen(qcolor(color))
            p.drawText(QRectF(x, plot.bottom() - 16, 70, 14), Qt.AlignLeft | Qt.AlignVCenter, name)
            x += 70
        p.setPen(qcolor(C.TEXT_FAINT))
        p.drawText(QRectF(plot.right() - 110, plot.bottom() - 16, 106, 14), Qt.AlignRight | Qt.AlignVCenter,
                   f"последние {int(GRAPH_WINDOW_S)} с")


class _Worker(QThread):
    progress = Signal(str)
    done = Signal(dict)
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self, runner_factory, cancel: threading.Event, on_sample):
        super().__init__()
        self.runner_factory = runner_factory
        self.cancel = cancel
        self.on_sample = on_sample

    def run(self):
        try:
            calib = self.runner_factory(self.progress.emit, self.cancel, self.on_sample).run()
            self.done.emit(calib.to_json())
        except CalibrationCancelled:
            self.cancelled.emit()
        except Exception as e:
            self.failed.emit(str(e))


class CalibrationDialog(QDialog):
    """Мастер калибровки: камера ~3–4 минуты поворачивается и зумирует, затем возвращается на место.
    Идёт живой график и цифры; «Прервать» работает всё время (камера остановится и вернётся домой)."""

    def __init__(self, runner_factory: Callable, on_saved: Callable[[dict], None], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Калибровка камеры")
        self.resize(980, 660)
        self._factory = runner_factory  # factory(progress, cancel: threading.Event, on_sample) -> runner
        self._on_saved = on_saved
        self._worker: _Worker | None = None
        self._cancel = threading.Event()
        self._close_when_done = False
        self._live = LiveData()
        self._shown_version = -1
        intro = QLabel(
            "Программа измерит, как быстро эта камера поворачивается и зумирует на разных скоростях, "
            "чтобы слежение было плавным. Камера будет поворачиваться влево-вправо и зумировать "
            "3–4 минуты, затем вернётся на место. Наведите её на сцену с деталями (не на однотонную стену). "
            "Прервать можно в любой момент: камера остановится и вернётся на место."
        )
        intro.setWordWrap(True)
        self.off_air_check = QCheckBox("Камера НЕ в эфире — можно двигать")
        self.step_label = QLabel("")
        self.step_label.setProperty("variant", "section")
        self.graph = CalibrationGraph()
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        mono.setPixelSize(12)
        self.numbers_view = QPlainTextEdit()
        self.numbers_view.setReadOnly(True)
        self.numbers_view.setFont(mono)
        self.numbers_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.numbers_view.setMinimumWidth(380)
        self.numbers_view.setPlaceholderText("Цифры появятся по ходу замеров.")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(96)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.start_button = QPushButton("Начать калибровку")
        self.start_button.setDefault(True)
        self.start_button.setEnabled(False)
        self.cancel_button = QPushButton("Прервать")
        self.cancel_button.setProperty("variant", "stop")
        self.cancel_button.setEnabled(False)
        self.close_button = QPushButton("Закрыть")
        mid = QHBoxLayout()
        mid.addWidget(self.graph, 3)
        mid.addWidget(self.numbers_view, 2)
        row = QHBoxLayout()
        row.addWidget(self.start_button)
        row.addWidget(self.cancel_button)
        row.addStretch(1)
        row.addWidget(self.close_button)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(self.off_air_check)
        lay.addWidget(self.step_label)
        lay.addLayout(mid, 1)
        lay.addWidget(self.log_view)
        lay.addWidget(self.result_label)
        lay.addLayout(row)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self.off_air_check.toggled.connect(self._sync_start)
        self.start_button.clicked.connect(self._start)
        self.cancel_button.clicked.connect(self._request_cancel)
        self.close_button.clicked.connect(self.reject)

    # --- запуск и завершение -------------------------------------------

    def _running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _sync_start(self, *_) -> None:
        self.start_button.setEnabled(self.off_air_check.isChecked() and not self._running())

    def _start(self) -> None:
        self.start_button.setEnabled(False)
        self.off_air_check.setEnabled(False)
        self.close_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.result_label.setText("")
        self.log_view.clear()
        self._cancel = threading.Event()
        self._close_when_done = False
        self._live = LiveData()
        self._shown_version = -1
        self._worker = _Worker(self._factory, self._cancel, self._live.on_sample)
        self._worker.progress.connect(self.log_view.appendPlainText)
        self._worker.done.connect(self._done)
        self._worker.cancelled.connect(self._cancelled)
        self._worker.failed.connect(self._failed)
        self._worker.finished.connect(self._ended)
        self._refresh()
        self._timer.start()
        self._worker.start()

    def _request_cancel(self) -> None:
        """Прервать: мастер сам остановит камеру и вернёт её на место, Qt при этом не ждёт."""
        if not self._running() or self._cancel.is_set():
            return
        self._cancel.set()
        self.cancel_button.setEnabled(False)
        self.result_label.setText(CANCELLING_TEXT)

    def _refresh(self) -> None:
        snap = self._live.snapshot()
        self.graph.set_data(snap["apc"], snap["cmds"])
        if snap["version"] != self._shown_version:
            self._shown_version = snap["version"]
            self.step_label.setText(step_text(snap["step"]))
            self.numbers_view.setPlainText(numbers_text(snap))

    def _finish(self) -> None:
        # «Закрыть» и «Начать» включаются в _ended: до конца потока мастер ещё может возвращать камеру
        self.cancel_button.setEnabled(False)

    def _done(self, calib: dict) -> None:
        if self._cancel.is_set():  # «Прервать» нажали, пока шёл сигнал done: калибровку не сохраняем
            self._cancelled()
            return
        self._finish()
        self._on_saved(calib)
        self.result_label.setText("Готово: калибровка сохранена для этой камеры.")

    def _cancelled(self) -> None:
        self._finish()
        self.result_label.setText(CANCELLED_TEXT)

    def _failed(self, err: str) -> None:
        self._finish()
        self.result_label.setText(f"Калибровка не удалась: {err}")

    def _ended(self) -> None:
        """Поток мастера закончился (после done/cancelled/failed): последний кадр графика и цифр."""
        self._timer.stop()
        self._refresh()
        self.close_button.setEnabled(True)
        self.off_air_check.setEnabled(True)
        self.start_button.setEnabled(self.off_air_check.isChecked())
        if self._close_when_done:
            self._close_when_done = False
            super().reject()

    # --- закрытие = прерывание ------------------------------------------

    def reject(self) -> None:
        if self._running():
            self._close_when_done = True
            self._request_cancel()
            return  # окно закроется, когда мастер вернёт камеру на место
        super().reject()

    def closeEvent(self, event) -> None:
        if self._running():
            event.ignore()
            self.reject()
            return
        super().closeEvent(event)
