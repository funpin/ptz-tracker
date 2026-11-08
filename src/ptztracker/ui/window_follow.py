"""Слежение и кадр: включение, композиция, зоны, команды агента."""

from __future__ import annotations

from typing import Callable

from ptztracker.camera.session import CameraSession
from ptztracker.control.zones import Zone, framing, zone_of
from ptztracker.multicam.analysis import tracking_refusal
from ptztracker.multicam.grid import Cell
from ptztracker.ui.agent_panel import revokes_agent
from ptztracker.ui.camera_cell import CameraCell
from ptztracker.ui.state import TrackingView, tracking_view


class FollowMixin:
    """Часть MainWindow (ui/main_window.py): состояние окна — в его __init__."""

    def _follow_cb(self, session: CameraSession) -> Callable[[bool, str | None], None]:
        def on_change(on: bool, reason: str | None) -> None:
            """Follower.on_change (из любого потока): раскачка/аварийный стоп снимают агента; вибрация геймпада."""
            try:
                if revokes_agent(on, reason):  # раскачка или аварийный стоп — разрешения этой камеры
                    cell = next((c for c in self.grid.cells if c is not None and c.session is session), None)
                    if cell is not None:
                        cell.gate.revoke()
            finally:
                for n, b in list(self.pads.items()):
                    if session is self._pad_session(n):
                        b.notify_follow(on, reason)
        return on_change

    def _follower(self):
        return self.session.follower if self.session else None

    def _on_follow_requested(self, want: bool, by_agent: bool = False) -> None:
        if not by_agent:
            if not want:  # оператор забрал камеру — как Esc: агент теряет разрешение до следующей команды
                self.agent_panel.revoke()
            if self.on_operator_follow:
                self.on_operator_follow(want)
        if want and self.selected_cell is not None:
            if self.selected_cell.session.analyzer is None and self.selected_cell.cfg.tracking:
                self.request_analysis(self.grid.selected)  # слежение здесь — значит и анализ здесь
            refusal = tracking_refusal(self._states(), self.grid.selected, self.cfg.max_analyzed)
            if refusal is not None:
                self.follow_panel.set_state(False, None)
                self.follow_panel.show_hint(refusal)
                self._view_shown = self._current_view()  # подсказка остаётся, пока вид не изменится сам
                return
        f = self._follower()
        if f is None:
            self.follow_panel.set_state(False, "распознавание недоступно")
            self._view_shown = self._current_view()  # сообщение остаётся, пока вид не изменится сам
            return
        if want:
            if not f.enable():
                v = self._current_view()
                if v.kind == "swing":
                    self.follow_panel.set_view(v)  # раскачка важнее подсказки: плитка и «Калибровка…» остаются
                else:
                    self.follow_panel.set_state(False, None)
                    self.follow_panel.show_hint("Сначала кликните по человеку на видео.")
                # подсказка остаётся, пока вид не изменится сам
                self._view_shown = v
        else:
            f.disable("выключено оператором")
        self._sync_follow()

    def request_follow(self, on: bool, slot: int | None = None) -> bool:
        """Включить/выключить слежение тем же путём, что пробел. Возвращает, включено ли оно теперь.

        `slot` — клетка не выбранной камеры: там панель не меняется, проверки лимита те же."""
        if slot is None or slot == self.grid.selected:
            self._on_follow_requested(on, by_agent=True)
            f = self._follower()
            return bool(f and f.enabled)
        c = self.grid.cells[slot]
        f = c.session.follower if c else None
        if f is None:
            return False
        if not on:
            f.disable("выключено агентом")
        elif tracking_refusal(self._states(), slot, self.cfg.max_analyzed) is None:
            f.enable()
        self._sync_follow()
        return f.enabled

    def apply_composition(self, key: str, slot: int | None = None) -> None:
        if slot is None or slot == self.grid.selected:
            self._on_composition(key)
            return
        c = self.grid.cells[slot]
        if c is None:
            return
        c.cfg.composition = key
        if c.cfg.kind != "sim":
            self._save()
        self._apply_framing(c.cfg, c.session.follower)

    def apply_effort(self, x: float, slot: int | None = None) -> None:
        if slot is None or slot == self.grid.selected:
            self.follow_panel.set_effort(x)
            self._on_effort(x)
            return
        c = self.grid.cells[slot]
        if c is None:
            return
        c.cfg.effort = x
        if c.cfg.kind != "sim":
            self._save()
        self._apply_framing(c.cfg, c.session.follower)

    def refresh_status(self) -> None:
        self._update_status()

    def _toggle_follow_slot(self, slot: int) -> None:
        """Квадратик камеры на TRACK: то же, что Пробел для этой камеры, выбор не меняется."""
        if slot == self.grid.selected:
            self.toggle_follow()
            return
        c = self.grid.cells[slot]
        f = c.session.follower if c else None
        if f is None:
            return
        if f.enabled:
            c.gate.revoke()  # оператор забрал камеру — как Esc: агент теряет разрешение этой камеры
            f.disable("выключено оператором")
        elif tracking_refusal(self._states(), slot, self.cfg.max_analyzed) is None:
            f.enable()
        self._sync_follow()

    def toggle_follow(self) -> None:
        f = self._follower()
        self._on_follow_requested(not (f and f.enabled))

    def _current_view(self, cell: Cell | None = None) -> TrackingView:
        cell = cell if cell is not None else self.selected_cell
        s = cell.session if cell else None
        f = s.follower if s else None
        a = s.analyzer if s else None
        r = a.latest() if a else None
        return tracking_view(
            bool(f and f.enabled),
            f.last_reason if f else None,
            r.target_id if r else None,
            r.target_state if r else None,
            diverged=bool(f and getattr(f.controller, "diverged", False)),
        )

    def _sync_follow(self) -> None:
        """Состояние слежения меняется и из других потоков (ручное управление, сбой анализа) — показать."""
        ts = frozenset(
            i for i, c in enumerate(self.grid.cells) if c and c.session.follower and c.session.follower.enabled
        )
        if ts != self._tracking_set:
            self._tracking_set = ts
            self._reallocate_analysis()  # слежение кончилось — место анализа свободно
        for i, c in enumerate(self.grid.cells):
            v = self._current_view(c) if c else None
            if v != self._tag_shown.get(i):
                self._tag_shown[i] = v
                if v is not None:
                    self.cells[i].video_frame.set_tracking_tag(v.tag, v.color)
        v = self._current_view()
        if v != self._view_shown:
            self._view_shown = v
            self.follow_panel.set_view(v)
        cams = []
        for i, c in enumerate(self.grid.cells):
            if c is not None and c.session.analyzer is not None:
                kind = {"on": "on", "lost": "lost"}.get(self._current_view(c).kind, "off")
                cams.append((i + 1, kind, i == self.grid.selected))
        self.track_btn.set_cams(cams)
        sel = self.selected_cell
        if sel is not None and sel.session.analyzer is None and self.track_btn.kind() != "none":
            self.track_btn.set_view("none", "", "У этой камеры нет анализа — ☰ → Камер с анализом",
                                    False)
            self._view_shown = None  # анализ появится — вид вернётся

    def _on_composition(self, key: str) -> None:
        if key.startswith("waist"):
            self._last_waist = key
        cam = self._current_cfg()
        self.follow_panel.set_composition(key)
        if cam is None:
            return
        cam.composition = key
        if self._is_stored(cam):
            self._save()
        self._apply_framing(cam, self._follower())

    def _on_effort(self, x: float) -> None:
        cam = self._current_cfg()
        if cam is None:
            return
        cam.effort = x
        if self._is_stored(cam):
            self._save()
        self._apply_framing(cam, self._follower())

    def _zone_scale(self, slot: int) -> float:
        g = self.grid.cells[slot]
        z = zone_of(g.cfg.zones, g.cfg.composition) if g else None
        return z.scale if z else 1.0

    def _on_zone(self, slot: int, zone: Zone | None, save: bool = False) -> None:
        """Оператор тянет зону на видео (None — двойной клик, сброс). Слежение не выключается:
        регулятор доводит камеру до новой точки плавно, как при смене крупности."""
        g = self.grid.cells[slot]
        if g is None:
            return
        if zone is None:
            g.cfg.zones.pop(g.cfg.composition, None)
        else:
            g.cfg.zones[g.cfg.composition] = zone.to_json()
        self._apply_framing(g.cfg, g.session.follower)
        if save:
            self._save_zone(slot)

    def _save_zone(self, slot: int) -> None:
        g = self.grid.cells[slot]
        if g is not None and self._is_stored(g.cfg):
            self._save()

    @staticmethod
    def _apply_framing(cam, follower) -> None:
        """Крупность, характер и своя зона этой крупности — в слежение камеры."""
        if follower is not None:
            follower.set_framing(*framing(cam.composition, cam.effort, cam.zones))

    def _reset_target(self, slot: int | None = None) -> None:
        c = self.grid.cells[self.grid.selected if slot is None else slot]
        if c and c.session.analyzer:
            c.session.analyzer.clear_target()
        self._update_status()

    def _on_skeleton(self, on: bool, cell: CameraCell | None = None) -> None:
        cell = cell or self._cell
        cell.preview.show_skeleton = on
        cell.preview.update()
