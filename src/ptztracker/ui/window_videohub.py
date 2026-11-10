"""Videohub в окне: выход хаба идёт вслед за выбранной камерой (спека 2026-11-10-videohub-follow-design.md).

Сеть — только в фоновом потоке клиента; здесь — решение и запись желаемого, значок из снимка по таймеру.
Любой сбой хаба не должен мешать выбору клетки, управлению камерами и стопам.
"""

from __future__ import annotations

import logging

from ptztracker.ui.hub_view import hub_menu_status, hub_pill
from ptztracker.ui.videohub_dialog import VideohubDialog
from ptztracker.videohub.client import VIDEOHUB_PORT, HubSnapshot, VideohubClient
from ptztracker.videohub.follow import HubFollower
from ptztracker.videohub.protocol import hub_input_of, hub_output_of

log = logging.getLogger(__name__)


class VideohubMixin:
    def _init_videohub(self, port: int = VIDEOHUB_PORT) -> None:
        self._hub_port = port
        self.hub: VideohubClient | None = None
        self._hub_follower = HubFollower()

    def _restart_videohub(self) -> None:
        """Остановить старый клиент и создать новый из cfg.videohub_ip (пусто — хаб выключен)."""
        if self.hub is not None:
            self.hub.stop()
            self.hub = None
        if self.cfg.videohub_ip:
            self.hub = VideohubClient(self.cfg.videohub_ip, port=self._hub_port)
            self.hub.start()
        self._sync_hub()

    def _stop_videohub(self) -> None:
        if self.hub is not None:
            self.hub.stop()
            self.hub = None

    def _hub_snapshot(self) -> HubSnapshot:
        return self.hub.snapshot() if self.hub is not None else HubSnapshot()

    def _hub_follow(self) -> None:
        """Выбранная камера могла смениться: решить и, если нужно, отдать хабу (не ждёт сеть)."""
        try:
            cam = self._current_cfg()
            out = hub_output_of(self.cfg)
            inp = hub_input_of(cam) if cam is not None else 0
            r = self._hub_follower.selected(cam.id if cam else None, inp, out, self.cfg.videohub_follow,
                                            self._hub_snapshot())
            if r.action == "route" and self.hub is not None:
                self.hub.route(r.output, r.input)
            elif r.reason in ("offline", "locked"):
                log.info("Videohub: выход %d не переключён (%s)", out, r.reason)
        except Exception:
            log.exception("Videohub: сбой при смене камеры")  # хаб не должен ломать выбор клетки

    def _hub_cameras(self) -> list[tuple[str, int]]:
        """(имя, вход хаба) камер сетки — выбранная первой: её имя важнее при одинаковом входе."""
        cells = [c for c in self.grid.cells if c is not None]
        sel = self.selected_cell
        cells.sort(key=lambda c: c is not sel)
        return [(c.cfg.name, hub_input_of(c.cfg)) for c in cells]

    def _sync_hub(self) -> None:
        if not hasattr(self, "hub_pill"):
            return
        try:
            pill = hub_pill(bool(self.cfg.videohub_ip), self._hub_snapshot(), hub_output_of(self.cfg),
                            self._hub_cameras())
        except Exception:
            log.exception("Videohub: значок не обновлён")
            return
        if pill is None:
            if not self.hub_pill.isHidden():
                self.hub_pill.hide()
                self._fit_top_bar()
            return
        was_hidden = self.hub_pill.isHidden()
        self.hub_pill.set(*pill)
        if was_hidden:
            self.hub_pill.show()
            self._fit_top_bar()

    def _videohub_menu_status(self) -> tuple[str, str]:
        return hub_menu_status(bool(self.cfg.videohub_ip), self._hub_snapshot(), self.cfg.videohub_ip)

    def _edit_videohub(self) -> None:
        cams = [c.cfg for c in self.grid.cells if c is not None]
        slots = [i + 1 for i, c in enumerate(self.grid.cells) if c is not None]
        d = VideohubDialog(self.cfg.videohub_ip, hub_output_of(self.cfg), self.cfg.videohub_follow,
                           list(zip(slots, cams)), self, snapshot=self._hub_snapshot)
        if not d.exec():
            return
        old_ip = self.cfg.videohub_ip
        self.cfg.videohub_ip, self.cfg.videohub_output, self.cfg.videohub_follow = d.values()
        for cam, inp in d.camera_inputs():
            cam.videohub_input = inp
        self._save()
        if self.cfg.videohub_ip != old_ip:
            self._restart_videohub()
        self._sync_hub()
