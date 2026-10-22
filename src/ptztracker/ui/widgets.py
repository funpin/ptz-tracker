"""Общие компоненты интерфейса: клавиша-подсказка, плитка состояния, чип, рамка видео."""

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ptztracker.ui.theme import (
    FS_CAPTION,
    FS_STATE,
    H_STATE,
    R_PANEL,
    SP,
    C,
    qcolor,
)

KEYCAP_FS = 11  # px
KEYCAP_H = 18
KEYCAP_PAD = 6

# Цвет по виду состояния слежения (см. таблицу состояний).
STATE_COLOR = {
    "off": C.TEXT_FAINT,
    "on": C.ON,
    "lost": C.LOST,
    "remote": C.REMOTE,
    "swing": C.FAULT,
}


def _keycap_font() -> QFont:
    f = QFont(QApplication.font().family())
    f.setPixelSize(KEYCAP_FS)
    f.setBold(True)
    return f


def keycap_width(text: str) -> float:
    return QFontMetrics(_keycap_font()).horizontalAdvance(text) + 2 * KEYCAP_PAD + 2


def paint_keycap(p: QPainter, right: float, cy: float, text: str) -> float:
    """Рисует «клавишу» правым краем в right; возвращает её ширину."""
    w = keycap_width(text)
    rect = QRectF(right - w, cy - KEYCAP_H / 2, w, KEYCAP_H)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(qcolor(C.LINE), 1))
    p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
    p.setPen(QPen(qcolor(C.LINE), 2))  # нижняя граница толще
    p.drawLine(rect.left() + 3, rect.bottom() - 0.5, rect.right() - 3, rect.bottom() - 0.5)
    p.setFont(_keycap_font())
    p.setPen(qcolor(C.TEXT_DIM))
    p.drawText(rect.adjusted(0, -1, 0, -1), Qt.AlignCenter, text)
    p.restore()
    return w


class KeyButton(QPushButton):
    """Кнопка с «клавишей» справа."""

    def __init__(self, text: str, hotkey: str | None = None, variant: str | None = None):
        super().__init__(text)
        self._hotkey = hotkey
        if variant:
            self.setProperty("variant", variant)

    @property
    def hotkey(self) -> str | None:
        return self._hotkey

    def sizeHint(self):
        s = super().sizeHint()
        if self._hotkey:
            s.setWidth(s.width() + int(keycap_width(self._hotkey)) + SP[1])
        return s

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._hotkey:
            p = QPainter(self)
            paint_keycap(p, self.width() - SP[1], self.height() / 2, self._hotkey)
            p.end()


CHIP_KINDS = ("neutral", "ok", "accent", "warn", "fault", "pgm", "pvw")
_LAMP_SIZE = 8


class Chip(QLabel):
    """Метка-чип с точкой-лампой слева."""

    def __init__(self, text: str = "", kind: str = "neutral"):
        super().__init__(text)
        self.setContentsMargins(_LAMP_SIZE + SP[0], 0, 0, 0)
        self._kind = ""
        self.set(text, kind)

    def kind(self) -> str:
        return self._kind

    def set(self, text: str, kind: str) -> None:
        self.setText(text)
        self._kind = kind
        self.setProperty("kind", kind)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _lamp_color(self) -> str:
        return {
            "neutral": C.TEXT_FAINT,
            "ok": C.PVW,
            "accent": C.ACCENT,
            "warn": C.LOST,
            "fault": C.FAULT,
            "pgm": C.TEXT,
            "pvw": C.PVW,
        }.get(self._kind, C.TEXT_FAINT)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(self._lamp_color()))
        x = SP[1] + 1
        p.drawEllipse(QRectF(x, (self.height() - _LAMP_SIZE) / 2, _LAMP_SIZE, _LAMP_SIZE))
        p.end()


class SectionHeader(QWidget):
    """Заголовок раздела слева, подсказка справа."""

    def __init__(self, title: str, hint: str = ""):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(title)
        self.title_label.setProperty("variant", "section")
        self.hint_label = QLabel(hint)
        self.hint_label.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
        lay.addWidget(self.title_label)
        lay.addStretch(1)
        lay.addWidget(self.hint_label)


class _Lamp(QWidget):
    SIZE = 16

    def __init__(self):
        super().__init__()
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.color = C.TEXT_FAINT
        self.hollow = True
        self.lit = True

    def paintEvent(self, e):
        if not self.lit:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(2, 2, self.SIZE - 4, self.SIZE - 4)
        if self.hollow:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(qcolor(self.color), 2))
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(self.color))
            p.drawEllipse(r)
        p.end()


class _ElideLabel(QLabel):
    """Перенос слов, максимум 2 строки; конец 2-й строки обрезается многоточием."""

    LINES = 2

    def __init__(self):
        super().__init__()
        self._full = ""
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setFixedHeight(self.LINES * QFontMetrics(self.font()).lineSpacing() + 2)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)

    def set_full(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._refit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refit()

    def _refit(self) -> None:
        fm = QFontMetrics(self.font())
        w = max(self.width(), 120)
        words = self._full.split()
        lines, cur, i = [], "", 0
        while i < len(words) and len(lines) < self.LINES - 1:
            cand = f"{cur} {words[i]}".strip()
            if fm.horizontalAdvance(cand) <= w or not cur:
                cur = cand
                i += 1
            else:
                lines.append(cur)
                cur = ""
        rest = " ".join(([cur] if cur else []) + words[i:])
        lines.append(fm.elidedText(rest, Qt.ElideRight, w))
        self.setText("\n".join(lines))


class StateTile(QPushButton):
    """Плитка состояния слежения: высота постоянна, цвет — по виду состояния."""

    actionClicked = Signal()

    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedHeight(H_STATE)
        self.setCursor(Qt.PointingHandCursor)
        self._kind = "off"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP[2], SP[1], SP[2], SP[1])
        lay.setSpacing(SP[0])

        self.caption_label = QLabel("Слежение")
        self.caption_label.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
        self.caption_label.setFixedHeight(KEYCAP_H)
        lay.addWidget(self.caption_label)

        row = QHBoxLayout()
        row.setSpacing(SP[1])
        self._lamp = _Lamp()
        self.word_label = QLabel()
        row.addWidget(self._lamp)
        row.addWidget(self.word_label, 1)
        lay.addLayout(row)

        why_row = QHBoxLayout()
        why_row.setSpacing(SP[1])
        self.why_label = _ElideLabel()
        self.why_label.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
        self.action_btn = KeyButton("Калибровка…")
        self.action_btn.setFocusPolicy(Qt.NoFocus)
        self.action_btn.setFixedHeight(28)
        self.action_btn.setStyleSheet(
            f"QPushButton {{ min-height: 0; padding: 0 8px; font-size: {FS_CAPTION + 1}px; }}"
        )
        self.action_btn.hide()
        why_row.addWidget(self.why_label, 1)
        why_row.addWidget(self.action_btn, 0, Qt.AlignBottom)
        lay.addLayout(why_row)

        for w in (self.caption_label, self.word_label, self._lamp, self.why_label):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.action_btn.clicked.connect(self.actionClicked)

        self._blink = QTimer(self)
        self._blink.setInterval(500)  # 1 Гц: полпериода горит, полпериода нет
        self._blink.timeout.connect(self._toggle_lamp)
        self.set_view("off", "", "", False)

    def kind(self) -> str:
        return self._kind

    def set_view(self, kind: str, word: str, why: str, show_action: bool) -> None:
        self._kind = kind
        color = STATE_COLOR.get(kind, C.TEXT_FAINT)
        self.word_label.setText(word)
        self.word_label.setStyleSheet(
            f"color: {C.TEXT_FAINT if kind == 'off' else color};"
            f" font-size: {FS_STATE}px; font-weight: 800;"
        )
        self.why_label.set_full(why)
        self.setToolTip(why)
        self.action_btn.setVisible(show_action and kind == "swing")
        self._lamp.color = color
        self._lamp.hollow = kind == "off"
        self._lamp.lit = True
        if kind == "lost":
            if not self._blink.isActive():
                self._blink.start()
        else:
            self._blink.stop()
        self._lamp.update()
        self.update()

    def set_why(self, text: str) -> None:
        """Заменить только пояснение (с тем же усечением и подсказкой)."""
        self.why_label.set_full(text)
        self.setToolTip(text)

    def _toggle_lamp(self) -> None:
        self._lamp.lit = not self._lamp.lit
        self._lamp.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = STATE_COLOR.get(self._kind, C.TEXT_FAINT)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(qcolor(C.RAISED))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(r, R_PANEL, R_PANEL)
        if self._kind == "off":
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(qcolor(C.LINE), 1))
            p.drawRoundedRect(r, R_PANEL, R_PANEL)
        else:
            p.setBrush(qcolor(color, int(255 * 0.16)))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(r, R_PANEL, R_PANEL)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(qcolor(color), 2))
            p.drawRoundedRect(r, R_PANEL, R_PANEL)
        if self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(C.TEXT, 14))
            p.drawRoundedRect(r, R_PANEL, R_PANEL)
        cy = SP[1] + KEYCAP_H / 2
        paint_keycap(p, self.width() - SP[2], cy, "Пробел")
        p.end()


class VideoFrame(QFrame):
    """Рамка вокруг видео: цвет — Tally; угловые ярлыки поверх картинки."""

    BORDER = 4

    def __init__(self, inner: QWidget):
        super().__init__()
        self._tally: str | None = None
        lay = QVBoxLayout(self)
        b = self.BORDER
        lay.setContentsMargins(b, b, b, b)
        lay.addWidget(inner)
        inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.tracking_tag = self._make_tag()
        self.onair_tag = self._make_tag()
        self.tracking_tag.hide()
        self.onair_tag.hide()

    def _make_tag(self) -> QLabel:
        t = QLabel(self)
        t.setAttribute(Qt.WA_TransparentForMouseEvents)
        return t

    @staticmethod
    def _tag_style(bg: str, fg: str, corner: str) -> str:
        return (
            f"background: {bg}; color: {fg}; border: none; border-{corner}-radius: 8px;"
            " padding: 4px 12px;"
            f" font-size: {FS_CAPTION}px; font-weight: 700;"
        )

    def tally(self) -> str | None:
        return self._tally

    def set_tally(self, state: str | None) -> None:
        self._tally = state if state in ("pgm", "pvw") else None
        if self._tally == "pgm":
            self.onair_tag.setStyleSheet(self._tag_style(C.PGM, C.TEXT, 'bottom-left'))
            self.onair_tag.setText("ЭФИР · PGM")
        elif self._tally == "pvw":
            self.onair_tag.setStyleSheet(self._tag_style(C.PVW, C.TEXT, 'bottom-left'))
            self.onair_tag.setText("ПРЕВЬЮ · PVW")
        self.onair_tag.setVisible(self._tally is not None)
        self._place_tags()
        self.update()

    def set_tracking_tag(self, text: str, color: str) -> None:
        if color == C.TEXT_FAINT:  # «выкл»: нейтральный ярлык
            self.tracking_tag.setStyleSheet(self._tag_style(C.RAISED_HI, C.TEXT, 'bottom-right'))
        else:
            self.tracking_tag.setStyleSheet(self._tag_style(color, C.BG, 'bottom-right'))
        self.tracking_tag.setText(text)
        self.tracking_tag.show()
        self._place_tags()

    def _place_tags(self) -> None:
        off = self.BORDER
        for t in (self.tracking_tag, self.onair_tag):
            t.adjustSize()
            t.raise_()
        self.tracking_tag.move(off, off)
        self.onair_tag.move(self.width() - self.onair_tag.width() - off, off)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_tags()

    def paintEvent(self, e):
        super().paintEvent(e)
        color = {"pgm": C.PGM, "pvw": C.PVW}.get(self._tally or "", C.LINE)
        p = QPainter(self)
        p.setPen(QPen(qcolor(color), self.BORDER))
        p.setBrush(Qt.NoBrush)
        h = self.BORDER / 2
        p.drawRect(QRectF(self.rect()).adjusted(h, h, -h, -h))
        p.end()
