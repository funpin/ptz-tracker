"""Общие компоненты интерфейса: клавиша-подсказка, плитка состояния, чип, рамка видео."""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ptztracker.ui.icons import icon
from ptztracker.ui.theme import (
    FS_CAPTION,
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


_PILL_COLORS = {
    "ok": C.OK, "warn": C.LOST, "fault": C.FAULT, "off": C.TEXT_FAINT,
    "pgm": C.PGM, "pvw": C.PVW, "accent": C.ACCENT, "neutral": C.TEXT_DIM,
}


class StatusPill(QToolButton):
    """Значок верхней панели: иконка цвета состояния + короткий текст; подробности — в подсказке."""

    def __init__(self, icon_name: str, text: str = "", kind: str = "off"):
        super().__init__()
        self._icon_name = icon_name
        self._full = ""
        self._kind = ""
        self._compact = False
        self.setFocusPolicy(Qt.NoFocus)
        self.setAutoRaise(True)
        self.setProperty("pill", True)
        self.set(text, kind, "")

    def kind(self) -> str:
        return self._kind

    @property
    def full_text(self) -> str:
        return self._full

    def set(self, text: str, kind: str, tooltip: str) -> None:
        if (text, kind, tooltip) == (self._full, self._kind, self.toolTip()):
            return
        self._full = text
        if kind != self._kind:
            self._kind = kind
            self.setIcon(icon(self._icon_name, _PILL_COLORS.get(kind, C.TEXT_DIM)))
        self.setToolTip(tooltip)
        self._apply()

    def set_compact(self, on: bool) -> None:
        if on != self._compact:
            self._compact = on
            self._apply()

    def _apply(self) -> None:
        show = bool(self._full) and not self._compact
        self.setText(self._full if show else "")
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if show else Qt.ToolButtonIconOnly)


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
        # подсказка не раздвигает панель (шрифт Windows шире): при нехватке места обрезается, полный текст — в подсказке
        self.hint_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.hint_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.hint_label.setToolTip(hint)
        lay.addWidget(self.title_label)
        lay.addWidget(self.hint_label, 1)


class TrackButton(QPushButton):
    """TRACK ON/OFF в верхней панели: цвет — состояние слежения, пояснение — во всплывающей подсказке.
    «Калибровка…» (action_btn) — отдельная кнопка рядом, видна только при раскачке."""

    actionClicked = Signal()
    WORDS = {"on": "TRACK ON", "lost": "TRACK LOST"}

    def __init__(self):
        super().__init__("TRACK OFF")
        self.setCheckable(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self._kind = ""
        self._why = ""
        self.action_btn = KeyButton("Калибровка…")
        self.action_btn.setFocusPolicy(Qt.NoFocus)
        self.action_btn.clicked.connect(self.actionClicked)
        self.action_btn.hide()
        self.set_view("off", "", "", False)

    def kind(self) -> str:
        return self._kind

    def set_view(self, kind: str, word: str, why: str, show_action: bool) -> None:
        self.setText(self.WORDS.get(kind, "TRACK OFF"))
        if kind != self._kind:
            self._kind = kind
            color = STATE_COLOR.get(kind, C.TEXT_FAINT)
            self.setIcon(icon("track", color))
            on = kind in ("on", "lost")
            self.setStyleSheet(
                f"QPushButton {{ border: 2px solid {color}; border-radius: {R_PANEL}px; padding: 4px 12px;"
                f" font-weight: 700; color: {C.BG if on else C.TEXT}; background: {color if on else C.RAISED}; }}"
            )
        self.action_btn.setVisible(show_action)
        self.set_why(why)

    def set_why(self, text: str) -> None:
        self._why = text
        self.setToolTip(f"{text}\nПробел — включить или выключить слежение".strip())


class PowerOverlay(QWidget):
    """Поверх видео, когда камера найдена, но в режиме ожидания: круглая кнопка питания и подпись."""

    powerOnRequested = Signal()
    BTN = 88

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_StyledBackground, True)  # иначе фон из QSS у QWidget не рисуется
        self.setStyleSheet(f"PowerOverlay {{ background: {C.BG}; }}")
        self.power_btn = QPushButton()
        self.power_btn.setFixedSize(self.BTN, self.BTN)
        self.power_btn.setIcon(icon("power", C.OK, 40))
        self.power_btn.setIconSize(self.power_btn.size() * 0.45)
        self.power_btn.setFocusPolicy(Qt.NoFocus)
        self.power_btn.setCursor(Qt.PointingHandCursor)
        self.power_btn.setToolTip("Включить камеру")
        inner = self.BTN - 6  # в QSS min/max — без рамки 3 px
        self.power_btn.setStyleSheet(
            f"QPushButton {{ border: 3px solid {C.OK}; border-radius: {self.BTN // 2}px; background: {C.RAISED};"
            f" min-width: {inner}px; max-width: {inner}px; min-height: {inner}px; max-height: {inner}px;"
            " padding: 0; }"
            f"QPushButton:hover {{ background: {C.RAISED_HI}; }}"
            f"QPushButton:disabled {{ border-color: {C.TEXT_FAINT}; }}"
        )
        self.power_btn.clicked.connect(self.powerOnRequested)
        self.title_label = QLabel()
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(f"color: {C.TEXT}; font-size: 18px;")
        self.hint_label = QLabel()
        self.hint_label.setAlignment(Qt.AlignCenter)
        self.hint_label.setStyleSheet(f"color: {C.TEXT_DIM};")
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        lay.addWidget(self.power_btn, 0, Qt.AlignHCenter)
        lay.addSpacing(SP[2])
        lay.addWidget(self.title_label)
        lay.addWidget(self.hint_label)
        lay.addStretch(1)
        self.hide()

    def set_view(self, view: str, camera_name: str, can_power: bool = True) -> None:
        """view: standby | switching | иное — скрыть. can_power=False: камера не знает команду питания —
        кнопка серая с пояснением (способ выйти из ожидания остаётся на виду)."""
        if view == "standby":
            self.title_label.setText(f"Камера {camera_name} найдена, но выключена (режим ожидания)")
            self.hint_label.setText("Нажмите, чтобы включить" if can_power
                                    else "Эта камера не принимает команду питания — включите её кнопкой на корпусе")
            self.power_btn.setEnabled(can_power)
            self.power_btn.setToolTip("Включить камеру" if can_power
                                      else "Камера не принимает команду питания от программы")
        elif view == "switching":
            self.title_label.setText(f"Камера {camera_name} включается…")
            self.hint_label.setText("Видео появится через несколько секунд")
            self.power_btn.setEnabled(False)
        self.setVisible(view in ("standby", "switching"))
        if self.isVisible():
            self.raise_()


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
        self._overlays: list[tuple[QWidget, str]] = []

    def add_overlay(self, w: QWidget, corner: str) -> None:
        """Виджет поверх видео: «top-left» — под ярлыком слежения, «top-center» — сверху по центру, «bottom-left» — внизу слева."""
        w.setParent(self)
        self._overlays.append((w, corner))
        self._place_tags()

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
        for w, corner in self._overlays:
            w.adjustSize()
            w.raise_()
            if corner == "fill":
                w.setGeometry(self.rect().adjusted(off, off, -off, -off))
                continue
            if corner == "center":
                w.move(max(off, (self.width() - w.width()) // 2), max(off, (self.height() - w.height()) // 2))
            elif corner == "top-center":
                w.move(max(off, (self.width() - w.width()) // 2), off + SP[1])
            elif corner == "top-left":
                w.move(off + SP[1], off + self.tracking_tag.height() + SP[1])
            else:
                w.move(off + SP[1], self.height() - w.height() - off - SP[1])

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
