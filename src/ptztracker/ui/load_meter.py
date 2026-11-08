"""Нагрузка на компьютер в нижней строке: «CPU 92 % · PTZ 61» (макет load-placement.html, вариант 1)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ptztracker.system_load import LoadSnapshot, Metric, load_level
from ptztracker.ui.theme import FS_CAPTION, SP, C

KEYS = (("cpu", "CPU", "Процессор"), ("gpu", "GPU", "Видеокарта"), ("ram", "RAM", "Память"))
_LEVEL_COLOR = {"": C.TEXT, "warn": C.LOST, "crit": C.FAULT}


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{round(v)} %"


class LoadMeter(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.NoFocus)
        self._snap = LoadSnapshot()
        self._labels: dict[str, QLabel] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SP[2])
        for key, _, _ in KEYS:
            label = QLabel()
            label.setStyleSheet(f"color: {C.TEXT_FAINT}; font-size: {FS_CAPTION}px;")
            row.addWidget(label)
            self._labels[key] = label
        self.set_snapshot(self._snap)

    def _metric(self, key: str) -> Metric:
        return getattr(self._snap, key)

    def total_text(self, key: str) -> str:
        return _pct(self._metric(key).total)

    def own_text(self, key: str) -> str:
        own = self._metric(key).own
        return f"PTZ {'—' if own is None else round(own)}"

    def level(self, key: str) -> str:
        return load_level(self._metric(key).total)

    def line_text(self, key: str) -> str:
        name = next(n for k, n, _ in KEYS if k == key)
        return f"{name} {self.total_text(key)} · {self.own_text(key)}"

    def set_snapshot(self, s: LoadSnapshot) -> None:
        self._snap = s
        for key, label in self._labels.items():
            name = next(n for k, n, _ in KEYS if k == key)
            color = _LEVEL_COLOR[self.level(key)]
            label.setText(f"{name} <b style='color:{color}'>{self.total_text(key)}</b> · {self.own_text(key)}")
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
