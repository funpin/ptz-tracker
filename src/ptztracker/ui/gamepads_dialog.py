"""Окно «Геймпады…» (☰): все геймпады, которые программа видела, у каждого своя чувствительность стика
и свой режим L1/R1 (предел скорости или чувствительность).

Изменения действуют сразу и сохраняются окном; камеру само окно не трогает.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ptztracker.config import SPEED_GEARS
from ptztracker.control.manual_curve import SENS_MAX, SENS_MIN, clamp_sens
from ptztracker.ui.theme import FS_CAPTION, SP, C

SHARED_NOTE = "одинаковые геймпады без серийного номера делят эту настройку"
L1R1_MODES = (("limit", "L1/R1 — предел скорости"), ("sens", "L1/R1 — чувствительность"))
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
    mode: str = "limit"  # что меняют L1/R1
    gear: int = SPEED_GEARS  # ступень предела скорости


class GamepadsDialog(QDialog):
    def __init__(self, rows: list[PadRow], slow_on_tele: bool,
                 on_sensitivity: Callable[[str, int], None], on_slow_tele: Callable[[bool], None], parent=None,
                 on_mode: Callable[[str, str], None] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Геймпады")
        self._on_sens = on_sensitivity
        self._on_mode = on_mode
        self.sliders: dict[str, QSlider] = {}
        self.mode_combos: dict[str, QComboBox] = {}
        self._rows: dict[str, PadRow] = {}
        self.status_labels: dict[str, QLabel] = {}
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(SP[2])
        self._grid.setVerticalSpacing(SP[1])
        self.empty_label = QLabel("Программа ещё не видела ни одного геймпада. Подключите его — он появится здесь.")
        self.empty_label.setWordWrap(True)

        intro = QLabel("Чувствительность стика: «Плавно» — мягче у центра, легче сделать маленький поворот;"
                       " «Резко» — как раньше. Полный наклон всегда даёт полную скорость. Действует сразу.\n"
                       "L1/R1 на геймпаде: R1 — быстрее, L1 — медленнее. «Предел скорости» — полный наклон даёт"
                       " 25 / 50 / 75 / 100 % (толчков столько, какая ступень); «Чувствительность» — этот ползунок"
                       " шагами по 10. Крупность — свайп по тачпаду: вправо шире, влево ближе.")
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
        speed = f" · скорость {r.gear} из {SPEED_GEARS}" if r.mode == "limit" else ""
        if not r.connected:
            return "не подключён" + speed
        return (f"подключён · камера {r.cell}" if r.cell is not None else "подключён · без камеры") + speed

    def _note(self, r: PadRow) -> str:
        return self._status(r) + ("" if has_serial(r.pad_id) else f" · {SHARED_NOTE}")

    def set_rows(self, rows: list[PadRow]) -> None:
        """Перестроить список (геймпад подключили или отключили), не трогая настроек."""
        while self._grid.count():
            w = self._grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.sliders.clear()
        self.status_labels.clear()
        self.mode_combos.clear()
        self._rows = {r.pad_id: r for r in rows}
        for i, r in enumerate(rows):
            # два одинаковых геймпада различаются по хвосту серийного номера
            name = QLabel(f"{r.name} · …{r.pad_id[-5:]}" if has_serial(r.pad_id) else r.name)
            name.setToolTip(r.pad_id)
            status = self._caption("")
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
            status.setText(self._note(r))
            status.setWordWrap(True)
            self._grid.addWidget(status, row + 1, 0, 1, 2)
            mode = QComboBox()
            for key, text in L1R1_MODES:
                mode.addItem(text, key)
            mode.setCurrentIndex(max(0, mode.findData(r.mode)))
            mode.currentIndexChanged.connect(lambda _i, pid=r.pad_id, c=mode: self._mode_changed(pid, c.currentData()))
            self._grid.addWidget(mode, row + 1, 2, 1, 2)
            self.mode_combos[r.pad_id] = mode
            self.sliders[r.pad_id] = sl
            self.status_labels[r.pad_id] = status
        self._grid.setColumnStretch(2, 1)
        self.empty_label.setVisible(not rows)
        self._grid_host.setVisible(bool(rows))

    def _mode_changed(self, pad_id: str, mode: str) -> None:
        if self._on_mode is not None:
            self._on_mode(pad_id, mode)
        r = self._rows.get(pad_id)
        if r is not None:  # «скорость N из 4» видна только в режиме предела
            self._rows[pad_id] = r = replace(r, mode=mode)
            self.status_labels[pad_id].setText(self._note(r))

    def set_slow_on_tele(self, on: bool) -> None:
        self.slow_tele_check.blockSignals(True)
        self.slow_tele_check.setChecked(bool(on))
        self.slow_tele_check.blockSignals(False)
