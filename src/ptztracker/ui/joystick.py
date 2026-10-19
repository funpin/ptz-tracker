from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class JoystickWidget(QWidget):
    """Экранный джойстик: тянете ручку мышью, отпускаете — она возвращается в центр.

    moved(x, y): x вправо, y вверх, в пределах единичного круга.
    """

    moved = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(160, 160)
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
        p.setPen(QPen(QColor(110, 120, 140), 2))
        p.setBrush(QColor(40, 44, 54))
        p.drawEllipse(c, r, r)
        p.setPen(QPen(QColor(70, 78, 95), 1))
        p.drawLine(QPointF(c.x() - r, c.y()), QPointF(c.x() + r, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - r), QPointF(c.x(), c.y() + r))
        kx, ky = c.x() + self._pos[0] * r, c.y() - self._pos[1] * r
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(59, 130, 246) if self._active else QColor(150, 160, 180))
        p.drawEllipse(QPointF(kx, ky), 14, 14)
