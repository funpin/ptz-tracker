"""Панель «Агент»: оператор разрешает программе-агенту (на этом же компьютере) управлять слежением.

У каждой открытой камеры свои два флажка (`AgentGate` клетки) и своя строка в панели. Оба по умолчанию
выключены и не сохраняются. Esc снимает все флажки; ручное действие, выключение слежения оператором, смена
камеры, раскачка и аварийный стоп — флажки этой камеры. `AgentGate.revoke()` можно звать из любого потока.
Снятый «НЕ в эфире» выключает слежение, если его включил агент (`WindowBackend`).

Свойства `control_allowed`, `not_on_air`, `allow_check`, `off_air_check`, `revoke()` относятся к выбранной клетке.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ptztracker.multicam.agent import AgentGate
from ptztracker.ui.theme import C

ALLOW_TIP = (
    "Только с этим флажком агент может выключить слежение. Esc, ручное управление этой камерой, выключение "
    "слежения оператором, смена камеры, раскачка и аварийный стоп сразу снимают оба флажка."
)
OFF_AIR_TIP = (
    "Вместе с первым флажком агент может двигать камеру: включать слежение, выбирать цель, "
    "композицию и характер движения, запускать калибровку. Ставьте, только когда камера не в эфире. "
    "Снимете флажок — слежение, которое включил агент, выключится."
)


def revokes_agent(on: bool, reason: str | None) -> bool:
    """Выключение слежения, после которого агент теряет управление: раскачка или аварийный стоп."""
    return not on and bool(reason) and (reason == "аварийный стоп" or "раскач" in reason)


class _Row(QWidget):
    def __init__(self, gate: AgentGate):
        super().__init__()
        self.gate = gate
        self.title = QLabel("")
        self.allow_check = QCheckBox("Разрешить управление агенту")
        self.off_air_check = QCheckBox("Камера НЕ в эфире")
        for c in (self.allow_check, self.off_air_check):
            c.setFocusPolicy(Qt.NoFocus)  # пробел и стрелки обрабатывает окно
        self.allow_check.setToolTip(ALLOW_TIP)
        self.off_air_check.setToolTip(OFF_AIR_TIP)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        for w in (self.title, self.allow_check, self.off_air_check):
            lay.addWidget(w)
        lay.addStretch(1)
        self.allow_check.toggled.connect(gate.set_allowed)
        self.off_air_check.toggled.connect(gate.set_off_air)

    def show_gate(self) -> None:
        """Флажки на экране — как в снимке (без повторной записи в снимок)."""
        for check, on in ((self.allow_check, self.gate.control_allowed), (self.off_air_check, self.gate.not_on_air)):
            if check.isChecked() != on:
                check.blockSignals(True)
                check.setChecked(on)
                check.blockSignals(False)


class AgentPanel(QGroupBox):
    _sync = Signal()  # флажки сменились в любом потоке — перерисовать в потоке Qt
    _off_air_cleared = Signal(object)
    offAirCleared = Signal(object)  # AgentGate: «НЕ в эфире» снят (оператором или revoke) — поток Qt
    anyAllowedChanged = Signal(bool)  # хоть у одной камеры «Разрешить» — значок AGENT

    def __init__(self, parent=None):
        super().__init__("Агент", parent)
        self._grid = None
        self._rows: dict[AgentGate, _Row] = {}
        self._null = _Row(AgentGate())  # выбранной камеры нет: флажки-пустышки
        self.empty_label = QLabel("Нет открытых камер.")
        self.empty_label.setStyleSheet(f"color: {C.TEXT_DIM};")
        self.api_label = QLabel("")
        self.api_label.setWordWrap(True)
        self.api_label.setStyleSheet(f"color: {C.TEXT_DIM};")
        self._lay = QVBoxLayout(self)
        self._lay.addWidget(self.empty_label)
        self._lay.addWidget(self.api_label)
        self._sync.connect(self._show_all)
        self._off_air_cleared.connect(self.offAirCleared)
        self._any = False

    # --- привязка к сетке ---------------------------------------------------

    def attach(self, grid) -> None:
        self._grid = grid
        self.refresh()

    def refresh(self) -> None:
        """Строки — по открытым клеткам (поток Qt): вызывать после любого открытия/закрытия клетки."""
        cells = list(self._grid.cells) if self._grid else []
        live = {c.gate: i for i, c in enumerate(cells) if c is not None}
        for gate in [g for g in self._rows if g not in live]:
            row = self._rows.pop(gate)
            self._lay.removeWidget(row)
            row.deleteLater()
        for i, c in enumerate(cells):
            if c is None:
                continue
            row = self._rows.get(c.gate)
            if row is None:
                row = self._rows[c.gate] = _Row(c.gate)
                c.gate.on_change = self._emit_sync
                c.gate.on_off_air_cleared = lambda g=c.gate: self._emit_cleared(g)
            row.title.setText(f"{i + 1} · {c.cfg.name}:")
        pos = 0
        for gate, _i in sorted(live.items(), key=lambda kv: kv[1]):
            self._lay.insertWidget(pos, self._rows[gate])
            pos += 1
        self.empty_label.setVisible(not self._rows)
        self._show_all()

    def _emit_sync(self) -> None:  # любой поток
        try:
            self._sync.emit()
        except RuntimeError:  # окно уже закрыто
            pass

    def _emit_cleared(self, gate: AgentGate) -> None:  # любой поток
        try:
            self._off_air_cleared.emit(gate)
        except RuntimeError:
            pass

    def _show_all(self) -> None:
        for row in self._rows.values():
            row.show_gate()
        any_allowed = any(g.control_allowed for g in self._rows)
        if any_allowed != self._any:
            self._any = any_allowed
            self.anyAllowedChanged.emit(any_allowed)

    # --- выбранная клетка ---------------------------------------------------

    def _selected_row(self) -> _Row:
        g = self._grid
        sel = g.selected if g else None
        cell = g.cells[sel] if sel is not None else None
        return self._rows.get(cell.gate, self._null) if cell is not None else self._null

    @property
    def gate(self) -> AgentGate:
        return self._selected_row().gate

    @property
    def allow_check(self) -> QCheckBox:
        return self._selected_row().allow_check

    @property
    def off_air_check(self) -> QCheckBox:
        return self._selected_row().off_air_check

    @property
    def control_allowed(self) -> bool:
        return self.gate.control_allowed

    @control_allowed.setter
    def control_allowed(self, on: bool) -> None:
        self.gate.control_allowed = bool(on)  # как revoke() из чужого потока: без перерисовки

    @property
    def not_on_air(self) -> bool:
        return self.gate.not_on_air

    def revoke(self) -> None:
        """Снять оба разрешения выбранной камеры. Снимок меняется сразу, флажки — в потоке Qt."""
        self.gate.revoke()

    def revoke_all(self) -> None:
        """Снять разрешения у всех камер (Esc, аварийный стоп)."""
        for gate in list(self._rows):
            gate.revoke()

    def set_api_status(self, text: str, error: bool = False) -> None:
        self.api_label.setText(text)
        self.api_label.setStyleSheet(f"color: {C.FAULT if error else C.TEXT_DIM};")
