"""Панель «Агент»: оператор разрешает программе-агенту (на этом же компьютере) управлять слежением.

Оба флажка по умолчанию выключены и не сохраняются. Esc, ручное управление, выключение слежения
оператором, смена камеры, раскачка и аварийный стоп снимают их — `revoke()` можно звать из любого потока.
Снятый «НЕ в эфире» выключает слежение, если его включил агент (`WindowBackend`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QGroupBox, QLabel, QVBoxLayout

from ptztracker.ui.theme import C


def revokes_agent(on: bool, reason: str | None) -> bool:
    """Выключение слежения, после которого агент теряет управление: раскачка или аварийный стоп."""
    return not on and bool(reason) and (reason == "аварийный стоп" or "раскач" in reason)


class AgentPanel(QGroupBox):
    _revoked = Signal()  # из чужого потока — снять флажки в потоке Qt
    offAirCleared = Signal()  # «НЕ в эфире» снят (оператором или revoke) — поток Qt

    def __init__(self, parent=None):
        super().__init__("Агент", parent)
        # снимки для потоков API: читаются без Qt, пишутся флажками и revoke()
        self.control_allowed = False
        self.not_on_air = False
        self.allow_check = QCheckBox("Разрешить управление агенту")
        self.off_air_check = QCheckBox("Камера НЕ в эфире")
        for c in (self.allow_check, self.off_air_check):
            c.setFocusPolicy(Qt.NoFocus)  # пробел и стрелки обрабатывает окно
        self.allow_check.setToolTip(
            "Только с этим флажком агент может выключить слежение. Esc, ручное управление, выключение "
            "слежения оператором, смена камеры, раскачка и аварийный стоп сразу снимают оба флажка."
        )
        self.off_air_check.setToolTip(
            "Вместе с первым флажком агент может двигать камеру: включать слежение, выбирать цель, "
            "композицию и характер движения, запускать калибровку. Ставьте, только когда камера не в эфире. "
            "Снимете флажок — слежение, которое включил агент, выключится."
        )
        self.api_label = QLabel("")
        self.api_label.setWordWrap(True)
        self.api_label.setStyleSheet(f"color: {C.TEXT_DIM};")
        lay = QVBoxLayout(self)
        lay.addWidget(self.allow_check)
        lay.addWidget(self.off_air_check)
        lay.addWidget(self.api_label)
        self.allow_check.toggled.connect(self._on_allow)
        self.off_air_check.toggled.connect(self._on_off_air)
        self._revoked.connect(self._uncheck)

    def _on_allow(self, on: bool) -> None:
        self.control_allowed = on

    def _on_off_air(self, on: bool) -> None:
        self.not_on_air = on
        if not on:
            self.offAirCleared.emit()

    def revoke(self) -> None:
        """Снять оба разрешения. Снимок меняется сразу, флажки — в потоке Qt."""
        self.control_allowed = False
        self.not_on_air = False
        self._revoked.emit()

    def on_follow_change(self, on: bool, reason: str | None) -> None:
        """Follower.on_change (поток слежения)."""
        if revokes_agent(on, reason):
            self.revoke()

    def _uncheck(self) -> None:
        self.allow_check.setChecked(False)
        self.off_air_check.setChecked(False)

    def set_api_status(self, text: str, error: bool = False) -> None:
        self.api_label.setText(text)
        self.api_label.setStyleSheet(f"color: {C.FAULT if error else C.TEXT_DIM};")
