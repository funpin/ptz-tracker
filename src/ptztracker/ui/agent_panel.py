"""Панель «Агент»: оператор разрешает программе-агенту (на этом же компьютере) управлять слежением.

Оба флажка по умолчанию выключены и не сохраняются. Esc, ручное управление, смена камеры,
раскачка и аварийный стоп снимают их — `revoke()` можно звать из любого потока.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QGroupBox, QLabel, QVBoxLayout


def revokes_agent(on: bool, reason: str | None) -> bool:
    """Выключение слежения, после которого агент теряет управление: раскачка или аварийный стоп."""
    return not on and bool(reason) and (reason == "аварийный стоп" or "раскач" in reason)


class AgentPanel(QGroupBox):
    _revoked = Signal()  # из чужого потока — снять флажки в потоке Qt

    def __init__(self, parent=None):
        super().__init__("Агент", parent)
        # снимки для потоков API: читаются без Qt, пишутся флажками и revoke()
        self.control_allowed = False
        self.not_on_air = False
        self.allow_check = QCheckBox("Разрешить управление агенту")
        self.off_air_check = QCheckBox("Камера НЕ в эфире — агент может двигать камеру")
        for c in (self.allow_check, self.off_air_check):
            c.setFocusPolicy(Qt.NoFocus)  # пробел и стрелки обрабатывает окно
        self.allow_check.setToolTip(
            "Агент сможет включать и выключать слежение, выбирать цель, композицию и характер движения. "
            "Esc или любое ручное управление сразу снимают разрешение."
        )
        self.api_label = QLabel("")
        self.api_label.setWordWrap(True)
        self.api_label.setStyleSheet("color: #9ca3af;")
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
        self.api_label.setStyleSheet("color: #ef4444;" if error else "color: #9ca3af;")
