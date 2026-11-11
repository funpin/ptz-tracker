"""Подсказки управления поверх видео (макет docs/design/mockups/control-hints.html): одна плашка,
короткое состояние и не больше трёх пар «кнопка → что сделает». Кнопки рисуются: клавиша — клавишей,
мышь — мышкой с нужной кнопкой, геймпад — той же формой, что в справке F1."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from ptztracker.ui.help_drawings import PadGlyphs
from ptztracker.ui.theme import FS_CAPTION, R_TAG, SP, C, _rgba, qcolor

Pair = tuple[str, str, str]  # (key | mouse | pad, кнопка, что сделает)

ZONE_PAIRS: list[Pair] = [("mouse", "L", "тяните — переместить"), ("mouse", "L", "за скобку — размер"),
                          ("mouse", "LL", "сброс")]


def hint_for(kind: str, why: str, has_target: bool, target_id: int | None = None, pad: bool = False,
             zone_hover: bool = False, zone_text: str = "") -> tuple[str, list[Pair]]:
    """(состояние словами, пары) для плашки внизу видео. pad — камерой управляет геймпад: его кнопки.
    zone_text — зону тянут: её размер и что она значит (control.zones.zone_hint); кнопки тогда не показываем."""
    if zone_hover:
        return (zone_text, []) if zone_text else ("", list(ZONE_PAIRS))
    who = f" №{target_id}" if target_id is not None else ""
    if kind == "on":
        pairs = ([("pad", "rstick", "нажать — выключить"), ("pad", "touchpad", "стоп")] if pad else
                 [("mouse", "L", "тяните зону — где лицо"), ("key", "Esc", "стоп")])
        return f"Веду{who}", pairs
    if kind == "lost":
        return "Цель потеряна — жду", ([("pad", "touchpad", "стоп")] if pad else [("key", "Esc", "стоп")])
    if kind == "remote":
        return "Пульт: слежение на паузе", ([("pad", "rstick", "нажать — продолжить")] if pad else [("key", "Пробел", "продолжить")])
    if kind == "swing":
        return "Раскачка: слежение остановлено", []
    # выключено: причина — словами; «Цель не выбрана» уже написано сверху слева, здесь только что делать
    title = why if why.startswith("Слежение выключено") else ""
    if not has_target:
        pairs = ([("pad", "rstick", "← → выбрать"), ("pad", "rstick", "нажать — вести")] if pad else
                 [("mouse", "L", "по человеку — выбрать"), ("key", "Tab", "мышью")])
    else:
        pairs = ([("pad", "rstick", "нажать — вести"), ("pad", "touchpad", "стоп")] if pad else
                 [("key", "Пробел", "вести"), ("mouse", "L", "другой человек"), ("key", "Tab", "мышью")])
    return title, pairs


class MouseGlyph(QWidget):
    """Мышь 16×20: подсвечена левая (L), правая (R) кнопка, колесо (W); LL — двойной клик (×2 рядом)."""

    def __init__(self, button: str, parent=None):
        super().__init__(parent)
        self.button = button
        self.setFixedSize(QSize(26 if button == "LL" else 16, 20))

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        body = QRectF(1, 1, 14, 18)
        p.setPen(QPen(qcolor(C.GREY), 1.3))
        p.setBrush(qcolor(C.RAISED))
        p.drawRoundedRect(body, 7, 7)
        p.drawLine(QPointF(8, 1.5), QPointF(8, 8))
        lime = qcolor(C.LIME)
        p.setPen(Qt.NoPen)
        if self.button in ("L", "LL", "R"):
            half = QPainterPath(QPointF(8, 8))
            half.lineTo(8, 1.7)
            span = 90 if self.button == "R" else -90  # дуга верхней четверти: правая или левая кнопка
            half.arcTo(QRectF(1.7, 1.7, 12.6, 12.6), 90, span)
            half.closeSubpath()
            p.setBrush(lime)
            p.drawPath(half)
        p.setBrush(lime if self.button == "W" else qcolor(C.GREY))
        p.drawRoundedRect(QRectF(6.8, 3.2, 2.4, 4.2 if self.button == "W" else 3.4), 1.2, 1.2)
        if self.button == "LL":
            p.setPen(qcolor(C.TEXT_DIM))
            f = p.font()
            f.setPixelSize(10)
            p.setFont(f)
            p.drawText(QRectF(15, 0, 11, 20), Qt.AlignCenter, "×2")
        p.end()


class DeviceIcon(QWidget):
    """Чем управляли последним: геймпад или клавиатура — серый контур 18×12 в начале плашки."""

    def __init__(self, device: str, parent=None):
        super().__init__(parent)
        self.device = device
        self.setFixedSize(QSize(18, 14))
        self.setToolTip("Подсказка для геймпада" if device == "pad" else "Подсказка для клавиатуры и мыши")

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(qcolor(C.TEXT_FAINT), 1.2))
        p.setBrush(Qt.NoBrush)
        if self.device == "pad":
            body = QPainterPath()
            body.addRoundedRect(QRectF(1, 2, 16, 8), 4, 4)
            body.addEllipse(QRectF(1.5, 5, 5, 7.5))  # рукоятки
            body.addEllipse(QRectF(11.5, 5, 5, 7.5))
            p.drawPath(body.simplified())
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(C.TEXT_FAINT))
            p.drawEllipse(QPointF(6, 6), 1.3, 1.3)
            p.drawEllipse(QPointF(12, 6), 1.3, 1.3)
        else:
            p.drawRoundedRect(QRectF(1, 2.5, 16, 10), 2.5, 2.5)
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(C.TEXT_FAINT))
            for row, y in enumerate((5, 7.6)):
                for i in range(5 - row):
                    p.drawRect(QRectF(3.2 + i * 2.6 + row * 1.3, y, 1.4, 1.3))
            p.drawRect(QRectF(5.5, 10.1, 7, 1.2))  # пробел
        p.end()


def _device_of(pairs: list[Pair]) -> str | None:
    if not pairs:
        return None
    return "pad" if any(k == "pad" for k, _c, _t in pairs) else "kb"


def _glyph(kind: str, code: str) -> QWidget:
    if kind == "mouse":
        return MouseGlyph(code)
    if kind == "pad":
        g = PadGlyphs([code])
        g.setStyleSheet("background: transparent;")
        return g
    k = QLabel(code)  # клавиша-подсказка (BRANDBOOK §6)
    k.setAlignment(Qt.AlignCenter)
    k.setStyleSheet(f"background: {C.RAISED}; border: 1px solid {C.LINE}; border-radius: {R_TAG}px;"
                    f" color: {C.TEXT}; font-size: 10.5px; font-weight: 600; padding: 1px 6px;")
    return k


class HintBar(QFrame):
    """Плашка подсказки поверх видео: графит 85 %, скругление 7 (BRANDBOOK §6)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("hintBar")
        self.setStyleSheet(f"QFrame#hintBar {{ background: {_rgba(C.BG, 0.85)}; border-radius: 7px; }}")
        self.title = ""
        self.pairs: list[Pair] = []
        self.device: str | None = None  # "pad" | "kb" — значок слева; None — простое сообщение
        self.muted = False  # на этом месте другая плашка (режим мыши): текст обновляется, но не показывается
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(SP[1] + 1, 4, SP[1] + 2, 4)
        self._lay.setSpacing(6)

    def text(self) -> str:
        parts = [self.title] if self.title else []
        for kind, code, what in self.pairs:
            parts.append(f"{code} {what}" if kind == "key" else what)
        return " · ".join(parts)

    def setText(self, text: str) -> None:  # noqa: N802
        """Простое сообщение без кнопок (отказ слежения и т. п.)."""
        self.set_hint(text, [])

    def set_hint(self, title: str, pairs: list[Pair]) -> None:
        if (title, pairs) == (self.title, self.pairs):
            self.setVisible(bool(title or pairs) and not self.muted)  # могли спрятать при смене камеры
            return
        self.title, self.pairs = title, list(pairs)
        self.device = _device_of(self.pairs)
        while self._lay.count():
            w = self._lay.takeAt(0).widget()
            if w is not None:
                w.hide()  # удаление отложено до цикла событий: без этого старый текст виден под новым (зона под мышью)
                w.deleteLater()
        if self.device:
            self._lay.addWidget(DeviceIcon(self.device))
        if title:
            t = QLabel(title)
            t.setStyleSheet(f"color: {C.TEXT}; font-weight: 600; font-size: {FS_CAPTION}px; background: transparent;")
            self._lay.addWidget(t)
            if pairs:
                sep = QFrame()
                sep.setFixedSize(1, 14)
                sep.setStyleSheet(f"background: {C.LINE};")
                self._lay.addWidget(sep)
        for i, (kind, code, what) in enumerate(pairs):
            if i:
                self._lay.addSpacing(6)
            self._lay.addWidget(_glyph(kind, code))
            w = QLabel(what)
            w.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px; background: transparent;")
            self._lay.addWidget(w)
        for i in range(self._lay.count()):  # новые дети видимой плашки сами не показываются
            if (w := self._lay.itemAt(i).widget()) is not None:
                w.show()
        self.setVisible(bool(title or pairs) and not self.muted)
        self._lay.invalidate()
        self._lay.activate()  # раскладка — сразу: иначе новые подписи остаются нулевого размера до следующего показа
        self.adjustSize()

    def set_muted(self, on: bool) -> None:
        self.muted = on
        self.setVisible(bool(self.title or self.pairs) and not on)
