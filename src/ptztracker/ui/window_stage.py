"""Область сцены в окне: пункт меню клетки, настройка мышью на видео, сохранение в настройки камеры.

Макет — docs/design/mockups/stage-area.html; спека — docs/superpowers/specs/2026-11-13-stage-area-design.md.
Настройка камере ничего не шлёт и слежение не выключает.
"""

from __future__ import annotations

import dataclasses
import logging

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from ptztracker.control import stage_area as sa
from ptztracker.ui.theme import FS_CAPTION, SP, C, qcolor

log = logging.getLogger(__name__)

STAGE_TICK_MS = 200
BAR_MARGIN = 44  # плашка — над строкой подсказок внизу видео
BAR_SIDE = 8  # и не шире картинки: не помещается — подсказка «тяните» уходит, остаётся счёт людей


class StageEditBar(QFrame):
    """Плашка поверх видео при настройке области: подсказка и три кнопки."""

    done = Signal()
    switch_off = Signal()
    cancel = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("stageBar")
        bg = qcolor(C.BG, 224)
        self.setStyleSheet(f"QFrame#stageBar {{ background: rgba({bg.red()}, {bg.green()}, {bg.blue()}, {bg.alpha()});"
                           f" border-radius: 7px; }}"
                           f" QPushButton {{ font-size: {FS_CAPTION}px; padding: 3px 10px; min-height: 0; }}"
                           f" QLabel {{ color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px; background: transparent; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(SP[1], SP[0], SP[0], SP[0])
        row.setSpacing(SP[1])
        self.label = QLabel("Жду положение камеры…")
        self.done_btn = QPushButton("Готово")
        self.done_btn.setProperty("variant", "primary")
        self.off_btn = QPushButton("Выключить область")
        self.cancel_btn = QPushButton("Отмена")
        self.cancel_btn.setProperty("variant", "ghost")
        row.addWidget(self.label)
        for b, sig in ((self.done_btn, self.done), (self.off_btn, self.switch_off), (self.cancel_btn, self.cancel)):
            b.clicked.connect(sig)
            row.addWidget(b)
        self.hide()


def stage_state_text(area: sa.StageArea | None) -> str:
    return "не задана" if area is None else "задана" if area.on else "выключена"


class StageMixin:
    """Часть MainWindow: self.grid, self.cells, self.selected_cell, self._save — из других частей окна."""

    def _init_stage(self) -> None:
        self._stage_edit = None  # (клетка, сессия, область до настройки) — пока идёт настройка
        self.stage_bar = StageEditBar(self)
        self.stage_bar.done.connect(lambda: self.finish_stage_edit("done"))
        self.stage_bar.switch_off.connect(lambda: self.finish_stage_edit("off"))
        self.stage_bar.cancel.connect(lambda: self.finish_stage_edit("cancel"))
        self._stage_timer = QTimer(self)
        self._stage_timer.setInterval(STAGE_TICK_MS)
        self._stage_timer.timeout.connect(self._stage_tick)

    def stage_action_text(self) -> str:
        cell = self.selected_cell
        return f"Область сцены… — {stage_state_text(cell.session.stage_area if cell is not None else None)}"

    def start_stage_edit(self) -> None:
        slot, cell = self.grid.selected, self.selected_cell
        if cell is None or slot is None or self._stage_edit is not None:
            return
        s = cell.session
        self._stage_edit = (slot, s, s.stage_area)
        if s.follower is not None:
            s.follower.stage_on = True  # на время настройки положение камеры нужно и без записи и слежения
        preview = self.cells[slot].preview
        self.stage_bar.setParent(preview)
        self.stage_bar.label.setText("Жду положение камеры…")
        if s.stage_area is not None:  # правим прежнюю рамку (выключенную «Готово» включит)
            preview.begin_stage_edit(dataclasses.replace(s.stage_area, on=True))
        self.stage_bar.show()
        self._stage_timer.start()
        self._stage_tick()

    def _stage_tick(self) -> None:
        if self._stage_edit is None:
            return
        slot, s, _ = self._stage_edit
        cell = self.grid.cells[slot]
        if cell is None or cell.session is not s:  # камеру в клетке сменили — править нечего
            self.finish_stage_edit("cancel")
            return
        preview = self.cells[slot].preview
        if preview.stage_draft is None:
            view = s.stage_view(preview._frame_t)
            if view is None:
                s.poll_position()
            else:
                preview.begin_stage_edit(sa.default_area(view))
        n = preview.stage_counts() if preview.stage_draft is not None else None
        r = preview._image_rect() or preview.rect()
        if preview.stage_draft is not None:
            count = "" if n is None else f"в области {n[0]} из {n[1]} человек"
            for text in (" · ".join(t for t in ("Тяните за край рамки", count) if t), count or "Тяните за край"):
                self.stage_bar.label.setText(text)
                self.stage_bar.adjustSize()
                if self.stage_bar.width() <= r.width() - 2 * BAR_SIDE:
                    break
        self.stage_bar.adjustSize()
        self.stage_bar.move(int(r.center().x() - self.stage_bar.width() / 2),
                            int(r.bottom() - self.stage_bar.height() - BAR_MARGIN))
        self.stage_bar.raise_()

    def finish_stage_edit(self, how: str) -> None:
        """how: "done" — сохранить и включить, "off" — сохранить выключенной, "cancel" — оставить как было."""
        if self._stage_edit is None:
            return
        slot, s, before = self._stage_edit
        self._stage_edit = None
        self._stage_timer.stop()
        preview = self.cells[slot].preview
        draft = preview.stage_draft
        preview.end_stage_edit()
        self.stage_bar.hide()
        area = before
        if how == "done" and draft is not None:
            area = dataclasses.replace(draft, on=True)
        elif how == "off" and (draft is not None or before is not None):
            area = dataclasses.replace(draft if draft is not None else before, on=False)
        try:
            s.set_stage_area(area)
        except Exception:
            log.exception("Область сцены не применена")
        if how != "cancel":
            self._save()
