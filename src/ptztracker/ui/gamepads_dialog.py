"""Окно «Геймпады…» (☰): все геймпады, которые программа видела, у каждого своя чувствительность стика.

Изменения действуют сразу и сохраняются окном; камеру само окно не трогает.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ptztracker.control.manual_curve import SENS_MAX, SENS_MIN, clamp_sens
from ptztracker.ui.theme import FS_CAPTION, SP, C

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


class GamepadsDialog(QDialog):
    def __init__(self, rows: list[PadRow], slow_on_tele: bool,
                 on_sensitivity: Callable[[str, int], None], on_slow_tele: Callable[[bool], None], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Геймпады")
        self._on_sens = on_sensitivity
        self.sliders: dict[str, QSlider] = {}
        self.status_labels: dict[str, QLabel] = {}
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(SP[2])
        self._grid.setVerticalSpacing(SP[1])
        self.empty_label = QLabel("Программа ещё не видела ни одного геймпада. Подключите его — он появится здесь.")
        self.empty_label.setWordWrap(True)

        intro = QLabel("Чувствительность стика: «Плавно» — мягче у центра, легче сделать маленький поворот;"
                       " «Резко» — как раньше. Полный наклон всегда даёт полную скорость. Действует сразу.")
        intro.setWordWrap(True)
        self.slow_tele_check = QCheckBox("Медленнее на крупном плане (мышь, стрелки и все геймпады)")
        self.slow_tele_check.setChecked(bool(slow_on_tele))
        self.slow_tele_check.toggled.connect(on_slow_tele)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addSpacing(SP[1])
        lay.addWidget(self._grid_host)
        lay.addWidget(self.empty_label)
        lay.addSpacing(SP[1])
        lay.addWidget(self.slow_tele_check)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.set_rows(rows)
        self.resize(640, self.sizeHint().height())

    @staticmethod
    def _caption(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
        return lab

    @staticmethod
    def _status(r: PadRow) -> str:
        if not r.connected:
            return "не подключён"
        return f"подключён · камера {r.cell}" if r.cell is not None else "подключён · без камеры"

    def set_rows(self, rows: list[PadRow]) -> None:
        """Перестроить список (геймпад подключили или отключили), не трогая настроек."""
        while self._grid.count():
            w = self._grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.sliders.clear()
        self.status_labels.clear()
        for i, r in enumerate(rows):
            # два одинаковых геймпада различаются по хвосту серийного номера
            name = QLabel(f"{r.name} · …{r.pad_id[-5:]}" if has_serial(r.pad_id) else r.name)
            name.setToolTip(r.pad_id)
            status = self._caption(self._status(r))
            sl = QSlider(Qt.Horizontal)
            sl.setRange(SENS_MIN, SENS_MAX)
            sl.setPageStep(10)
            sl.setValue(clamp_sens(r.sensitivity))
            sl.setMinimumWidth(160)
            sl.setTracking(False)  # значение — по отпусканию: config.json не пишется на каждый шаг
            sl.valueChanged.connect(lambda v, pid=r.pad_id: self._on_sens(pid, v))
            row = 2 * i
            self._grid.addWidget(name, row, 0)
            self._grid.addWidget(self._caption("Плавно"), row, 1)
            self._grid.addWidget(sl, row, 2)
            self._grid.addWidget(self._caption("Резко"), row, 3)
            note = self._status(r) + ("" if has_serial(r.pad_id) else f" · {SHARED_NOTE}")
            status.setText(note)
            status.setWordWrap(True)
            self._grid.addWidget(status, row + 1, 0, 1, 4)
            self.sliders[r.pad_id] = sl
            self.status_labels[r.pad_id] = status
        self._grid.setColumnStretch(2, 1)
        self.empty_label.setVisible(not rows)
        self._grid_host.setVisible(bool(rows))

    def set_slow_on_tele(self, on: bool) -> None:
        self.slow_tele_check.blockSignals(True)
        self.slow_tele_check.setChecked(bool(on))
        self.slow_tele_check.blockSignals(False)
