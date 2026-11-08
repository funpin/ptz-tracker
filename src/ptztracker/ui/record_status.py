"""Запись внизу слева: какие камеры, сколько времени и места; клик — меню остановки (макет recording-controls.html)."""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QMenu, QPushButton

from ptztracker.ui.theme import FS_CAPTION, REC_BLINK_S, SP, C, qcolor
from ptztracker.videorec.controller import RecSummary, format_clock

_LEVEL_COLOR = {"": C.TEXT, "warn": C.LOST, "crit": C.FAULT}
_LEVEL_BORDER = {"": C.LINE, "warn": C.LOST, "crit": C.FAULT}


class RecordPill(QPushButton):
    stop_requested = Signal(int)
    stop_all_requested = Signal()
    open_folder_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.NoFocus)
        self._summary = RecSummary()
        self.clicked.connect(self._show_menu)
        self.hide()

    def set_summary(self, s: RecSummary) -> None:
        self._summary = s
        self.setVisible(bool(s.rows))
        self.setText(s.text)
        if self.property("level") != s.level:
            self.setProperty("level", s.level)
            self.setStyleSheet(
                f"QPushButton {{ color: {_LEVEL_COLOR[s.level]}; border: 1px solid {_LEVEL_BORDER[s.level]};"
                f" border-radius: 12px; min-height: 22px; max-height: 22px; padding: 0 {SP[2]}px 0 {SP[4]}px;"
                f" font-size: {FS_CAPTION}px; background: transparent; }}")
        self.update()

    def build_menu(self) -> QMenu:
        m = QMenu(self)
        for r in self._summary.rows:
            state = format_clock(r.elapsed_s) if r.signal else "нет сигнала"
            a = m.addAction(f"{r.slot + 1} · {r.name} · {state} — стоп")
            a.triggered.connect(lambda _=False, slot=r.slot: self.stop_requested.emit(slot))
        m.addSeparator()
        m.addAction("Остановить все").triggered.connect(lambda: self.stop_all_requested.emit())
        m.addAction("Открыть папку").triggered.connect(lambda: self.open_folder_requested.emit())
        return m

    def _show_menu(self) -> None:
        m = self.build_menu()
        m.exec(self.mapToGlobal(QPoint(0, -m.sizeHint().height())))

    def paintEvent(self, e):  # noqa: N802
        super().paintEvent(e)
        if int(time.monotonic() / REC_BLINK_S) % 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(C.REC))
        p.drawEllipse(QPointF(SP[3] - 2, self.height() / 2), 4, 4)
