"""Панель «Агент»: оператор разрешает программе-агенту (на этом же компьютере) управлять слежением.

У каждой открытой камеры свои два флажка (`AgentGate` клетки) и своя строка в панели. Оба по умолчанию
выключены и не сохраняются. Esc снимает все флажки; ручное действие, выключение слежения оператором, смена
камеры, раскачка и аварийный стоп — флажки этой камеры. `AgentGate.revoke()` можно звать из любого потока.
Снятый «НЕ в эфире» выключает слежение, если его включил агент (`WindowBackend`).

Свойства `control_allowed`, `not_on_air`, `allow_check`, `off_air_check`, `revoke()` относятся к выбранной клетке.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ptztracker.multicam.agent import AgentGate
from ptztracker.ui.theme import FS_BUTTON, FS_CAPTION, R_PANEL, R_TAG, SP, C
from ptztracker.ui.widgets import BrandMark, SectionHeader, ToggleCheck

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


TALLY_WARN = {
    "pvw": "Камера на PVW — следующей уйдёт в эфир. Перед «НЕ в эфире» проверьте микшер.",
    "pgm": "В эфире! Камера на PGM — проверьте, правда ли она не в эфире.",
}


class _Row(QFrame):
    """Карточка камеры (макет docs/design/mockups/agent.html): номер цветом эфира, имя, что может агент,
    два тумблера; подсказка про эфир — только словами (Tally ничего не блокирует, решение от 2026-10-22)."""

    def __init__(self, gate: AgentGate):
        super().__init__()
        self.gate = gate
        self.tally: str | None = None
        self.live = False
        self.setObjectName("agentCard")
        self.num_label = QLabel("")
        self.num_label.setFixedSize(18, 18)
        self.num_label.setAlignment(Qt.AlignCenter)
        self.title = QLabel("")
        self.title.setStyleSheet("font-weight: 600; background: transparent;")
        self.tally_chip = QLabel("")
        self.tally_chip.hide()
        self.state_label = QLabel("")
        self.allow_check = ToggleCheck("Разрешить управление агенту")
        self.off_air_check = ToggleCheck("Камера НЕ в эфире")
        for c in (self.allow_check, self.off_air_check):
            c.setFocusPolicy(Qt.NoFocus)  # пробел и стрелки обрабатывает окно
        self.allow_check.setToolTip(ALLOW_TIP)
        self.off_air_check.setToolTip(OFF_AIR_TIP)
        self.warn_label = QLabel("")
        self.warn_label.setWordWrap(True)
        self.warn_label.setStyleSheet(f"color: {C.LOST}; font-size: {FS_CAPTION}px; background: transparent;")
        self.warn_label.hide()
        head = QHBoxLayout()
        head.setSpacing(SP[1])
        for w in (self.num_label, self.title, self.tally_chip):
            head.addWidget(w)
        head.addStretch(1)
        head.addWidget(self.state_label)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SP[2], SP[1] + 2, SP[2], SP[1] + 2)
        lay.setSpacing(SP[1])
        lay.addLayout(head)
        lay.addWidget(self.allow_check)
        lay.addWidget(self.off_air_check)
        lay.addWidget(self.warn_label)
        self.allow_check.toggled.connect(gate.set_allowed)
        self.off_air_check.toggled.connect(gate.set_off_air)
        self.allow_check.toggled.connect(lambda _on: self._restyle())
        self.off_air_check.toggled.connect(lambda _on: self._restyle())
        self._restyle()

    def set_tally(self, tally: str | None) -> None:
        if tally != self.tally:
            self.tally = tally
            self._restyle()

    def show_gate(self) -> None:
        """Флажки на экране — как в снимке (без повторной записи в снимок)."""
        for check, on in ((self.allow_check, self.gate.control_allowed), (self.off_air_check, self.gate.not_on_air)):
            if check.isChecked() != on:
                check.blockSignals(True)
                check.setChecked(on)
                check.blockSignals(False)
        self._restyle()

    def _restyle(self) -> None:
        allowed, off_air = self.allow_check.isChecked(), self.off_air_check.isChecked()
        self.live = allowed and off_air
        text = "может двигать камеру" if self.live else "только выключить слежение" if allowed else "агенту нельзя"
        self.state_label.setText(text)
        self.state_label.setStyleSheet(f"color: {C.LIME if self.live else C.TEXT_FAINT}; font-size: {FS_CAPTION}px;"
                                       " background: transparent;")
        self.setStyleSheet(f"QFrame#agentCard {{ background: {C.SURFACE}; border: 1px solid"
                           f" {C.LIME if self.live else C.LINE}; border-radius: {R_PANEL}px; }}")
        bg, fg = {"pgm": (C.PGM, C.TEXT), "pvw": (C.PVW, C.BG)}.get(self.tally or "", (C.RAISED_HI, C.TEXT))
        self.num_label.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: {R_TAG + 1}px;"
                                     f" font-weight: 700; font-size: {FS_CAPTION - 1}px;")
        self.tally_chip.setVisible(self.tally is not None)
        if self.tally:
            col = C.PGM if self.tally == "pgm" else C.PVW
            self.tally_chip.setText(self.tally.upper())
            self.tally_chip.setStyleSheet(f"color: {col}; border: 1px solid {col}; border-radius: {R_TAG}px;"
                                          f" padding: 0 5px; font-size: {FS_CAPTION - 1}px; font-weight: 700;"
                                          " background: transparent;")
        warn = TALLY_WARN.get(self.tally or "") if (allowed or off_air) else None
        self.warn_label.setText(warn or "")
        self.warn_label.setVisible(bool(warn))


class AgentPanel(QWidget):
    _sync = Signal()  # флажки сменились в любом потоке — перерисовать в потоке Qt
    _off_air_cleared = Signal(object)
    offAirCleared = Signal(object)  # AgentGate: «НЕ в эфире» снят (оператором или revoke) — поток Qt
    anyAllowedChanged = Signal(bool)  # хоть у одной камеры «Разрешить» — значок AGENT

    def __init__(self, parent=None):
        super().__init__(parent)
        self._grid = None
        self._rows: dict[AgentGate, _Row] = {}
        self._null = _Row(AgentGate())  # выбранной камеры нет: флажки-пустышки
        self._tally: dict[int, str | None] = {}
        title = QLabel("Агент")
        title.setStyleSheet(f"font-size: {FS_BUTTON}px; font-weight: 700;")
        self.badge = QLabel("AGENT")
        self.badge.setStyleSheet(f"background: {C.LIME}; color: {C.BG}; border-radius: {R_TAG}px; padding: 1px 6px;"
                                 f" font-size: {FS_CAPTION - 1}px; font-weight: 700;")
        self.badge.hide()
        head = QHBoxLayout()
        head.setSpacing(SP[2])
        head.addWidget(BrandMark(22))
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.badge)
        intro = QLabel("Разрешения — у каждой камеры свои и не сохраняются после перезапуска. Esc, ручное управление"
                       " камерой и аварийный стоп снимают их сразу.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
        self.empty_label = QLabel("Нет открытых камер.")
        self.empty_label.setStyleSheet(f"color: {C.TEXT_DIM};")
        self._cards = QVBoxLayout()
        self._cards.setSpacing(SP[1])
        self._cards.addWidget(self.empty_label)
        self.api_dot = QLabel()
        self.api_dot.setFixedSize(8, 8)
        self.api_label = QLabel("")
        self.api_label.setWordWrap(True)
        api = QFrame()
        api.setObjectName("agentApi")
        api.setStyleSheet(f"QFrame#agentApi {{ background: {C.SURFACE}; border: 1px solid {C.LINE};"
                          f" border-radius: {R_PANEL}px; }}")
        al = QHBoxLayout(api)
        al.setContentsMargins(SP[2], SP[1] + 1, SP[2], SP[1] + 1)
        al.setSpacing(SP[1])
        al.addWidget(self.api_dot)
        al.addWidget(self.api_label, 1)
        self.revoke_button = QPushButton("Снять все разрешения")
        self.revoke_button.clicked.connect(self.revoke_all)
        self.done_button = QPushButton("Готово")
        self.done_button.setProperty("variant", "primary")
        self.done_button.clicked.connect(lambda: self.window().close() if self.window() is not self else None)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(self.revoke_button)
        foot.addWidget(self.done_button)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(SP[2], SP[2], SP[2], SP[2])
        self._lay.setSpacing(SP[1])
        self._lay.addLayout(head)
        self._lay.addWidget(intro)
        self._lay.addWidget(SectionHeader("Камеры"))
        self._lay.addLayout(self._cards)
        self._lay.addWidget(SectionHeader("Связь"))
        self._lay.addWidget(api)
        self._lay.addStretch(1)
        self._lay.addLayout(foot)
        self.set_api_status("")
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
            self._cards.removeWidget(row)
            row.deleteLater()
        for i, c in enumerate(cells):
            if c is None:
                continue
            row = self._rows.get(c.gate)
            if row is None:
                row = self._rows[c.gate] = _Row(c.gate)
                c.gate.on_change = self._emit_sync
                c.gate.on_off_air_cleared = lambda g=c.gate: self._emit_cleared(g)
            row.num_label.setText(str(i + 1))
            row.title.setText(c.cfg.name)
            row.set_tally(self._tally.get(i))
        pos = 0
        for gate, _i in sorted(live.items(), key=lambda kv: kv[1]):
            self._cards.insertWidget(pos, self._rows[gate])
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
        self.badge.setVisible(any_allowed)
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

    def set_tally(self, slot: int, tally: str | None) -> None:
        """Эфир камеры в клетке (pgm | pvw | None) — плитка номера и подсказка; ничего не блокирует."""
        if self._tally.get(slot) == tally:
            return
        self._tally[slot] = tally
        cells = list(self._grid.cells) if self._grid else []
        if slot < len(cells) and cells[slot] is not None and cells[slot].gate in self._rows:
            self._rows[cells[slot].gate].set_tally(tally)

    def set_api_status(self, text: str, error: bool = False) -> None:
        """Связь — одна строка: точка лайм — API работает, красная — ошибка с причиной."""
        self.api_label.setText(text or "API выключен")
        self.api_label.setStyleSheet(f"color: {C.FAULT if error else C.TEXT_DIM}; font-size: {FS_CAPTION}px;"
                                     " background: transparent;")
        self.api_dot.setProperty("error", bool(error))
        col = C.FAULT if error else C.LIME if text else C.GREY
        self.api_dot.setStyleSheet(f"background: {col}; border-radius: 4px;")
