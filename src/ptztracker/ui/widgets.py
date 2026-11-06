"""Общие компоненты интерфейса: клавиша-подсказка, плитка состояния, чип, рамка видео."""

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
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
    R_BUTTON,
    R_TAG,
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
    """TRACK в верхней панели — про выбранную камеру: цвет и слово — её состояние, пояснение — в подсказке.
    Справа внутри — квадратики камер с анализом (set_cams); клик по квадратику — camToggled(номер),
    сама кнопка при этом не переключается. «Калибровка…» (action_btn) — рядом, только при раскачке."""

    actionClicked = Signal()
    camToggled = Signal(int)
    WORDS = {"on": "TRACK ON", "lost": "ИЩУ ЦЕЛЬ", "none": "TRACK —"}
    CAM = 22  # сторона квадратика
    CAM_GAP = 3

    def __init__(self):
        super().__init__("TRACK OFF")
        self.setCheckable(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self._kind = ""
        self._why = ""
        self._cams: list[tuple[int, str, bool]] = []
        self.action_btn = KeyButton("Калибровка…")
        self.action_btn.setFocusPolicy(Qt.NoFocus)
        self.action_btn.clicked.connect(self.actionClicked)
        self.action_btn.hide()
        self.set_view("off", "", "", False)

    def kind(self) -> str:
        return self._kind

    def _filled(self) -> bool:
        return self._kind in ("on", "lost")

    def _fg(self) -> str:
        return C.BG if self._filled() else C.TEXT_FAINT if self._kind == "none" else C.TEXT

    def _cams_width(self) -> int:
        n = len(self._cams)
        return n * self.CAM + (n - 1) * self.CAM_GAP + 2 * SP[0] if n else 0

    def _restyle(self) -> None:
        bg = {"on": C.LIME, "lost": C.LOST}.get(self._kind, C.RAISED)
        border = {"on": C.LIME, "lost": C.LOST, "swing": C.FAULT}.get(self._kind, C.LINE)
        pad_r = SP[2] + (self._cams_width() + SP[1] if self._cams else 0)
        self.setStyleSheet(
            f"QPushButton {{ border: 1px solid {border}; border-radius: {R_BUTTON + 1}px;"
            f" padding: 0 {pad_r}px 0 {SP[2] + 2}px; min-height: 36px; font-weight: 700;"
            f" color: {self._fg()}; background: {bg}; }}"
            + ("" if self._filled() else f"QPushButton:hover {{ background: {C.RAISED_HI}; }}")
        )
        self.setIcon(icon("track", self._fg()))
        self.updateGeometry()

    def set_view(self, kind: str, word: str, why: str, show_action: bool) -> None:
        self.setText(self.WORDS.get(kind, "TRACK OFF"))
        if kind != self._kind:
            self._kind = kind
            self._restyle()
        self.action_btn.setVisible(show_action)
        self.set_why(why)

    def set_cams(self, cams: list[tuple[int, str, bool]]) -> None:
        """(номер камеры 1–4, вид on|lost|off, выбрана) — только камеры с анализом."""
        cams = list(cams)
        if cams != self._cams:
            self._cams = cams
            self._restyle()
            self.update()

    def cam_rect(self, number: int) -> QRectF:
        nums = [n for n, _k, _s in self._cams]
        i = nums.index(number)
        x0 = self.width() - SP[1] - self._cams_width() + SP[0]
        return QRectF(x0 + i * (self.CAM + self.CAM_GAP), (self.height() - self.CAM) / 2, self.CAM, self.CAM)

    def _cam_at(self, pos) -> int | None:
        for n, _k, _s in self._cams:
            if self.cam_rect(n).contains(QPointF(pos)):
                return n
        return None

    def mousePressEvent(self, e):  # noqa: N802
        n = self._cam_at(e.position()) if e.button() == Qt.LeftButton else None
        if n is not None:
            self._pressed_cam = n
            e.accept()
            return
        self._pressed_cam = None
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):  # noqa: N802
        n = getattr(self, "_pressed_cam", None)
        if n is not None:
            self._pressed_cam = None
            if self._cam_at(e.position()) == n:
                self.camToggled.emit(n)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):  # noqa: N802
        super().paintEvent(e)
        if not self._cams:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fg = qcolor(self._fg())
        first = self.cam_rect(self._cams[0][0])
        tray = QRectF(first.left() - SP[0] + 1, first.top() - 3, self._cams_width() - 2, self.CAM + 6)
        tray_bg = qcolor(C.BG, 56) if self._filled() else qcolor(C.BG)
        p.setPen(Qt.NoPen)
        p.setBrush(tray_bg)
        p.drawRoundedRect(tray, 7, 7)
        f = QFont(self.font())
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        for n, kind, selected in self._cams:
            r = self.cam_rect(n)
            if kind in ("on", "lost"):
                p.setPen(Qt.NoPen)
                p.setBrush(qcolor(C.BG))
                p.drawRoundedRect(r, R_TAG, R_TAG)
                p.setPen(qcolor(C.LIME if kind == "on" else C.LOST))
            else:
                p.setPen(QPen(fg, 1.5))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), R_TAG, R_TAG)
                p.setPen(fg)
            p.drawText(r, Qt.AlignCenter, str(n))
            if selected:
                p.setPen(QPen(fg, 2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(-2, -2, 2, 2), R_TAG + 2, R_TAG + 2)
        p.end()

    def set_why(self, text: str) -> None:
        self._why = text
        tip = f"{text}\nПробел — включить или выключить слежение".strip()
        if self._cams:
            tip += "\nКвадратики — камеры с анализом: клик включает или выключает слежение этой камеры"
        self.setToolTip(tip)


def paint_brackets(p: QPainter, rect: QRectF, color: QColor, width: float,
                   corners: tuple[str, ...] = ("tl", "br"), arm: float | None = None) -> None:
    """Скруглённые углы-скобки. По умолчанию — как в знаке: левый верхний и правый нижний (зона слежения);
    рамка цели «ВЕДУ» — зеркально, правый верхний и левый нижний, чтобы не сливаться с зоной."""
    if arm is None:
        arm = max(8.0, min(rect.width(), rect.height()) * 0.3)
    r = min(5.0, arm / 3)
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    p.save()
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    for c in corners:
        x = rect.left() if c[1] == "l" else rect.right()
        y = rect.top() if c[0] == "t" else rect.bottom()
        sx = 1 if c[1] == "l" else -1  # плечо уходит внутрь рамки
        sy = 1 if c[0] == "t" else -1
        path = QPainterPath(QPointF(x, y + sy * arm))
        path.lineTo(x, y + sy * r)
        path.quadTo(x, y, x + sx * r, y)
        path.lineTo(x + sx * arm, y)
        p.drawPath(path)
    p.restore()


class BrandMark(QWidget):
    """Знак PTZ Tracker: две встречные скобки и лаймовая точка (docs/BRANDBOOK.md §2)."""

    def __init__(self, size: int = 24, parent: QWidget | None = None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)

    def sizeHint(self) -> QSize:
        return QSize(self._size, self._size)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        k = self._size / 120
        pen = QPen(qcolor(C.TEXT), (11 if self._size <= 24 else 9) * k)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        a = QPainterPath(QPointF(31 * k, 58 * k))
        a.lineTo(31 * k, 42 * k)
        a.quadTo(31 * k, 31 * k, 42 * k, 31 * k)
        a.lineTo(58 * k, 31 * k)
        b = QPainterPath(QPointF(62 * k, 89 * k))
        b.lineTo(78 * k, 89 * k)
        b.quadTo(89 * k, 89 * k, 89 * k, 78 * k)
        b.lineTo(89 * k, 62 * k)
        p.drawPath(a)
        p.drawPath(b)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(C.LIME))
        rad = (11 if self._size <= 24 else 10) * k
        p.drawEllipse(QPointF(60 * k, 60 * k), rad, rad)
        p.end()


def battery_time_text(seconds: float | None) -> str:
    """Сколько ещё проработает геймпад, коротко для верхней строки: 6h, 1.5h, 30m."""
    if seconds is None:
        return "—"
    mins = max(1, round(seconds / 60))
    if mins < 60:
        return f"{mins}m"
    hours = mins / 60
    return f"{round(hours)}h" if hours >= 2 else f"{round(hours, 1):g}h"


def battery_level(pct: int | None) -> tuple[str, float] | None:
    """Заряд словом и долей заливки батарейки. SDL отдаёт заряд грубо (5/20/70/100 %), поэтому процентов
    не показываем — только уровень."""
    if pct is None:
        return None
    if pct >= 90:
        return "полный", 1.0
    if pct >= 50:
        return "средний", 0.66
    if pct >= 15:
        return "мало", 0.33
    return "почти пусто", 0.12


class PadBadge(QWidget):
    """Геймпад: значок, номер (как огоньки на геймпаде) и батарейка с уровнем заряда (без цифр: SDL даёт
    заряд грубо). Один виджет и для верхней строки, и для шапки клетки, и для «Сводки»."""

    W, H = 72, 24
    BAT_W, BAT_H = 22, 11  # батарейка — как значки рядом, а не крупнее них (пользователь 2026-11-06)
    BAR_W, BAR_GAP, BAR_SEP = 2.5, 1.5, 9  # ступень L1/R1 — столбиками, как уровень сигнала (макет pad-gear.html)
    FLASH_MS = 900

    def __init__(self, framed: bool = True, parent: QWidget | None = None):
        super().__init__(parent)
        self._framed = framed
        self._n = 0
        self._pct: int | None = None
        self._charging = False
        self._time: str | None = None
        self._gear: int | None = None
        self._gears = 0
        self._flash = QTimer(self)
        self._flash.setSingleShot(True)
        self._flash.timeout.connect(self.update)
        self.setFixedSize(self.W, self.H)

    @classmethod
    def gear_width(cls, gears: int) -> int:
        return round(cls.BAR_SEP + gears * (cls.BAR_W + cls.BAR_GAP) + 2)

    def sizeHint(self) -> QSize:
        return QSize(self.width(), self.H)

    def set(self, number: int, percent: int | None, charging: bool, time_text: str | None = None,
            gear: int | None = None, gears: int = 0) -> None:
        """time_text — вместо процента время до разрядки (верхняя строка); при зарядке — всё равно процент.
        gear — ступень предела скорости 1…gears (L1/R1 в режиме «предел»); None — столбиков нет."""
        if gear is not None and self._gear is not None and gear != self._gear and number == self._n:
            self._flash.start(self.FLASH_MS)
        if (number, percent, charging, time_text, gear, gears) != (
                self._n, self._pct, self._charging, self._time, self._gear, self._gears):
            self._n, self._pct, self._charging, self._time = number, percent, charging, time_text
            self._gear, self._gears = gear, gears if gear is not None else 0
            self.setFixedSize(self.W + (self.gear_width(self._gears) if gear is not None else 0), self.H)
            self.update()

    def gear_text(self) -> str:
        return f"скорость {self._gear} из {self._gears}" if self._gear is not None else ""

    def flashing(self) -> bool:
        return self._flash.isActive()

    def battery_text(self) -> str:
        """Уровень словом (для подсказки и тестов); на самой батарейке — только заливка."""
        if self._charging:
            return "заряжается"
        lvl = battery_level(self._pct)
        return "—" if lvl is None else lvl[0]

    @staticmethod
    def fill_color(pct: int | None, charging: bool) -> str:
        if charging:
            return C.LIME
        if pct is None:
            return C.TEXT_FAINT
        return C.FAULT if pct <= 10 else C.LOST if pct <= 20 else C.TEXT

    @staticmethod
    def text_color(pct: int | None) -> str:
        return C.BG if pct is not None and pct >= 50 else C.TEXT

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._framed:
            p.setPen(QPen(qcolor(C.LINE), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        x = SP[1] - 2
        icon("gamepad", C.TEXT_DIM, 16).paint(p, int(x), (self.H - 16) // 2, 16, 16)
        x += 16 + SP[0]
        f = QFont(self.font())
        f.setPixelSize(12)
        f.setBold(True)
        p.setFont(f)
        p.setPen(qcolor(C.TEXT))
        p.drawText(QRectF(x, 0, 12, self.H), Qt.AlignCenter, str(self._n))
        bat = QRectF(x + 12 + SP[0] + 1, (self.H - self.BAT_H) / 2, self.BAT_W, self.BAT_H)  # сразу за номером
        body = QPainterPath()
        body.addRoundedRect(bat, 3, 3)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(C.TEXT, 46))
        p.drawPath(body)
        lvl = battery_level(self._pct)
        if lvl is not None or self._charging:
            frac = lvl[1] if lvl is not None else 1.0  # при зарядке уровня нет — батарейка полная лаймовая
            fill = QRectF(bat.left(), bat.top(), bat.width() * frac, bat.height())
            p.save()
            p.setClipPath(body)
            p.fillRect(fill, qcolor(self.fill_color(self._pct, self._charging)))
            p.restore()
        p.setPen(QPen(qcolor(C.TEXT, 140), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(bat.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(C.TEXT, 140))
        p.drawRoundedRect(QRectF(bat.right() + 1, bat.center().y() - 2, 1.5, 4), 0.75, 0.75)
        if self._charging:  # молния рисуется, а не символом: эмодзи на Windows цветное
            bx, cy = bat.center().x(), bat.center().y()
            k = self.BAT_H / 17  # молния — под высоту батарейки
            bolt = QPainterPath(QPointF(bx + 1.5 * k, cy - 6 * k))
            for dx, dy in ((-3.5, 1), (-0.5, 1), (-1.5, 6), (3.5, -1), (0.5, -1), (1.5, -6)):
                bolt.lineTo(bx + dx * k, cy + dy * k)
            bolt.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(C.BG))
            p.drawPath(bolt)
        if self._gear is not None:
            self._paint_gear(p, bat.right() + 3)
        p.end()

    def _paint_gear(self, p: QPainter, x: float) -> None:
        sep = x + self.BAR_SEP / 2
        p.setPen(QPen(qcolor(C.LINE), 1))
        p.drawLine(QPointF(sep, self.H / 2 - 7), QPointF(sep, self.H / 2 + 7))
        x += self.BAR_SEP
        on = qcolor(C.LIME if self._flash.isActive() else C.TEXT)
        p.setPen(Qt.NoPen)
        bottom, top = self.H / 2 + 6.5, 4.0  # столбики растут слева направо: низкий — медленно
        for i in range(1, self._gears + 1):
            h = top + 9 * i / self._gears
            p.setBrush(on if i <= self._gear else qcolor(C.TEXT, 56))
            p.drawRoundedRect(QRectF(x, bottom - h, self.BAR_W, h), 1, 1)
            x += self.BAR_W + self.BAR_GAP


class StateTag(QLabel):
    """Плашка 5 px в шапке клетки: эфир, состояние слежения, симулятор (docs/BRANDBOOK.md §6)."""

    TEXTS = {"on": "ВЕДУ", "lost": "ИЩУ", "off": "ВЫКЛ", "standby": "ОЖИДАНИЕ", "no_analysis": "БЕЗ АНАЛИЗА",
             "pgm": "PGM", "pvw": "PVW", "sim": "СИМУЛЯТОР"}

    def __init__(self, kind: str = "off", parent: QWidget | None = None):
        super().__init__(parent)
        self._kind = ""
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAlignment(Qt.AlignCenter)
        self.set(kind)

    def kind(self) -> str:
        return self._kind

    def set(self, kind: str) -> None:
        if kind == self._kind:
            return
        self._kind = kind
        self.setText(self.TEXTS.get(kind, kind))
        bg, fg, border = {
            "on": (C.LIME, C.BG, C.LIME),
            "lost": (C.LOST, C.BG, C.LOST),
            "pgm": (C.PGM, C.TEXT, C.PGM),
            "pvw": ("transparent", C.PVW, C.PVW),
            "sim": (C.TEXT, C.BG, C.TEXT),
        }.get(kind, ("transparent", C.TEXT_FAINT, C.LINE))
        self.setStyleSheet(
            f"background: {bg}; color: {fg}; border: 1px solid {border}; border-radius: {R_TAG}px;"
            f" padding: 1px 5px; font-size: 10.5px; font-weight: 700;"
        )


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
            self.hint_label.setText("Нажмите, чтобы включить · с геймпада: Options, затем ×" if can_power
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
        self.tags_enabled = True  # клетка сетки показывает эфир и слежение в своей шапке — ярлыки не нужны
        self._overlays: list[tuple[QWidget, str]] = []
        self._placing = False

    def add_overlay(self, w: QWidget, corner: str) -> None:
        """Виджет поверх видео: «top-left» — под ярлыком слежения, «top-center» — сверху по центру, «bottom-left» — внизу слева."""
        w.setParent(self)
        w.ensurePolished()  # стиль (шрифты, отступы) — до первого расчёта размера, иначе после показа размер меняется
        w.installEventFilter(self)  # поменялся размер или показали — сразу на место, а не на следующем тике окна
        self._overlays.append((w, corner))
        self._place_tags()

    def set_overlay_corner(self, w: QWidget, corner: str) -> None:
        """Перенести виджет поверх видео в другой угол (меню Panasonic — влево, чтобы не закрывать меню камеры)."""
        self._overlays = [(x, corner if x is w else c) for x, c in self._overlays]
        self._place_tags()

    def remove_overlay(self, w: QWidget) -> None:
        """Снять виджет с этой рамки (чтобы перенести его на другую)."""
        self._overlays = [(x, c) for x, c in self._overlays if x is not w]
        w.removeEventFilter(self)

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() in (QEvent.Resize, QEvent.Show) and not self._placing and any(
                x is obj for x, _c in self._overlays):
            self._place_tags()
        return False

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
        self.onair_tag.setVisible(self._tally is not None and self.tags_enabled)
        self._place_tags()
        self.update()

    def set_tracking_tag(self, text: str, color: str) -> None:
        if color == C.TEXT_FAINT:  # «выкл»: нейтральный ярлык
            self.tracking_tag.setStyleSheet(self._tag_style(C.RAISED_HI, C.TEXT, 'bottom-right'))
        else:
            self.tracking_tag.setStyleSheet(self._tag_style(color, C.BG, 'bottom-right'))
        self.tracking_tag.setText(text)
        self.tracking_tag.setVisible(self.tags_enabled)
        self._place_tags()

    def _place_tags(self) -> None:
        self._placing = True  # adjustSize ниже шлёт Resize — без повторного входа
        try:
            self._place_tags_now()
        finally:
            self._placing = False

    def _place_tags_now(self) -> None:
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
            elif corner == "left":  # слева по центру высоты: меню Panasonic камера рисует в середине кадра
                w.move(off + SP[1], max(off, (self.height() - w.height()) // 2))
            elif corner == "top-center":
                w.move(max(off, (self.width() - w.width()) // 2), off + SP[1])
            elif corner == "top-left":
                below = self.tracking_tag.height() if self.tracking_tag.isVisibleTo(self) else 0
                w.move(off + SP[1], off + below + SP[1])
            elif corner == "bottom-right":
                w.move(self.width() - w.width() - off - SP[1], self.height() - w.height() - off - SP[1])
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


class SilhouetteButton(QPushButton):
    """Крупность кадра рисунком: пояс, в рост, общий план (спека «Кадр»). Активная — лаймовая рамка."""

    W, H = 56, 40

    def __init__(self, kind: str, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.kind = kind
        self.setCheckable(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        h = self.H + 24
        self.setStyleSheet(f"QPushButton {{ background: transparent; border: none; padding: 0;"
                           f" min-height: {h}px; max-height: {h}px; }}")
        self.setFixedSize(self.W + 24, h)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        box = QRectF((self.width() - self.W) / 2 + 0.75, 0.75, self.W - 1.5, self.H - 1.5)
        if self.underMouse() and not on:
            p.setBrush(qcolor(C.RAISED))
        else:
            p.setBrush(Qt.NoBrush)
        p.setPen(QPen(qcolor(C.LIME if on else C.GREY), 1.5))
        p.drawRoundedRect(box, 6, 6)
        fig = qcolor(C.TEXT if on else C.TEXT_DIM)
        p.setPen(Qt.NoPen)
        p.setBrush(fig)
        x, y, w, h = box.x(), box.y(), box.width(), box.height()
        cx = x + w / 2
        p.save()
        p.setClipRect(box.adjusted(1, 1, -1, -1))
        if self.kind == "waist_center":
            p.drawEllipse(QPointF(cx, y + h * 0.37), h * 0.16, h * 0.16)
            body = QPainterPath(QPointF(cx - w * 0.28, y + h))
            body.cubicTo(cx - w * 0.28, y + h * 0.66, cx - w * 0.14, y + h * 0.6, cx, y + h * 0.6)
            body.cubicTo(cx + w * 0.14, y + h * 0.6, cx + w * 0.28, y + h * 0.66, cx + w * 0.28, y + h)
            p.drawPath(body)
        elif self.kind == "full":
            p.drawEllipse(QPointF(cx, y + h * 0.2), h * 0.09, h * 0.09)
            p.drawRoundedRect(QRectF(cx - w * 0.1, y + h * 0.32, w * 0.2, h * 0.3), 2, 2)
            p.drawRect(QRectF(cx - w * 0.06, y + h * 0.6, w * 0.12, h * 0.28))
        else:
            p.setPen(QPen(qcolor(C.GREY), 1))
            p.drawLine(QPointF(x + 4, y + h * 0.8), QPointF(x + w - 4, y + h * 0.8))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx, y + h * 0.45), h * 0.05, h * 0.05)
            p.drawRect(QRectF(cx - w * 0.035, y + h * 0.52, w * 0.07, h * 0.28))
        p.restore()
        f = QFont(self.font())
        f.setPixelSize(FS_CAPTION - 1)
        p.setFont(f)
        p.setPen(qcolor(C.TEXT if on else C.TEXT_DIM))
        p.drawText(QRectF(0, self.H + 4, self.width(), 18), Qt.AlignHCenter | Qt.AlignTop, self.text())
        p.end()
