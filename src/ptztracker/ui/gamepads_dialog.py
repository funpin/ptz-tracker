"""Окно «Геймпады…» (☰): все геймпады, которые программа видела, у каждого своя чувствительность стика.

Изменения действуют сразу и сохраняются окном; камеру само окно не трогает.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ptztracker.control.manual_curve import SENS_MAX, SENS_MIN, clamp_sens, shape
from ptztracker.ui.icons import icon
from ptztracker.ui.theme import FS_CAPTION, R_PANEL, SP, C, qcolor

SHARED_NOTE = "одинаковые геймпады без серийного номера делят эту настройку"
_GUID_ID = re.compile(r"^(?:[0-9A-Fa-f]{32}|\?):")  # gamepad.device: без серийного номера — «GUID:имя»


def has_serial(pad_id: str) -> bool:
    """Опознаватель — серийный номер (у DualSense — адрес Bluetooth), а не «GUID:имя»."""
    return not _GUID_ID.match(pad_id)


@dataclass(frozen=True)
class PadRow:
    pad_id: str
    name: str
    sensitivity: int
    connected: bool = False
    cell: int | None = None  # номер клетки 1–4 (огоньки на геймпаде); None — без камеры или не подключён
    battery: int | None = None  # заряд, %
    charging: bool = False
    battery_time: str | None = None  # «~5 ч» — сколько ещё проработает
    tally: str | None = None  # эфир камеры, которой управляет: pgm | pvw | None — цвет плитки номера


def time_left_text(seconds: float | None) -> str | None:
    """«~5 ч», «~1,5 ч», «~30 мин» — сколько ещё проработает геймпад."""
    if seconds is None or seconds <= 0:
        return None
    h = seconds / 3600
    if h >= 2:
        return f"~{round(h)} ч"
    if h >= 1:
        return f"~{h:.1f} ч".replace(".", ",")
    return f"~{max(1, round(seconds / 60))} мин"


def status_text(r: PadRow) -> str:
    if not r.connected:
        text = "не подключён · настройка сохранится"
    elif r.cell is None:
        text = "без камеры — выберите клетку кнопкой на геймпаде"
    else:
        text = f"управляет камерой {r.cell}"
    if r.connected and r.battery is not None:
        text += f" · {'заряжается, ' if r.charging else ''}{r.battery} %"
        if r.battery_time and not r.charging:
            text += f" · {r.battery_time}"
    return text + ("" if has_serial(r.pad_id) else f" · {SHARED_NOTE}")


class CurvePreview(QWidget):
    """Маленький график: как стик отвечает на наклон при этой чувствительности (ось X — наклон, Y — скорость)."""

    def __init__(self, sens: int, parent=None):
        super().__init__(parent)
        self.sens = sens
        self.setFixedSize(48, 40)
        self.setToolTip("Как стик отвечает на наклон: ниже у начала — мягче у центра")

    def set_sens(self, sens: int) -> None:
        self.sens = sens
        self.update()

    def value_at(self, v: float) -> float:
        return shape(v, self.sens)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(qcolor(C.LINE))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 6, 6)
        inner = r.adjusted(5, 5, -5, -5)
        path = QPainterPath(QPointF(inner.left(), inner.bottom()))
        for i in range(1, 41):
            v = i / 40
            path.lineTo(inner.left() + v * inner.width(), inner.bottom() - self.value_at(v) * inner.height())
        pen = QPen(qcolor(C.LIME if self.isEnabled() else C.GREY), 1.6)
        p.setPen(pen)
        p.drawPath(path)
        p.end()


class _ZebraRow(QWidget):
    def __init__(self, shaded: bool, dim: bool):
        super().__init__()
        self.shaded, self.dim = shaded, dim
        self.setStyleSheet("QWidget { background: transparent; }")

    def paintEvent(self, _e) -> None:  # noqa: N802
        if self.shaded:
            p = QPainter(self)
            p.fillRect(self.rect(), qcolor(C.TEXT, 9))
            p.end()


def _section(title: str) -> QLabel:
    lab = QLabel(title.upper())
    lab.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: 11px; font-weight: 600; letter-spacing: 1px;")
    return lab


def _panel() -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("padsPanel")
    f.setStyleSheet(f"QFrame#padsPanel {{ background: {C.SURFACE}; border: 1px solid {C.LINE};"
                    f" border-radius: {R_PANEL}px; }} QCheckBox, QLabel {{ background: transparent; }}")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(1, 1, 1, 1)
    lay.setSpacing(0)
    return f, lay


class GamepadsDialog(QDialog):
    """Окно «Геймпады» по макету docs/design/mockups/pads-atem.html: строка на геймпад (значок, имя, какой камерой
    управляет, заряд, «Плавно ↔ Резко» и график отклика), общая настройка отдельной группой."""

    def __init__(self, rows: list[PadRow], slow_on_tele: bool,
                 on_sensitivity: Callable[[str, int], None], on_slow_tele: Callable[[bool], None], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Геймпады")
        self._on_sens = on_sensitivity
        self.sliders: dict[str, QSlider] = {}
        self.status_labels: dict[str, QLabel] = {}
        self.name_labels: dict[str, QLabel] = {}
        self.tiles: dict[str, QLabel] = {}
        self.curves: dict[str, CurvePreview] = {}
        self._list, self._list_lay = _panel()
        self.empty_label = QLabel("Программа ещё не видела ни одного геймпада. Подключите его — он появится здесь.")
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet(f"color: {C.TEXT_DIM};")

        self.slow_tele_check = QCheckBox("Медленнее на крупном плане")
        self.slow_tele_check.setChecked(bool(slow_on_tele))
        self.slow_tele_check.toggled.connect(on_slow_tele)
        tele_note = QLabel("чем сильнее зум, тем медленнее ручной поворот — легче держать кадр")
        tele_note.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px; margin-left: 24px;")
        tele_panel, tl = _panel()
        tl.setContentsMargins(SP[2], SP[2], SP[2], SP[2])
        tl.setSpacing(2)
        tl.addWidget(self.slow_tele_check)
        tl.addWidget(tele_note)

        self.done_button = QPushButton("Готово")
        self.done_button.setProperty("variant", "primary")
        self.done_button.setDefault(True)
        self.done_button.clicked.connect(self.accept)
        note = QLabel("Изменения действуют сразу")
        note.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
        foot = QHBoxLayout()
        foot.addWidget(note)
        foot.addStretch(1)
        foot.addWidget(self.done_button)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP[3], SP[3], SP[3], SP[3])
        lay.setSpacing(SP[2])
        lay.addWidget(_section("Ваши геймпады"))
        lay.addWidget(self._list)
        lay.addWidget(self.empty_label)
        lay.addWidget(_section("Для всех: мышь, стрелки и геймпады"))
        lay.addWidget(tele_panel)
        lay.addStretch(1)
        lay.addLayout(foot)
        self.set_rows(rows)
        self.resize(700, self.sizeHint().height())

    @staticmethod
    def _caption(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
        return lab

    def _row(self, i: int, r: PadRow) -> QWidget:
        row = _ZebraRow(i % 2 == 1, not r.connected)
        h = QHBoxLayout(row)
        h.setContentsMargins(SP[2], SP[1] + 2, SP[2], SP[1] + 2)
        h.setSpacing(SP[2])
        ic = QLabel()
        ic.setPixmap(icon("gamepad", C.LIME if r.connected else C.GREY, 22).pixmap(22, 22))
        h.addWidget(ic)
        text = QVBoxLayout()
        text.setSpacing(2)
        tail = f" <span style='color:{C.TEXT_FAINT}; font-weight:400'>…{r.pad_id[-5:]}</span>" \
            if has_serial(r.pad_id) else ""
        fg = C.TEXT if r.connected else C.TEXT_FAINT
        name = QLabel(f"<b style='color:{fg}'>{r.name}</b>{tail}")
        name.setToolTip(r.pad_id)
        meta = QHBoxLayout()
        meta.setSpacing(6)
        bg, tfg = {"pgm": (C.PGM, C.TEXT), "pvw": (C.PVW, C.BG)}.get(r.tally or "", (C.RAISED_HI, C.TEXT))
        tile = QLabel(str(r.cell or ""))
        tile.setFixedSize(16, 16)
        tile.setAlignment(Qt.AlignCenter)
        tile.setStyleSheet(f"background: {bg}; color: {tfg}; border-radius: 4px; font-size: 10px; font-weight: 700;")
        tile.setVisible(r.connected and r.cell is not None)
        status = self._caption(status_text(r))
        status.setStyleSheet(f"color: {C.TEXT_DIM if r.connected else C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
        status.setWordWrap(True)
        meta.addWidget(tile, 0, Qt.AlignTop)
        meta.addWidget(status, 1)
        text.addWidget(name)
        text.addLayout(meta)
        host = QWidget()
        host.setLayout(text)
        host.setFixedWidth(300)
        h.addWidget(host)
        h.addStretch(1)
        sl = QSlider(Qt.Horizontal)
        sl.setRange(SENS_MIN, SENS_MAX)
        sl.setPageStep(10)
        sl.setValue(clamp_sens(r.sensitivity))
        sl.setFixedWidth(150)
        sl.setTracking(False)  # значение — по отпусканию: config.json не пишется на каждый шаг
        sl.valueChanged.connect(lambda v, pid=r.pad_id: self._on_sens(pid, v))
        curve = CurvePreview(clamp_sens(r.sensitivity))
        sl.sliderMoved.connect(curve.set_sens)  # график — сразу, пока тянут
        sl.valueChanged.connect(curve.set_sens)
        h.addWidget(self._caption("Плавно"))
        h.addWidget(sl)
        h.addWidget(self._caption("Резко"))
        h.addWidget(curve)
        self.sliders[r.pad_id] = sl
        self.status_labels[r.pad_id] = status
        self.name_labels[r.pad_id] = name
        self.tiles[r.pad_id] = tile
        self.curves[r.pad_id] = curve
        return row

    def set_rows(self, rows: list[PadRow]) -> None:
        """Перестроить список (геймпад подключили или отключили), не трогая настроек."""
        while self._list_lay.count():
            w = self._list_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for d in (self.sliders, self.status_labels, self.name_labels, self.tiles, self.curves):
            d.clear()
        for i, r in enumerate(rows):
            self._list_lay.addWidget(self._row(i, r))
        self.empty_label.setVisible(not rows)
        self._list.setVisible(bool(rows))
    def set_slow_on_tele(self, on: bool) -> None:
        self.slow_tele_check.blockSignals(True)
        self.slow_tele_check.setChecked(bool(on))
        self.slow_tele_check.blockSignals(False)
