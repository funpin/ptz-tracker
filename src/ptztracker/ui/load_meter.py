"""Нагрузка на компьютер в верхней строке: сверху «всего», снизу доля программы (макет recording-controls.html)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ptztracker.system_load import LoadSnapshot, Metric, load_level
from ptztracker.ui.theme import FS_CAPTION, FS_TEXT, SP, C

KEYS = (("cpu", "CPU", "Процессор"), ("gpu", "GPU", "Видеокарта"), ("ram", "RAM", "Память"))
_LEVEL_COLOR = {"": C.TEXT, "warn": C.LOST, "crit": C.FAULT}


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{round(v)} %"


class LoadMeter(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.NoFocus)
        self._snap = LoadSnapshot()
        self._totals: dict[str, QLabel] = {}
        self._owns: dict[str, QLabel] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SP[3])
        for key, name, _ in KEYS:
            col = QVBoxLayout()
            col.setSpacing(0)
            top = QHBoxLayout()
            top.setSpacing(SP[0])
            cap = QLabel(name)
            cap.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: 11px; font-weight: 600;")
            total = QLabel("—")
            own = QLabel("PTZ —")
            own.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: 11px;")
            top.addWidget(cap)
            top.addWidget(total)
            top.addStretch(1)
            col.addLayout(top)
            col.addWidget(own)
            row.addLayout(col)
            self._totals[key], self._owns[key] = total, own
        self.set_snapshot(self._snap)

    def _metric(self, key: str) -> Metric:
        return getattr(self._snap, key)

    def total_text(self, key: str) -> str:
        return _pct(self._metric(key).total)

    def own_text(self, key: str) -> str:
        return f"PTZ {_pct(self._metric(key).own)}"

    def level(self, key: str) -> str:
        return load_level(self._metric(key).total)

    def set_snapshot(self, s: LoadSnapshot) -> None:
        self._snap = s
        for key, label in self._totals.items():
            label.setText(self.total_text(key))
            label.setStyleSheet(f"color: {_LEVEL_COLOR[self.level(key)]}; font-size: {FS_TEXT}px; font-weight: 600;")
            self._owns[key].setText(self.own_text(key))
        self.setToolTip(self._table())

    def _table(self) -> str:
        rows = "".join(
            f"<tr><td>{title}<br><span style='font-size:{FS_CAPTION}px; color:{C.TEXT_FAINT}'>"
            f"{self._metric(key).detail}</span></td>"
            f"<td align='right'>&nbsp;&nbsp;{_pct(self._metric(key).total)}</td>"
            f"<td align='right'>&nbsp;&nbsp;{_pct(self._metric(key).own)}</td></tr>"
            for key, _, title in KEYS
        )
        return (f"<table><tr><td></td><td align='right'>&nbsp;&nbsp;Всего</td>"
                f"<td align='right'>&nbsp;&nbsp;PTZ Tracker</td></tr>{rows}</table>"
                "<p>Проценты — от всего компьютера, а не от одного ядра.</p>")
