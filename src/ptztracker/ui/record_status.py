"""Запись внизу слева: номера камер и запас времени; подробности — в подсказке, клик — меню (макет recording-text.html)."""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QMenu, QPushButton

from ptztracker.ui.theme import FS_CAPTION, REC_BLINK_S, SP, C, qcolor
from ptztracker.videorec.controller import RecSummary, format_clock

CHIPS_X, CHIP, CHIP_GAP = 24, 17, 3  # номера камер слева: откуда, размер, зазор
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
        self._look = None  # (уровень, сколько камер): стиль пересобирается только при смене
        self.clicked.connect(self._show_menu)
        self.hide()

    def set_summary(self, s: RecSummary) -> None:
        self._summary = s
        self.setVisible(bool(s.rows))
        self.setText(s.text)
        self.setToolTip(s.tooltip)
        look = (s.level, len(s.rows))
        if look != self._look:
            self._look = look
            self.setProperty("level", s.level)
            left = CHIPS_X + len(s.rows) * (CHIP + CHIP_GAP) + SP[0]
            self.setStyleSheet(
                f"QPushButton {{ color: {_LEVEL_COLOR[s.level]}; border: 1px solid {_LEVEL_BORDER[s.level]};"
                f" border-radius: 12px; min-height: 22px; max-height: 22px; padding: 0 {SP[2]}px 0 {left}px;"
                f" font-size: {FS_CAPTION}px; background: transparent; }}")
        self.update()

    def numbers(self) -> list[int]:
        return [r.slot + 1 for r in self._summary.rows]

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
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        if int(time.monotonic() / REC_BLINK_S) % 2 == 0:
            p.setBrush(qcolor(C.REC))
            p.drawEllipse(QPointF(SP[3] - 2, self.height() / 2), 4, 4)
        font = p.font()
        font.setPixelSize(FS_CAPTION - 1)
        font.setBold(True)
        p.setFont(font)
        y = (self.height() - CHIP) / 2
        for i, n in enumerate(self.numbers()):
            chip = QRectF(CHIPS_X + i * (CHIP + CHIP_GAP), y, CHIP, CHIP)
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(C.RAISED_HI))
            p.drawRoundedRect(chip, 4, 4)
            p.setPen(qcolor(C.TEXT))
            p.drawText(chip, Qt.AlignCenter, str(n))
