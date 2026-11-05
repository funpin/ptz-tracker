from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ptztracker.diagnostics.checks import ALL_CHECKS, MOTION_CHECKS, TITLES, CheckResult
from ptztracker.diagnostics.runner import DiagContext, default_report_dir, run_diagnostics
from ptztracker.ui.calibration_dialog import StepBar, mmss
from ptztracker.ui.theme import FS_BUTTON, FS_CAPTION, FS_TEXT, R_PANEL, SP, C, qcolor

ORDER = [cid for cid, _fn, _http in ALL_CHECKS]
# значок и цвет итога: лайм — в порядке, оранжевый — замечание, красный — ошибка, пунктир — пропущено
MARKS = {"ok": ("✓", C.LIME), "warn": ("!", C.LOST), "fail": ("✕", C.FAULT)}
SKIP_NOTE = "пропустится: движение не разрешено"


class _Worker(QThread):
    progress = Signal(str)
    ask = Signal(str, float)
    result = Signal(object)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, ctx_kwargs: dict):
        super().__init__()
        self.ctx_kwargs = ctx_kwargs

    def _ask_user(self, msg: str, seconds: float) -> None:
        self.ask.emit(msg, seconds)
        time.sleep(seconds)
        self.ask.emit("", 0.0)

    def run(self):
        try:
            ctx = DiagContext(ask_user=self._ask_user, progress=self.progress.emit, on_result=self.result.emit,
                              **self.ctx_kwargs)
            self.done.emit(str(run_diagnostics(ctx)))
        except Exception as e:  # отчёт должен получиться в любом случае; если нет — показать причину
            self.failed.emit(str(e))


@dataclass
class CheckRow:
    cid: str
    title: str
    status: str = "wait"  # wait | now | ok | warn | fail | skip
    note: str = ""  # итог коротко (полностью — во всплывающей подсказке)


class Checklist(QWidget):
    """Список проверок строками через одну: значок итога, название, итог коротко; текущая — лаймовая полоска."""

    ROW_H = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = [CheckRow(cid, TITLES[cid]) for cid in ORDER]
        self.setMinimumHeight(len(self.rows) * self.ROW_H + 2 * SP[0])
        self.setMouseTracking(True)

    def row(self, cid: str) -> CheckRow:
        return next(r for r in self.rows if r.cid == cid)

    def reset(self, allow_move: bool) -> None:
        for r in self.rows:
            r.status = "wait"
            r.note = SKIP_NOTE if r.cid in MOTION_CHECKS and not allow_move else ""
        self.update()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        i = int((e.position().y() - SP[0]) // self.ROW_H)
        self.setToolTip(self.rows[i].note if 0 <= i < len(self.rows) else "")

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.ROW_H
        p.setPen(qcolor(C.LINE))
        p.setBrush(qcolor(C.SURFACE))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), R_PANEL, R_PANEL)
        f = QFont(self.font())
        for i, r in enumerate(self.rows):
            y = SP[0] + i * h
            rect = QRectF(1, y, w - 2, h)
            if r.status == "now":
                p.fillRect(rect, qcolor(C.LIME, 20))
                p.fillRect(QRectF(1, y, 3, h), qcolor(C.LIME))
            elif i % 2:
                p.fillRect(rect, qcolor(C.TEXT, 9))
            c = QRectF(SP[2], y + (h - 18) / 2, 18, 18)
            if r.status in MARKS:
                mark, col = MARKS[r.status]
                p.setPen(Qt.NoPen)
                p.setBrush(qcolor(col, 40))
                p.drawEllipse(c)
                f.setPixelSize(FS_CAPTION - 1)
                f.setBold(True)
                p.setFont(f)
                p.setPen(qcolor(col))
                p.drawText(c, Qt.AlignCenter, mark)
            else:
                pen = p.pen()
                pen.setColor(qcolor(C.LIME if r.status == "now" else C.GREY))
                pen.setWidthF(2 if r.status == "now" else 1.5)
                pen.setStyle(Qt.DashLine if r.status == "skip" else Qt.SolidLine)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                if r.status == "now":  # «крутится» — дуга без четверти
                    p.drawArc(c.adjusted(1, 1, -1, -1), 0, 270 * 16)
                else:
                    p.drawEllipse(c.adjusted(1, 1, -1, -1))
            f.setBold(False)
            f.setPixelSize(FS_TEXT)
            p.setFont(f)
            dim = r.status in ("wait", "skip")
            p.setPen(qcolor(C.GREY if dim else C.TEXT))
            title_w = int(w * 0.52)
            p.drawText(QRectF(c.right() + SP[2], y, title_w, h), Qt.AlignLeft | Qt.AlignVCenter, r.title)
            f.setPixelSize(FS_CAPTION)
            p.setFont(f)
            p.setPen(qcolor(C.GREY if dim else C.TEXT_DIM))
            note = r.note or ("проверяю…" if r.status == "now" else "")
            nr = QRectF(c.right() + SP[2] + title_w, y, w - c.right() - 2 * SP[2] - title_w - SP[1], h)
            p.drawText(nr, Qt.AlignRight | Qt.AlignVCenter, p.fontMetrics().elidedText(note, Qt.ElideRight,
                                                                                       int(nr.width())))
        p.end()


def reveal(path: Path) -> None:
    """Показать файл в Finder / Проводнике выделенным; иначе — открыть папку."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
            return
        if sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{path}"])
            return
    except OSError:
        pass
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


def _word(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def summary_text(rows: list[CheckRow]) -> str:
    """«7 из 8 в порядке, 1 замечание» — итог одной строкой."""
    ok = sum(r.status == "ok" for r in rows)
    warn = sum(r.status == "warn" for r in rows)
    fail = sum(r.status == "fail" for r in rows)
    skip = sum(r.status == "skip" for r in rows)
    parts = [f"{ok} из {len(rows)} в порядке"]
    if warn:
        parts.append(f"{warn} {_word(warn, 'замечание', 'замечания', 'замечаний')}")
    if fail:
        parts.append(f"{fail} {_word(fail, 'ошибка', 'ошибки', 'ошибок')}")
    if skip:
        parts.append(f"{skip} {_word(skip, 'пропущена', 'пропущены', 'пропущено')}")
    return ", ".join(parts)


class DiagnosticsDialog(QDialog):
    """Диагностика по макету docs/design/mockups/menu-camera-diag.html: что проверяется сейчас, полоска шагов,
    просьба к оператору крупной карточкой с отсчётом, список проверок со значками, журнал — по кнопке."""

    def __init__(
        self,
        host: str,
        username: str | None,
        password: str | None,
        out_dir: Path | None = None,
        notify_port: int = 31004,
        notify_wait_s: float = 15.0,
        parent=None,
        name: str | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Диагностика камеры «{name}» · {host}" if name else f"Диагностика камеры {host}")
        self.resize(640, 640)
        if out_dir is None:
            out_dir = default_report_dir()
        self._ctx_kwargs = dict(
            host=host,
            username=username,
            password=password,
            out_dir=Path(out_dir),
            notify_port=notify_port,
            notify_wait_s=notify_wait_s,
        )
        self.result_path: str | None = None
        self._worker: _Worker | None = None
        self._t0: float | None = None

        self.step_label = QLabel("Перед началом")
        self.step_label.setStyleSheet(f"font-size: {FS_BUTTON}px; font-weight: 600;")
        self.step_count_label = QLabel("")
        self.step_count_label.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
        self.eta_label = QLabel("займёт 1–2 минуты")
        self.eta_label.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
        self.step_bar = StepBar()
        self.step_bar.set_progress(-1, len(ORDER))
        self.intro = QLabel(
            f"<b style='color:{C.TEXT}'>Программа проверит связь, видео и команды камеры</b> и соберёт "
            f"архив-отчёт для разработчика.<br>Отчёт сохранится в папку: <span style='color:{C.TEXT}'>{out_dir}</span>"
        )
        self.intro.setWordWrap(True)
        self.intro.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.intro.setStyleSheet(f"color: {C.TEXT_DIM}; line-height: 140%;")
        # просьба к оператору — белая карточка с крупным отсчётом: её нельзя пропустить
        self.ask_card = QFrame()
        self.ask_card.setObjectName("askCard")
        self.ask_card.setStyleSheet(f"QFrame#askCard {{ background: {C.SURFACE}; border: 1px solid {C.TEXT};"
                                    f" border-radius: {R_PANEL}px; }}")
        self.ask_title = QLabel("")
        self.ask_title.setWordWrap(True)
        self.ask_title.setStyleSheet("font-size: 17px; font-weight: 600;")
        self.ask_count = QLabel("")
        self.ask_count.setStyleSheet("font-size: 28px; font-weight: 600;")
        ak = QHBoxLayout(self.ask_card)
        ak.setContentsMargins(SP[3], SP[2], SP[3], SP[2])
        ak.addWidget(self.ask_title, 1)
        ak.addWidget(self.ask_count)
        self.ask_card.hide()
        self.checklist = Checklist()
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result_label.hide()
        self.log_toggle = QPushButton("Журнал ▾")
        self.log_toggle.setProperty("variant", "ghost")
        self.log_toggle.setCheckable(True)
        self.log_toggle.setAutoDefault(False)
        self.log_toggle.setStyleSheet(f"font-size: {FS_CAPTION}px; color: {C.TEXT_FAINT}; min-height: 24px;")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(120)
        self.log_view.hide()
        self.allow_move_check = QCheckBox("Разрешить движение камеры")
        self.move_note = QLabel("Только если камера НЕ в эфире. Без него пропустятся 3 проверки: "
                                "задержка, скорости поворота и зум.")
        self.move_note.setWordWrap(True)
        self.move_note.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px; margin-left: 24px;")
        self.start_button = QPushButton("Начать проверку")
        self.start_button.setProperty("variant", "primary")
        self.start_button.setDefault(True)
        self.open_button = QPushButton("Показать файл")
        self.open_button.setEnabled(False)
        self.open_button.hide()
        self.open_button.setAutoDefault(False)
        self.close_button = QPushButton("Закрыть")
        self.close_button.setAutoDefault(False)

        head = QHBoxLayout()
        head.setSpacing(SP[1])
        head.addWidget(self.step_label)
        head.addWidget(self.step_count_label, 0, Qt.AlignBottom)
        head.addStretch(1)
        head.addWidget(self.eta_label)
        log_row = QHBoxLayout()
        log_row.addWidget(self.log_toggle)
        log_row.addStretch(1)
        buttons = QHBoxLayout()
        buttons.setSpacing(SP[2])
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.open_button)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP[3], SP[3], SP[3], SP[3])
        lay.setSpacing(SP[2])
        lay.addLayout(head)
        lay.addWidget(self.step_bar)
        lay.addWidget(self.intro)
        lay.addWidget(self.ask_card)
        lay.addWidget(self.checklist)
        lay.addWidget(self.result_label)
        lay.addWidget(self.allow_move_check)
        lay.addWidget(self.move_note)
        lay.addLayout(log_row)
        lay.addWidget(self.log_view)
        lay.addStretch(1)
        lay.addLayout(buttons)

        self._countdown_end = 0.0
        self._prompt_text = ""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._clock = QTimer(self)
        self._clock.setInterval(500)
        self._clock.timeout.connect(self._tick_clock)

        self.checklist.reset(False)
        self.allow_move_check.toggled.connect(lambda on: self._running() or self.checklist.reset(on))
        self.start_button.clicked.connect(self._start)
        self.open_button.clicked.connect(self._open_folder)
        self.close_button.clicked.connect(self.reject)
        self.log_toggle.toggled.connect(self._toggle_log)

    @property
    def prompt_label(self) -> QLabel:  # прежнее имя: просьба к оператору
        return self.ask_title

    def _running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _toggle_log(self, on: bool) -> None:
        self.log_view.setVisible(on)
        self.log_toggle.setText("Журнал ▴" if on else "Журнал ▾")

    def _start(self) -> None:
        self.start_button.setEnabled(False)
        self.start_button.hide()  # во время проверки — только «Закрыть» (неактивна: камера может двигаться)
        self.allow_move_check.setEnabled(False)
        self.close_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.open_button.hide()
        self.result_path = None
        self.result_label.hide()
        self.intro.hide()
        self.move_note.hide()
        self.log_view.clear()
        self.checklist.reset(self.allow_move_check.isChecked())
        self._t0 = time.monotonic()
        self._clock.start()
        self._tick_clock()
        self._worker = _Worker({**self._ctx_kwargs, "allow_move": self.allow_move_check.isChecked()})
        self._worker.progress.connect(self._on_progress)
        self._worker.ask.connect(self._on_ask)
        self._worker.result.connect(self._on_result)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        for i, r in enumerate(self.checklist.rows):
            if text == f"Проверка: {r.title}…":
                r.status, r.note = "now", ""
                self.step_label.setText(r.title)
                self.step_count_label.setText(f"проверка {i + 1} из {len(ORDER)}")
                self.step_bar.set_progress(i, len(ORDER))
                self.checklist.update()

    def _on_result(self, res: CheckResult) -> None:
        if res.id in ORDER:
            r = self.checklist.row(res.id)
            r.status, r.note = res.status, res.summary
            self.checklist.update()

    def _on_ask(self, msg: str, seconds: float) -> None:
        self._prompt_text = msg
        self._countdown_end = time.monotonic() + seconds
        if msg:
            self.ask_title.setText(msg)
            self.ask_card.show()
            self._timer.start(200)
            self._tick()
        else:
            self._timer.stop()
            self.ask_card.hide()

    def _tick(self) -> None:
        left = max(0, self._countdown_end - time.monotonic())
        self.ask_count.setText(f"{left:.0f} с")

    def _tick_clock(self) -> None:
        if self._t0 is not None:
            self.eta_label.setText(f"прошло {mmss(time.monotonic() - self._t0)}")

    def _finish(self) -> None:
        self.close_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.start_button.show()
        self.start_button.setText("Повторить")
        self.start_button.setProperty("variant", "")
        self.allow_move_check.setEnabled(True)
        self._timer.stop()
        self._clock.stop()
        self.ask_card.hide()
        if self._t0 is not None:
            self.eta_label.setText(f"заняло {mmss(time.monotonic() - self._t0)}")
        self.step_bar.set_progress(len(ORDER), len(ORDER))
        self.result_label.show()

    def _on_done(self, path: str) -> None:
        self._finish()
        self.result_path = path
        self.step_label.setText("Готово")
        self.step_count_label.setText(summary_text(self.checklist.rows))
        self.result_label.setText(f"{summary_text(self.checklist.rows)}. Отчёт сохранён: "
                                  f"<b>{Path(path).name}</b> — пришлите этот файл разработчику.")
        self.open_button.setProperty("variant", "primary")
        self.open_button.show()
        self.open_button.setEnabled(True)
        self._restyle()

    def _on_failed(self, err: str) -> None:
        self._finish()
        self.step_label.setText("Диагностика прервалась")
        self.result_label.setText(f"<span style='color:{C.FAULT}'>Диагностика прервалась: {err}</span>")
        self._restyle()

    def _restyle(self) -> None:
        for b in (self.start_button, self.open_button):
            b.style().unpolish(b)
            b.style().polish(b)

    def _open_folder(self) -> None:
        if self.result_path:
            reveal(Path(self.result_path))

    def reject(self) -> None:
        if self._running():
            return  # нельзя закрыть посреди проверки — камера может быть в движении
        super().reject()
