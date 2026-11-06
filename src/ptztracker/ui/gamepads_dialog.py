"""Окно «Геймпады…» (☰): все геймпады, которые программа видела, у каждого своя чувствительность стика.

Изменения действуют сразу и сохраняются окном; камеру само окно не трогает.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ptztracker.config import DEFAULT_GEAR, SPEED_GEARS
from ptztracker.control.manual_curve import (
    PAD_GEAR_DEG_S,
    SENS_MAX,
    SENS_MIN,
    TELE_STRENGTH_MAX,
    clamp_sens,
    shape,
    tele_scale,
)
from ptztracker.ui.icons import icon
from ptztracker.ui.theme import FS_CAPTION, R_PANEL, SP, C, qcolor
from ptztracker.ui.widgets import battery_level

L1R1_MODES = (("limit", "L1/R1 — предел скорости"), ("sens", "L1/R1 — чувствительность"))
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
    mode: str = "limit"  # что меняют L1/R1: limit — предел скорости, sens — чувствительность
    gear: int = DEFAULT_GEAR  # ступень скорости L1/R1


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
    if r.connected and r.charging:  # SDL при зарядке уровня не даёт
        text += " · заряжается"
    elif r.connected and (lvl := battery_level(r.battery)) is not None:
        text += f" · заряд {lvl[0]}"  # без процентов: SDL даёт заряд грубо (5/20/70/100 %)
        if r.battery_time:
            text += f" · {r.battery_time}"
    if r.mode == "limit":  # ступень видна только в режиме предела скорости
        text += f" · ступень {r.gear} из {SPEED_GEARS} · до {PAD_GEAR_DEG_S[r.gear - 1]} °/с"
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


TELE_MIN_UI = 20  # «Чуть» на ползунке: слабее — почти не заметно


class TeleGraph(QWidget):
    """Скорость ручного поворота по зуму (×1…×20, лог-шкала): лайм — выбранная сила, пунктир — «Чуть» и «Сильно»."""

    def __init__(self, strength: int, parent=None):
        super().__init__(parent)
        self.strength = strength
        self.setFixedSize(240, 96)

    def set_strength(self, v: int) -> None:
        self.strength = v
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(qcolor(C.LINE))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 8, 8)
        inner = r.adjusted(8, 8, -8, -18)
        f = p.font()
        f.setPixelSize(10)
        p.setFont(f)
        p.setPen(qcolor(C.TEXT_FAINT))
        for z, align in ((1, Qt.AlignLeft), (4, Qt.AlignHCenter), (20, Qt.AlignRight)):
            p.drawText(QRectF(inner.left(), inner.bottom() + 2, inner.width(), 14), align | Qt.AlignVCenter, f"×{z}")
        ratio = math.log(20.0)

        def path(strength: float) -> QPainterPath:
            pp = QPainterPath()
            for i in range(41):
                z = math.exp(i / 40 * ratio)
                pt = QPointF(inner.left() + i / 40 * inner.width(),
                             inner.top() + (1 - tele_scale(z, strength)) * inner.height())
                pp.moveTo(pt) if i == 0 else pp.lineTo(pt)
            return pp

        dash = QPen(qcolor(C.GREY), 1.1, Qt.DashLine)
        for s in (TELE_MIN_UI, TELE_STRENGTH_MAX):
            p.setPen(dash)
            p.drawPath(path(s))
        p.setPen(QPen(qcolor(C.LIME if self.isEnabled() else C.GREY), 2))
        p.drawPath(path(self.strength))
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
                    f" border-radius: {R_PANEL}px; }} QCheckBox, QLabel, QSlider {{ background: transparent; }}")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(1, 1, 1, 1)
    lay.setSpacing(0)
    return f, lay


class GamepadsDialog(QDialog):
    """Окно «Геймпады» по макету docs/design/mockups/pads-atem.html: строка на геймпад (значок, имя, какой камерой
    управляет, заряд, «Плавно ↔ Резко» и график отклика), общая настройка отдельной группой."""

    def __init__(self, rows: list[PadRow], slow_on_tele: bool,
                 on_sensitivity: Callable[[str, int], None], on_slow_tele: Callable[[bool], None], parent=None,
                 on_mode: Callable[[str, str], None] | None = None, tele_strength: int = TELE_STRENGTH_MAX,
                 on_tele_strength: Callable[[int], None] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Геймпады")
        self._on_tele_strength = on_tele_strength
        self._on_sens = on_sensitivity
        self._on_mode = on_mode
        self.mode_combos: dict[str, QComboBox] = {}
        self._rows: dict[str, PadRow] = {}
        self.sliders: dict[str, QSlider] = {}
        self.status_labels: dict[str, QLabel] = {}
        self.name_labels: dict[str, QLabel] = {}
        self.tiles: dict[str, QLabel] = {}
        self.curves: dict[str, CurvePreview] = {}
        self.row_extras: dict[str, QHBoxLayout] = {}  # место под свои настройки геймпада (L1/R1 и т. п.)
        self._list, self._list_lay = _panel()
        self.empty_label = QLabel("Программа ещё не видела ни одного геймпада. Подключите его — он появится здесь.")
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet(f"color: {C.TEXT_DIM};")

        self.slow_tele_check = QCheckBox("Медленнее на крупном плане")
        self.slow_tele_check.setChecked(bool(slow_on_tele))
        self.slow_tele_check.toggled.connect(on_slow_tele)
        tele_note = QLabel("чем сильнее зум, тем медленнее ручной поворот — легче держать кадр")
        tele_note.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px; margin-left: 24px;")
        # насколько медленнее: ползунок «Чуть ↔ Сильно», цифра на ×8 и график скорости по зуму
        self.tele_slider = QSlider(Qt.Horizontal)
        self.tele_slider.setRange(TELE_MIN_UI, TELE_STRENGTH_MAX)
        self.tele_slider.setSingleStep(5)
        self.tele_slider.setPageStep(25)
        self.tele_slider.setFixedWidth(170)
        self.tele_slider.setTracking(False)  # в config.json — по отпусканию
        self.tele_readout = QLabel()
        self.tele_readout.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
        self.tele_graph = TeleGraph(tele_strength)
        self.tele_slider.setValue(max(TELE_MIN_UI, min(TELE_STRENGTH_MAX, int(tele_strength))))
        self.tele_slider.sliderMoved.connect(self._show_tele)
        self.tele_slider.valueChanged.connect(self._tele_changed)
        self._show_tele(self.tele_slider.value())
        strength = QHBoxLayout()
        strength.setSpacing(SP[1])
        strength.addWidget(QLabel("Насколько"))
        strength.addSpacing(SP[2])
        strength.addWidget(self._caption("Чуть"))
        strength.addWidget(self.tele_slider)
        strength.addWidget(self._caption("Сильно"))
        strength.addStretch(1)
        strength.addWidget(self.tele_readout)
        graph_row = QHBoxLayout()
        graph_row.addWidget(self.tele_graph)
        legend = QLabel("Скорость поворота по зуму. Лайм — выбранная сила,\nпунктир — «Чуть» и «Сильно». «Сильно» —\nкак было раньше.")
        legend.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
        graph_row.addWidget(legend, 1, Qt.AlignVCenter)
        tele_panel, tl = _panel()
        tl.setContentsMargins(SP[2], SP[2], SP[2], SP[2])
        tl.setSpacing(SP[1])
        tl.addWidget(self.slow_tele_check)
        tl.addWidget(tele_note)
        tl.addLayout(strength)
        tl.addLayout(graph_row)
        self.slow_tele_check.toggled.connect(self._sync_tele_enabled)
        self._sync_tele_enabled(self.slow_tele_check.isChecked())

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
        # место под настройки этого геймпада (например, выбор назначения L1/R1) — строкой под состоянием
        extra = QHBoxLayout()
        extra.setSpacing(SP[1])
        text.addLayout(extra)
        # что меняют L1/R1 у этого геймпада: предел скорости (ступени) или чувствительность
        mode = QComboBox()
        for key, label in L1R1_MODES:
            mode.addItem(label, key)
        mode.setCurrentIndex(max(0, mode.findData(r.mode)))
        mode.setToolTip(f"R1 — быстрее, L1 — медленнее. «Предел скорости» — полный наклон даёт от {PAD_GEAR_DEG_S[0]}"
                        f" до {PAD_GEAR_DEG_S[-1]} °/с за {SPEED_GEARS} ступеней;"
                        " «Чувствительность» — ползунок справа шагами по 10. Крупность — свайп по тачпаду.")
        mode.currentIndexChanged.connect(lambda _i, pid=r.pad_id, c=mode: self._mode_changed(pid, c.currentData()))
        extra.addWidget(mode)
        extra.addStretch(1)
        self.mode_combos[r.pad_id] = mode
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
        self.row_extras[r.pad_id] = extra
        return row

    def set_rows(self, rows: list[PadRow]) -> None:
        """Перестроить список (геймпад подключили или отключили), не трогая настроек."""
        while self._list_lay.count():
            w = self._list_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for d in (self.sliders, self.status_labels, self.name_labels, self.tiles, self.curves, self.row_extras,
                  self.mode_combos):
            d.clear()
        self._rows = {r.pad_id: r for r in rows}
        for i, r in enumerate(rows):
            self._list_lay.addWidget(self._row(i, r))
        self.empty_label.setVisible(not rows)
        self._list.setVisible(bool(rows))
    def _mode_changed(self, pad_id: str, mode: str) -> None:
        if self._on_mode is not None:
            self._on_mode(pad_id, mode)
        r = self._rows.get(pad_id)
        if r is not None:  # ступень видна только в режиме предела
            self._rows[pad_id] = r = replace(r, mode=mode)
            self.status_labels[pad_id].setText(status_text(r))

    def _show_tele(self, v: int) -> None:
        self.tele_readout.setText(f"на ×8 — {round(tele_scale(8.0, v) * 100)} % скорости")
        self.tele_graph.set_strength(v)

    def _tele_changed(self, v: int) -> None:
        self._show_tele(v)
        if self._on_tele_strength is not None:
            self._on_tele_strength(v)

    def _sync_tele_enabled(self, on: bool) -> None:
        for w in (self.tele_slider, self.tele_graph):
            w.setEnabled(on)

    def set_slow_on_tele(self, on: bool) -> None:
        self.slow_tele_check.blockSignals(True)
        self.slow_tele_check.setChecked(bool(on))
        self.slow_tele_check.blockSignals(False)
