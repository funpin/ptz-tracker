from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QWidget

from ptztracker.ui.theme import C, qcolor


class JoystickWidget(QWidget):
    """Экранный джойстик: тянете ручку мышью, отпускаете — она возвращается в центр.

    moved(x, y): x вправо, y вверх, в пределах единичного круга.
    """

    moved = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(176, 176)
        self.setFocusPolicy(Qt.ClickFocus)
        self._pos = (0.0, 0.0)
        self._active = False

    def _radius(self) -> float:
        return max(10.0, min(self.width(), self.height()) / 2 - 14)

    def _center(self) -> QPointF:
        return QPointF(self.width() / 2, self.height() / 2)

    def _set(self, x: float, y: float) -> None:
        self._pos = (x, y)
        self.update()
        self.moved.emit(x, y)

    def _from_mouse(self, p) -> None:
        c, r = self._center(), self._radius()
        x = (p.x() - c.x()) / r
        y = -(p.y() - c.y()) / r
        d = math.hypot(x, y)
        if d > 1.0:
            x, y = x / d, y / d
        self._set(x, y)

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self._active = True
            self._from_mouse(e.position())

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._active:
            self._from_mouse(e.position())

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.release()

    def focusOutEvent(self, e):  # noqa: N802
        self.release()
        super().focusOutEvent(e)

    def release(self) -> None:
        if self._active or self._pos != (0.0, 0.0):
            self._active = False
            self._set(0.0, 0.0)

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c, r = self._center(), self._radius()
        p.setPen(QPen(qcolor(C.TEXT_FAINT), 3))
        p.setBrush(qcolor(C.RAISED))
        p.drawEllipse(c, r, r)
        p.setPen(QPen(qcolor(C.LINE), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, r * 0.5, r * 0.5)
        p.drawLine(QPointF(c.x() - r, c.y()), QPointF(c.x() + r, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - r), QPointF(c.x(), c.y() + r))
        # 24 риски по краю, каждая 6-я длиннее
        p.setPen(QPen(qcolor(C.TEXT_FAINT), 1.5))
        for i in range(24):
            a = math.radians(i * 15)
            ca, sa = math.cos(a), math.sin(a)
            r0 = r - (9 if i % 6 == 0 else 5)
            p.drawLine(QPointF(c.x() + r0 * ca, c.y() + r0 * sa),
                       QPointF(c.x() + (r - 2) * ca, c.y() + (r - 2) * sa))
        kx, ky = c.x() + self._pos[0] * r, c.y() - self._pos[1] * r
        p.setPen(QPen(qcolor(C.TEXT, 200), 2))
        p.setBrush(qcolor(C.ACCENT if self._active else C.TEXT_DIM))
        p.drawEllipse(QPointF(kx, ky), 13, 13)
