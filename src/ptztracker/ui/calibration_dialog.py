from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ptztracker.camera import he42
from ptztracker.camera import protocol as P
from ptztracker.control.calibration import CalibrationCancelled
from ptztracker.ui.theme import FS_BUTTON, FS_CAPTION, FS_TEXT, R_BUTTON, R_PANEL, SP, C, qcolor

REFRESH_MS = 100  # график и цифры обновляются ~10 раз в секунду
GRAPH_WINDOW_S = 45.0  # график показывает последние секунды: весь мастер (3–4 минуты) не уместился бы читаемо
MAX_SAMPLES = 20000  # запас по памяти: мастер даёт ~20 опросов #APC в секунду, 4 минуты — меньше 5000
MIN_RANGE_DEG = 2.0
TYPICAL_S = 210.0  # мастер обычно идёт 3–4 минуты: от этого считается «осталось ≈»
TYPICAL_STEPS = 9
FS_SECTION = 11  # подписи разделов — как в брендбуке
# поворот — лайм (то, что сейчас движется); наклон и зум — серые: оранжевый и красный значат тревогу
PAN_COLOR, TILT_COLOR, ZOOM_COLOR = C.LIME, C.TEXT_DIM, C.GREY
LEGEND = ((PAN_COLOR, "поворот"), (TILT_COLOR, "наклон"), (ZOOM_COLOR, "зум"), (None, f"последние {int(GRAPH_WINDOW_S)} с"))

CANCELLING_TEXT = "Останавливаю камеру и возвращаю на место…"
CANCELLED_TEXT = "Калибровка прервана: камера возвращена на место, ничего не сохранено, прежние настройки целы."


def fmt(x: float, digits: int = 2) -> str:
    """Число по-русски: десятичная запятая."""
    return f"{x:.{digits}f}".replace(".", ",")


def cmd_label(cmd: str) -> str:
    """Короткая подпись команды для графика: «П70» (поворот), «Н63» (наклон), «П70/60» (оба), «З70» (зум), «стоп», «домой»."""
    if cmd == P.STOP_PT or cmd == P.STOP_ZOOM:
        return "стоп"
    if cmd.startswith("#PTS") and len(cmd) == 8:
        pan, tilt = int(cmd[4:6]), int(cmd[6:8])
        if tilt == 50:
            return f"П{pan}"
        if pan == 50:
            return f"Н{tilt}"
        return f"П{pan}/{tilt}"
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


@dataclass(frozen=True)
class Row:
    kind: str  # sec — заголовок группы, row — замер, now — меряю сейчас, wait — ещё не мерили
    text: str
    value: str = ""
    dev: float | None = None  # замер / модель — для черты отклонения


def result_rows(snap: dict, running: bool) -> list[Row]:
    """Строки «Результатов»: задержки, поворот по зумам (замер / модель), наклон, зум; пока идёт мастер —
    ещё строка «меряю» и серые «ждёт» для того, что впереди."""
    res, rows = snap["results"], [Row("sec", "Результаты")]

    def single(name: str, text: str, value) -> None:
        if name in res:
            rows.append(Row("row", text, value(res[name])))
        elif running:
            rows.append(Row("wait", text, "ждёт"))

    single("dead_time", "Задержка команды", lambda r: f"{fmt(r['value'])} с")
    single("tau", "Время разгона", lambda r: f"{fmt(r['value'])} с")
    single("video_latency", "Задержка видео",
           lambda r: f"{fmt(r['value'])} с" + (f" · {r['stream']}" if r.get("stream") else ""))
    for zoom in sorted(snap["pan"]):
        rows.append(Row("sec", f"Поворот на зуме {zoom:03X}, °/с · замер / модель"))
        for c, measured, model in snap["pan"][zoom]:
            rows.append(Row("row", f"код {c}", f"{fmt(measured, 1)} / {fmt(model, 1)}",
                            measured / model if model else None))
    if "tilt" in res and res["tilt"]["codes"]:
        rows.append(Row("sec", "Наклон, °/с (доля от поворота)"))
        for c, deg, ratio in res["tilt"]["codes"]:
            rows.append(Row("row", f"код {c}", f"{fmt(deg, 1)} (×{fmt(ratio)})"))
    if "zoom" in res and res["zoom"]["points"]:
        rows.append(Row("sec", "Скорость зума, раз в секунду"))
        for c, rate in res["zoom"]["points"]:
            rows.append(Row("row", f"код {c}", f"×{fmt(math.exp(rate))}"))
    if running:
        step = snap.get("step")
        if step:
            rows.append(Row("now", f"{step['title']} — меряю…", "—"))
        later = [t for t, name in (("Наклон", "tilt"), ("Скорость зума", "zoom")) if name not in res]
        if later:
            rows.append(Row("sec", "Дальше"))
            rows += [Row("wait", t, "ждёт") for t in later]
    return rows


def numbers_text(rows: list[Row]) -> str:
    """Те же строки текстом — для проверки и копирования."""
    return "\n".join(f"{r.text}  {r.value}".rstrip() for r in rows)


def mmss(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


class StepBar(QWidget):
    """Полоска шагов мастера: пройденные — лайм, текущий — заполнен наполовину, впереди — серые."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(4)
        self.index, self.total = -1, TYPICAL_STEPS

    def set_progress(self, index: int, total: int) -> None:
        self.index, self.total = index, max(1, total)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        gap = 3
        w = (self.width() - gap * (self.total - 1)) / self.total
        for i in range(self.total):
            r = QRectF(i * (w + gap), 0, w, self.height())
            p.setBrush(qcolor(C.RAISED_HI))
            p.drawRoundedRect(r, 2, 2)
            fill = 1.0 if i < self.index else 0.4 if i == self.index else 0.0
            if fill:
                p.setBrush(qcolor(C.LIME))
                p.drawRoundedRect(QRectF(r.left(), 0, r.width() * fill, r.height()), 2, 2)
        p.end()


class ResultsView(QWidget):
    """«Результаты» строками через одну, как в правой панели; строка «меряю» — лаймовая полоска слева."""

    ROW_H = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[Row] = []

    def set_rows(self, rows: list[Row]) -> None:
        self.rows = rows
        self.setMinimumHeight(len(rows) * self.ROW_H + SP[1])
        self.update()

    def toPlainText(self) -> str:  # noqa: N802 — как у QPlainTextEdit, что тут был раньше
        return numbers_text(self.rows)

    def now_row_y(self) -> int | None:
        for i, r in enumerate(self.rows):
            if r.kind == "now":
                return i * self.ROW_H
        return None

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), qcolor(C.SURFACE))
        f = QFont(self.font())
        w, h, n = self.width(), self.ROW_H, 0
        for i, r in enumerate(self.rows):
            y = i * h
            rect = QRectF(0, y, w, h)
            if r.kind == "sec":
                n = 0
                f.setPixelSize(FS_SECTION)
                f.setWeight(QFont.DemiBold)
                f.setCapitalization(QFont.AllUppercase)
                f.setLetterSpacing(QFont.PercentageSpacing, 108)
                p.setFont(f)
                p.setPen(qcolor(C.TEXT_FAINT))
                p.drawText(rect.adjusted(SP[2], 4, -SP[2], 0), Qt.AlignLeft | Qt.AlignVCenter,
                           p.fontMetrics().elidedText(r.text, Qt.ElideRight, w - 2 * SP[2]))
                continue
            f.setPixelSize(FS_TEXT)
            f.setWeight(QFont.Normal)
            f.setCapitalization(QFont.MixedCase)
            f.setLetterSpacing(QFont.PercentageSpacing, 100)
            p.setFont(f)
            if r.kind == "now":
                p.fillRect(rect, qcolor(C.LIME, 20))
                p.fillRect(QRectF(0, y, 3, h), qcolor(C.LIME))
            elif n % 2:
                p.fillRect(rect, qcolor(C.TEXT, 9))
            n += 1
            fg = {"wait": C.GREY, "now": C.TEXT}.get(r.kind, C.TEXT_DIM)
            p.setPen(qcolor(fg))
            p.drawText(rect.adjusted(SP[2], 0, -SP[2], 0), Qt.AlignLeft | Qt.AlignVCenter, r.text)
            p.setPen(qcolor(C.GREY if r.kind == "wait" else C.TEXT))
            p.drawText(rect.adjusted(SP[2], 0, -SP[2], 0), Qt.AlignRight | Qt.AlignVCenter, r.value)
            if r.dev is not None:  # черта: где замер относительно модели, ±30 % на всю ширину
                vw = p.fontMetrics().horizontalAdvance(r.value)
                bar = QRectF(w - SP[2] - vw - SP[2] - 46, y + h / 2 - 2, 46, 4)
                p.setPen(Qt.NoPen)
                p.setBrush(qcolor(C.RAISED_HI))
                p.drawRoundedRect(bar, 2, 2)
                p.fillRect(QRectF(bar.center().x() - 0.5, bar.top() - 2, 1, 8), qcolor(C.GREY))
                k = max(-1.0, min(1.0, (r.dev - 1.0) / 0.3))
                p.fillRect(QRectF(bar.center().x() + k * bar.width() / 2 - 1, bar.top() - 2, 2, 8), qcolor(C.LIME))
        p.end()


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
        p.fillRect(self.rect(), qcolor(C.BG))
        p.setPen(QPen(qcolor(C.LINE), 1))
        p.setBrush(qcolor(C.SURFACE))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), R_PANEL, R_PANEL)
        small = QFont(self.font())
        small.setPixelSize(FS_CAPTION - 1)
        # заголовок и легенда — над графиком, а не поверх кривых
        head = QFont(small)
        head.setPixelSize(FS_SECTION)
        head.setWeight(QFont.DemiBold)
        head.setCapitalization(QFont.AllUppercase)
        head.setLetterSpacing(QFont.PercentageSpacing, 108)
        p.setFont(head)
        p.setPen(qcolor(C.TEXT_FAINT))
        p.drawText(QRectF(SP[2], SP[1], w / 2, 16), Qt.AlignLeft | Qt.AlignVCenter, "Движение камеры")
        p.setFont(small)
        x = w - SP[2]
        for color, name in reversed(LEGEND):
            tw = p.fontMetrics().horizontalAdvance(name)
            x -= tw
            p.setPen(qcolor(C.TEXT_DIM if color else C.TEXT_FAINT))
            p.drawText(QRectF(x, SP[1], tw, 16), Qt.AlignLeft | Qt.AlignVCenter, name)
            if color:
                x -= 19
                p.fillRect(QRectF(x, SP[1] + 7, 14, 2), qcolor(color))
            x -= SP[2]
        left, right, gap = 46, SP[2], SP[2]
        strip = QRectF(left, SP[1] + 16 + SP[1], w - left - right, 16)  # строка кодов скорости
        top, bottom = strip.bottom() + SP[0], h - SP[2]
        zoom_h = max(30, int((bottom - top) * 0.14))
        plot = QRectF(left, top, strip.width(), bottom - top - zoom_h - gap)
        zplot = QRectF(left, plot.bottom() + gap, plot.width(), zoom_h)
        apc, cmds = self._apc, self._cmds
        if len(apc) < 2:  # до начала — только пояснение: пустые полосы кодов и зума выглядели как ошибка
            p.setPen(qcolor(C.TEXT_FAINT))
            p.drawText(QRectF(0, strip.top(), w, bottom - strip.top()), Qt.AlignCenter,
                       "График появится, когда камера начнёт двигаться")
            return
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(C.RAISED))
        p.drawRoundedRect(strip, 4, 4)
        p.setPen(QPen(qcolor(C.LINE), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(zplot, 4, 4)
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
        for v in (-lim, 0.0, lim):
            p.setPen(QPen(qcolor(C.RAISED_HI if v else C.LINE), 1))
            p.drawLine(QPointF(plot.left(), y_of(v)), QPointF(plot.right(), y_of(v)))
        p.setPen(qcolor(C.TEXT_FAINT))
        for v in (-lim, 0.0, lim):
            p.drawText(QRectF(0, y_of(v) - 8, left - 6, 16), Qt.AlignRight | Qt.AlignVCenter, f"{fmt(v, 0)}°")
        p.drawText(QRectF(0, zplot.top(), left - 6, zplot.height()), Qt.AlignRight | Qt.AlignVCenter, "зум")

        # коды скорости: строка сверху и тонкие границы; «стоп» и «домой» не рисуются — их было слишком много
        moves = [(t, cmd_label(c)) for t, c in cmds if cmd_label(c) not in ("стоп", "домой")]
        visible = [(t, lab) for t, lab in moves if t >= t_start]
        if moves and moves[-1][0] >= t_start:  # текущий участок подсвечен
            x0 = x_of(moves[-1][0])
            p.fillRect(QRectF(x0, plot.top(), plot.right() - x0, zplot.bottom() - plot.top()), qcolor(C.LIME, 12))
        last_label_x = -1e9
        for i, (t, lab) in enumerate(visible):
            x = x_of(t)
            p.setPen(QPen(qcolor(C.RAISED_HI), 1))
            p.drawLine(QPointF(x, strip.bottom()), QPointF(x, zplot.bottom()))
            if x - last_label_x < 34:  # подписи не налезают друг на друга
                continue
            last_label_x = x
            p.setPen(qcolor(C.TEXT if i == len(visible) - 1 else C.TEXT_DIM))
            p.drawText(QRectF(x + 4, strip.top(), 52, strip.height()), Qt.AlignLeft | Qt.AlignVCenter, lab)

        def trace(color: str, width: float, getter, y_fn) -> None:
            p.setPen(QPen(qcolor(color), width))
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

        trace(TILT_COLOR, 1.5, lambda s: s[2], y_of)
        trace(PAN_COLOR, 2, lambda s: s[1], y_of)
        span_z = P.LENS_MAX - P.LENS_MIN
        trace(
            ZOOM_COLOR, 1.5,
            lambda s: None if s[3] is None else (s[3] - P.LENS_MIN) / span_z,
            lambda f: zplot.bottom() - 3 - max(0.0, min(1.0, f)) * (zplot.height() - 6),
        )
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(PAN_COLOR))
        p.drawEllipse(QPointF(x_of(pts[-1][0]), y_of(pts[-1][1])), 3.5, 3.5)


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
    Идёт живой график и цифры; «Прервать» работает всё время (камера остановится и вернётся домой).
    Вид — по макету docs/design/mockups/calibration.html."""

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
        self._t0: float | None = None
        self._t_end: float | None = None
        # шапка: что меряем сейчас, номер шага, сколько прошло и осталось; под ней — полоска шагов
        self.step_label = QLabel("Перед началом")
        self.step_label.setStyleSheet(f"font-size: {FS_BUTTON}px; font-weight: 600;")
        self.step_count_label = QLabel("")
        self.step_count_label.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
        self.eta_label = QLabel("займёт 3–4 минуты")
        self.eta_label.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
        self.step_bar = StepBar()
        self.intro = QLabel(
            f"<b style='color:{C.TEXT}'>Программа измерит, как быстро эта камера поворачивается и зумирует</b>"
            " — чтобы слежение было плавным.<ul style='margin:6px 0 0 -20px'>"
            "<li>Займёт 3–4 минуты: камера будет поворачиваться влево-вправо и зумировать, потом вернётся на место.</li>"
            "<li>Наведите её на сцену с деталями, не на однотонную стену.</li>"
            "<li>Прервать можно в любой момент — камера остановится и вернётся.</li></ul>"
        )
        self.intro.setWordWrap(True)
        self.intro.setStyleSheet(
            f"background: {C.SURFACE}; border: 1px solid {C.LINE}; border-radius: {R_PANEL}px;"
            f" padding: {SP[2]}px {SP[3]}px; color: {C.TEXT_DIM}; line-height: 140%;"
        )
        self.graph = CalibrationGraph()
        self.numbers_view = ResultsView()
        self.numbers_view.set_rows(result_rows({"results": {}, "pan": {}, "step": None}, running=True))
        self.results_scroll = QScrollArea()
        self.results_scroll.setWidget(self.numbers_view)
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setMinimumWidth(340)
        self.results_scroll.setStyleSheet(
            f"QScrollArea {{ background: {C.SURFACE}; border: 1px solid {C.LINE}; border-radius: {R_PANEL}px; }}"
        )
        self.results_scroll.viewport().setStyleSheet(f"background: {C.SURFACE};")
        # строка «Сейчас»: последняя запись журнала или итог; полный журнал — по кнопке
        self.now_dot = QLabel()
        self.now_dot.setFixedSize(8, 8)
        self.now_label = QLabel("")
        self.now_label.setStyleSheet(f"color: {C.TEXT_DIM};")
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.hide()
        self.log_toggle = QPushButton("Журнал ▾")
        self.log_toggle.setProperty("variant", "ghost")
        self.log_toggle.setCheckable(True)
        self.log_toggle.setStyleSheet(f"font-size: {FS_CAPTION}px; color: {C.TEXT_FAINT}; min-height: 24px;")
        self.now_row = QFrame()
        self.now_row.setObjectName("nowRow")
        now = QHBoxLayout(self.now_row)
        now.setContentsMargins(SP[2], SP[0], SP[0], SP[0])
        now.setSpacing(SP[2])
        now.addWidget(self.now_dot)
        now.addWidget(self.now_label, 1)
        now.addWidget(self.result_label, 1)
        now.addWidget(self.log_toggle)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(110)
        self.log_view.hide()
        self.off_air_check = QCheckBox("Камера НЕ в эфире — можно двигать")
        self.off_air_hint = QLabel("отметьте, чтобы начать")
        self.off_air_hint.setStyleSheet(f"color: {C.LOST}; font-size: {FS_CAPTION}px;")
        self.start_button = QPushButton("Начать калибровку")
        self.start_button.setProperty("variant", "primary")
        self.start_button.setDefault(True)
        self.start_button.setEnabled(False)
        self.cancel_button = QPushButton("Прервать и вернуть на место")
        self.cancel_button.setProperty("variant", "stop")
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        self.close_button = QPushButton("Закрыть")
        self.close_button.setAutoDefault(False)
        head = QHBoxLayout()
        head.setSpacing(SP[1])
        head.addWidget(self.step_label)
        head.addWidget(self.step_count_label, 0, Qt.AlignBottom)
        head.addStretch(1)
        head.addWidget(self.eta_label)
        mid = QHBoxLayout()
        mid.setSpacing(SP[2])
        mid.addWidget(self.graph, 3)
        mid.addWidget(self.results_scroll, 2)
        row = QHBoxLayout()
        row.setSpacing(SP[2])
        row.addWidget(self.off_air_check)
        row.addWidget(self.off_air_hint)
        row.addStretch(1)
        row.addWidget(self.cancel_button)
        row.addWidget(self.close_button)
        row.addWidget(self.start_button)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP[3], SP[3], SP[3], SP[3])
        lay.setSpacing(SP[2])
        lay.addLayout(head)
        lay.addWidget(self.step_bar)
        lay.addWidget(self.intro)
        lay.addLayout(mid, 1)
        lay.addWidget(self.now_row)
        lay.addWidget(self.log_view)
        lay.addLayout(row)
        self._paint_now("idle")
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self.off_air_check.toggled.connect(self._sync_start)
        self.start_button.clicked.connect(self._start)
        self.cancel_button.clicked.connect(self._request_cancel)
        self.close_button.clicked.connect(self.reject)
        self.log_toggle.toggled.connect(self._toggle_log)

    def _paint_now(self, state: str) -> None:
        """Точка и рамка строки «Сейчас»: идёт — лайм, готово — лаймовая рамка, ошибка — красная, иначе серое."""
        dot = {"run": C.LIME, "done": C.LIME, "fail": C.FAULT}.get(state, C.GREY)
        border = {"done": C.LIME, "fail": C.FAULT}.get(state, C.LINE)
        self.now_dot.setStyleSheet(f"background: {dot}; border-radius: 4px;")
        self.now_row.setStyleSheet(
            f"QFrame#nowRow {{ background: {C.SURFACE}; border: 1px solid {border}; border-radius: {R_BUTTON}px; }}"
        )
        if state == "idle":
            self.now_label.setText("Камера стоит. Отметьте флажок внизу и нажмите «Начать калибровку».")

    def _toggle_log(self, on: bool) -> None:
        self.log_view.setVisible(on)
        self.log_toggle.setText("Журнал ▴" if on else "Журнал ▾")

    def _progress(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        self.now_label.setText(f"Сейчас: {text}")

    def _set_result(self, text: str, state: str) -> None:
        self.result_label.setText(text)
        self.result_label.setVisible(bool(text))
        self.now_label.setVisible(not text)
        self._paint_now(state)

    def _set_phase(self, running: bool) -> None:
        """Во время замеров — только «Прервать»; до и после — «Закрыть» и главная белая «Начать»/«Повторить»."""
        self.cancel_button.setVisible(running)
        self.start_button.setVisible(not running)
        self.close_button.setVisible(not running)
        self.intro.setVisible(False if running else self._t0 is None)
        self.off_air_hint.setVisible(not running and not self.off_air_check.isChecked())

    # --- запуск и завершение -------------------------------------------

    def _running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _sync_start(self, *_) -> None:
        self.start_button.setEnabled(self.off_air_check.isChecked() and not self._running())
        self.off_air_hint.setVisible(not self.off_air_check.isChecked() and not self._running())

    def _start(self) -> None:
        self.start_button.setEnabled(False)
        self.off_air_check.setEnabled(False)
        self.close_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._t0, self._t_end = time.monotonic(), None
        self._set_phase(True)
        self._set_result("", "run")
        self.now_label.setText("Сейчас: камера готовится…")
        self.log_view.clear()
        self._cancel = threading.Event()
        self._close_when_done = False
        self._live = LiveData()
        self._shown_version = -1
        self._worker = _Worker(self._factory, self._cancel, self._live.on_sample)
        self._worker.progress.connect(self._progress)
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
        self._set_result(CANCELLING_TEXT, "run")

    def _refresh(self) -> None:
        snap = self._live.snapshot()
        self.graph.set_data(snap["apc"], snap["cmds"])
        if self._t0 is not None:
            end = self._t_end if self._t_end is not None else time.monotonic()
            spent = end - self._t0
            if self._t_end is not None:
                self.eta_label.setText(f"заняло {mmss(spent)}")
            elif spent < TYPICAL_S:
                self.eta_label.setText(f"прошло {mmss(spent)} · осталось ≈ {mmss(TYPICAL_S - spent)}")
            else:
                self.eta_label.setText(f"прошло {mmss(spent)} · почти готово")
        if snap["version"] != self._shown_version:
            self._shown_version = snap["version"]
            step = snap["step"]
            if step:
                self.step_label.setText(step["title"])
                self.step_count_label.setText(f"шаг {step['index'] + 1} из {step['total']}")
                self.step_bar.set_progress(step["index"], step["total"])
            self.numbers_view.set_rows(result_rows(snap, running=self._running()))
            y = self.numbers_view.now_row_y()
            if y is not None:
                self.results_scroll.ensureVisible(0, y, 0, ResultsView.ROW_H * 2)

    def _finish(self) -> None:
        # «Закрыть» и «Начать» включаются в _ended: до конца потока мастер ещё может возвращать камеру
        self.cancel_button.setEnabled(False)

    def _done(self, calib: dict) -> None:
        if self._cancel.is_set():  # «Прервать» нажали, пока шёл сигнал done: калибровку не сохраняем
            self._cancelled()
            return
        self._finish()
        self._on_saved(calib)
        self.step_bar.set_progress(self.step_bar.total, self.step_bar.total)
        self._set_result("Готово: калибровка сохранена для этой камеры.", "done")

    def _cancelled(self) -> None:
        self._finish()
        self._set_result(CANCELLED_TEXT, "idle-done")

    def _failed(self, err: str) -> None:
        self._finish()
        self._set_result(f"Калибровка не удалась: {err}", "fail")

    def _ended(self) -> None:
        """Поток мастера закончился (после done/cancelled/failed): последний кадр графика и цифр."""
        self._timer.stop()
        if self._t0 is not None:
            self._t_end = time.monotonic()
        self._shown_version = -1  # «меряю» и «ждёт» больше не показываются
        self._refresh()
        self.start_button.setText("Повторить")
        self._set_phase(False)
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
