"""Рисунки для справки F1: геймпад, клавиатура, мышь. Свои линии в стиле брендбука, не фото.

Каждый рисунок знает свои элементы (ключ → область), подсвечивает элемент лаймом и умеет показать
движение: стик крутится или качается, кнопка «нажимается». Наведение мыши на элемент — сигнал hovered."""

from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ptztracker.ui.theme import C, qcolor

ANIM_MS = 33


class _Drawing(QWidget):
    """Общее: масштаб из логических координат, подсветка, анимация, наведение."""

    hovered = Signal(object)  # ключ элемента или None
    VIEW = (560.0, 360.0)  # логические координаты рисунка

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(420, 280)
        self.highlighted: str | None = None
        self._anim: str | None = None  # circle | sway | press | drag
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(ANIM_MS)
        self._timer.timeout.connect(self.update)
        self._hover: str | None = None

    def sizeHint(self) -> QSize:
        return QSize(int(self.VIEW[0]), int(self.VIEW[1]))

    # --- для наследников --------------------------------------------------------

    def elements(self) -> dict[str, QPainterPath]:
        raise NotImplementedError

    def animation_for(self, key: str) -> str | None:
        return "press"

    # --- общее ------------------------------------------------------------------

    @property
    def animating(self) -> bool:
        return self._timer.isActive()

    def set_highlight(self, key: str | None) -> None:
        if key is not None and key not in self.elements():
            key = None
        self.highlighted = key
        self._anim = self.animation_for(key) if key else None
        if self._anim:
            self._clock.restart()
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def phase(self) -> float:
        """0…1 по кругу, период 1,6 с."""
        return (self._clock.elapsed() % 1600) / 1600 if self._timer.isActive() else 0.0

    def _scale(self) -> tuple[float, float, float]:
        k = min(self.width() / self.VIEW[0], self.height() / self.VIEW[1])
        return k, (self.width() - self.VIEW[0] * k) / 2, (self.height() - self.VIEW[1] * k) / 2

    def element_at(self, pos: QPointF) -> str | None:
        k, dx, dy = self._scale()
        lp = QPointF((pos.x() - dx) / k, (pos.y() - dy) / k)
        hit = [key for key, path in self.elements().items() if path.contains(lp)]
        return hit[-1] if hit else None  # верхний (нарисован последним)

    def mouseMoveEvent(self, e):  # noqa: N802
        key = self.element_at(e.position())
        if key != self._hover:
            self._hover = key
            self.hovered.emit(key)

    def leaveEvent(self, _e):  # noqa: N802
        if self._hover is not None:
            self._hover = None
            self.hovered.emit(None)

    def _begin(self) -> QPainter:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        k, dx, dy = self._scale()
        p.translate(dx, dy)
        p.scale(k, k)
        return p

    def _part(self, p: QPainter, key: str, path: QPainterPath, lit: bool | None = None) -> None:
        lit = self.highlighted == key if lit is None else lit
        p.setPen(QPen(qcolor(C.LIME if lit else C.GREY), 1.5))
        p.setBrush(qcolor(C.LIME, 56) if lit else qcolor(C.RAISED))
        if lit and self._anim == "press":
            s = 1 - 0.08 * math.sin(self.phase() * math.pi * 2) ** 2
            c = path.boundingRect().center()
            p.save()
            p.translate(c)
            p.scale(s, s)
            p.translate(-c)
            p.drawPath(path)
            p.restore()
        else:
            p.drawPath(path)

    @staticmethod
    def _label(p: QPainter, rect: QRectF, text: str, align=Qt.AlignCenter, color: str = C.TEXT_FAINT,
               px: int = 11) -> None:
        f = QFont(p.font())
        f.setPixelSize(px)
        p.setFont(f)
        p.setPen(qcolor(color))
        p.drawText(rect, align, text)


def _rr(x: float, y: float, w: float, h: float, r: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), r, r)
    return path


def _circle(cx: float, cy: float, r: float) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), r, r)
    return path


class PadDrawing(_Drawing):
    """Геймпад DualSense в общих чертах: курки, бамперы, тачпад, крестовина, четыре кнопки, два стика."""

    STICKS = {"lstick": (200.0, 230.0), "rstick": (360.0, 230.0)}

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        e: dict[str, QPainterPath] = {
            "l2": _rr(96, 30, 86, 26, 10), "r2": _rr(378, 30, 86, 26, 10),
            "l1": _rr(100, 62, 80, 14, 6), "r1": _rr(380, 62, 80, 14, 6),
            "touchpad": _rr(206, 92, 148, 78, 12),
            "create": _rr(180, 96, 14, 22, 5), "options": _rr(366, 96, 14, 22, 5),
            "dpad_up": _rr(118, 118, 26, 30, 5), "dpad_down": _rr(118, 178, 26, 30, 5),
            "dpad_left": _rr(86, 150, 30, 26, 5), "dpad_right": _rr(146, 150, 30, 26, 5),
            "triangle": _circle(420, 130, 15), "circle": _circle(452, 162, 15),
            "cross": _circle(420, 194, 15), "square": _circle(388, 162, 15),
        }
        for key, (cx, cy) in self.STICKS.items():
            e[key] = _circle(cx, cy, 34)
        self._elements = e

    def elements(self) -> dict[str, QPainterPath]:
        return self._elements

    @staticmethod
    def owner(key: str) -> str:
        """Нажатие стика (L3/R3) подсвечивает сам стик."""
        return {"l3": "lstick", "r3": "rstick"}.get(key, key)

    def elements_for(self, keys: str) -> set[str]:
        """«l2,r2» → {l2, r2}; «dpad_lr» → обе половинки крестовины."""
        out: set[str] = set()
        for k in keys.split(","):
            out |= {"dpad_lr": {"dpad_left", "dpad_right"}, "dpad_ud": {"dpad_up", "dpad_down"}}.get(
                k, {self.owner(k)})
        return out & set(self._elements)

    def set_highlight(self, key: str | None) -> None:
        self._keys = key or ""
        lit = sorted(self.elements_for(key)) if key else []
        super().set_highlight(lit[0] if lit else None)

    def animation_for(self, key: str) -> str | None:
        first = getattr(self, "_keys", "").split(",")[0]
        return {"lstick": "circle", "rstick": "sway"}.get(first, "press")

    def _lit(self, key: str) -> bool:
        keys = getattr(self, "_keys", "")
        return bool(keys) and key in self.elements_for(keys)

    def paintEvent(self, _e):  # noqa: N802
        p = self._begin()
        body = QPainterPath(QPointF(140, 80))
        body.lineTo(420, 80)
        body.cubicTo(460, 80, 482, 106, 490, 150)
        body.lineTo(516, 280)
        body.cubicTo(522, 314, 492, 334, 468, 314)
        body.lineTo(416, 266)
        body.cubicTo(406, 257, 394, 252, 380, 252)
        body.lineTo(180, 252)
        body.cubicTo(166, 252, 154, 257, 144, 266)
        body.lineTo(92, 314)
        body.cubicTo(68, 334, 38, 314, 44, 280)
        body.lineTo(70, 150)
        body.cubicTo(78, 106, 100, 80, 140, 80)
        p.setPen(QPen(qcolor(C.LINE), 1.5))
        p.setBrush(qcolor(C.SURFACE))
        p.drawPath(body)
        for key, path in self._elements.items():
            if key in self.STICKS:
                continue
            self._part(p, key, path, self._lit(key))
        sym = QPen(qcolor(C.TEXT_DIM), 1.6)
        p.setPen(sym)
        p.setBrush(Qt.NoBrush)
        tri = QPainterPath(QPointF(412, 136))
        tri.lineTo(420, 123)
        tri.lineTo(428, 136)
        tri.closeSubpath()
        p.drawPath(tri)
        p.drawEllipse(QPointF(452, 162), 7, 7)
        p.drawLine(QPointF(414, 188), QPointF(426, 200))
        p.drawLine(QPointF(426, 188), QPointF(414, 200))
        p.drawRect(QRectF(382, 156, 12, 12))
        for key, (cx, cy) in self.STICKS.items():
            lit = self._lit(key)
            self._part(p, key, self._elements[key], lit)
            ox, oy = self._stick_offset(key) if lit else (0.0, 0.0)
            knob = _circle(cx + ox, cy + oy, 20)
            self._part(p, key + "_knob", knob, lit)
        for rect, text, align in (
            (QRectF(96, 12, 86, 14), "L2", Qt.AlignCenter), (QRectF(378, 12, 86, 14), "R2", Qt.AlignCenter),
            (QRectF(40, 62, 54, 14), "L1", Qt.AlignRight), (QRectF(466, 62, 54, 14), "R1", Qt.AlignLeft),
            (QRectF(120, 82, 54, 12), "Create", Qt.AlignRight), (QRectF(386, 82, 60, 12), "Options", Qt.AlignLeft),
            (QRectF(206, 92, 148, 78), "тачпад", Qt.AlignCenter),
            (QRectF(140, 272, 120, 14), "L-стик · L3", Qt.AlignCenter),
            (QRectF(300, 272, 120, 14), "R-стик · R3", Qt.AlignCenter),
        ):
            self._label(p, rect, text, align)
        p.end()

    def _stick_offset(self, key: str) -> tuple[float, float]:
        a = self.phase() * 2 * math.pi
        if self._anim == "circle":
            return 10 * math.cos(a), 10 * math.sin(a)
        if self._anim == "sway":
            return 10 * math.sin(a), 0.0
        if self._anim == "press":
            return 0.0, 0.0
        return 0.0, 0.0


class KeyboardDrawing(_Drawing):
    """Нужные клавиши — светлые, остальные бледные."""

    ROWS: tuple[tuple[tuple[str, str, float], ...], ...] = (
        (("Esc", "Esc", 1), ("F1", "F1", 1), ("F2", "F2", 1), ("…", "", 1), ("F9", "F9", 1)),
        tuple((d, d, 1) for d in "123456") + (("−", "−", 1), ("+", "+", 1)),
        tuple((c, c, 1) for c in "QWERTYU") + (("P", "P", 1),),
        tuple((c, c, 1) for c in "ASDFGHJ") + (("M", "M", 1),),
        (("Пробел", "Space", 6.4), ("←", "Left", 1), ("↑", "Up", 1), ("↓", "Down", 1), ("→", "Right", 1)),
    )
    KEY, GAP = 44.0, 7.0

    def __init__(self, used: set[str] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.used = set(used or ())
        self._elements: dict[str, QPainterPath] = {}
        self._caps: list[tuple[str, str, QRectF]] = []
        y = 40.0
        for row in self.ROWS:
            width = sum(w * self.KEY for _t, _k, w in row) + self.GAP * (len(row) - 1)
            x = (self.VIEW[0] - width) / 2
            for text, key, w in row:
                rect = QRectF(x, y, w * self.KEY, self.KEY)
                self._caps.append((text, key, rect))
                if key:
                    self._elements.setdefault(key, QPainterPath()).addRoundedRect(rect, 7, 7)
                x += rect.width() + self.GAP
            y += self.KEY + self.GAP + 10

    def elements(self) -> dict[str, QPainterPath]:
        return {k: v for k, v in self._elements.items() if k in self.used}

    def set_highlight(self, key: str | None) -> None:
        self._keys = set(key.split(",")) if key else set()
        super().set_highlight(key.split(",")[0] if key else None)

    def paintEvent(self, _e):  # noqa: N802
        p = self._begin()
        lit = getattr(self, "_keys", set())
        for text, key, rect in self._caps:
            used = key in self.used
            p.setOpacity(1.0 if used else 0.35)
            path = QPainterPath()
            path.addRoundedRect(rect, 7, 7)
            self._part(p, key, path, key in lit)
            self._label(p, rect, text, Qt.AlignCenter, C.LIME if key in lit else C.TEXT if used else C.TEXT_DIM, 13)
        p.end()


class MouseDrawing(_Drawing):
    """Кусок видео с человеком: клик выбирает цель (рамка-скобки), клик по другой клетке выбирает камеру."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._elements = {"m_target": _rr(250, 90, 60, 150, 10), "m_cell": _rr(400, 60, 120, 68, 8)}

    def elements(self) -> dict[str, QPainterPath]:
        return self._elements

    def paintEvent(self, _e):  # noqa: N802
        from ptztracker.ui.widgets import paint_brackets  # поздний импорт: widgets тянет иконки

        p = self._begin()
        for rect, sel in ((QRectF(40, 60, 340, 220), True), (QRectF(400, 60, 120, 68), False)):
            p.setPen(QPen(qcolor(C.TEXT if sel else C.LINE), 1.5))
            p.setBrush(qcolor(C.RAISED))
            p.drawRoundedRect(rect, 10, 10)
            floor = QRectF(rect.left() + 1, rect.center().y() + rect.height() * 0.1, rect.width() - 2,
                           rect.height() * 0.4 - 1)
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(C.SURFACE))
            p.drawRoundedRect(floor, 9, 9)
        p.setBrush(qcolor(C.TEXT_DIM))
        p.drawRoundedRect(QRectF(266, 100, 28, 130), 12, 12)
        lit = self.highlighted
        if lit == "m_target":
            paint_brackets(p, QRectF(254, 92, 52, 146), qcolor(C.LIME), 3)
        if lit == "m_cell":
            p.setPen(QPen(qcolor(C.LIME), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(400, 60, 120, 68), 8, 8)
        if lit:
            c = self._elements[lit].boundingRect().center()
            s = 1 - 0.15 * math.sin(self.phase() * math.pi * 2) ** 2
            p.setPen(QPen(qcolor(C.BG), 1.2))
            p.setBrush(qcolor(C.TEXT))
            arrow = QPainterPath(QPointF(c.x(), c.y()))
            for dx, dy in ((0, 22), (6, 16), (11, 26), (15, 24), (10, 14), (18, 14)):
                arrow.lineTo(c.x() + dx * s, c.y() + dy * s)
            arrow.closeSubpath()
            p.drawPath(arrow)
        self._label(p, QRectF(40, 290, 340, 16), "выбранная камера", Qt.AlignCenter)
        self._label(p, QRectF(400, 134, 120, 16), "другая камера", Qt.AlignCenter)
        p.end()
