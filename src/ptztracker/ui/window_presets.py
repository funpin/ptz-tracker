"""Пресеты: выдвижная панель, вызов, запись и снимки."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox, QWidget

from ptztracker.ui.presets_drawer import DrawerRow


class PresetsMixin:
    """Часть MainWindow (ui/main_window.py): состояние окна — в его __init__."""

    def _show_presets(self, on: bool) -> None:
        """Панель пресетов не отнимает место у камер: окно растёт вниз на её высоту (и обратно)."""
        self.presets_drawer.setVisible(on)
        if on:
            self._refresh_drawer()
        self._fit_drawer_height()

    def _drawer_extra(self) -> int:
        d = self.presets_drawer
        return d.sizeHint().height() + self.centralWidget().findChild(QWidget, "leftCol").layout().spacing() \
            if d.isVisibleTo(self) else 0

    def _fit_drawer_height(self) -> None:
        """Панель пресетов растит окно вниз, но не за экран: что не влезло — панель делит место с камерами."""
        grown = getattr(self, "_drawer_grown", 0)
        if self.isMaximized() or self.isFullScreen():
            self._drawer_grown = 0  # во весь экран расти некуда
            return
        self.presets_drawer.adjustSize()
        base = self.height() - grown
        target = base + self._drawer_extra()
        scr = self.screen().availableGeometry() if self.screen() else None
        if scr is not None:
            frame = self.frameGeometry().height() - self.height()
            target = max(base, min(target, scr.height() - frame))
        self._drawer_grown = target - base
        if target != self.height():
            self.resize(self.width(), target)
            if scr is not None and self.frameGeometry().bottom() > scr.bottom():
                self.move(self.x(), max(scr.top(), scr.bottom() - self.frameGeometry().height() + 1))

    def _thumbs_of(self, cam_id: str) -> dict:
        if cam_id not in self._thumb_cache:
            self._thumb_cache[cam_id] = self.thumbs.load(cam_id)
        return self._thumb_cache[cam_id]

    def _preset_rows(self) -> list[int]:
        """Строки панели пресетов сверху вниз: выбранные кнопками камер (config.preset_rows); пока не выбирали —
        только выбранная камера. Выбор другой камеры порядок не меняет."""
        cells = self.grid.cells
        sel = self.grid.selected
        if self.single_view and sel is not None and cells[sel] is not None:
            return [sel]  # одна камера крупно — её пресеты (за ней переключается и главный геймпад)
        rows = [i for i in self.cfg.preset_rows if 0 <= i < len(cells) and cells[i] is not None]
        if rows:
            return rows
        if sel is not None and cells[sel] is not None:
            return [sel]
        return [i for i, c in enumerate(cells) if c is not None][:1]

    def _on_preset_rows(self, order: list[int]) -> None:
        self.cfg.preset_rows = list(order)
        self._save()
        self._refresh_drawer()
        self._fit_drawer_height()

    def _refresh_drawer(self) -> None:
        if not hasattr(self, "presets_drawer") or self.presets_drawer.isHidden():
            return
        sel = self.grid.selected
        order = self._preset_rows()
        rows = []
        for i in order:
            c = self.grid.cells[i]
            rows.append(DrawerRow(i, c.cfg.name, self._tally_per[i][1], dict(self._thumbs_of(c.cfg.id)),
                                  self._last_preset.get(i)))
        self.presets_drawer.set_cameras([(i, c.cfg.name) for i, c in enumerate(self.grid.cells) if c is not None],
                                        order)
        self.presets_drawer.set_rows(rows, selected=sel)
        s = self.session
        self.presets_drawer.set_speed(s.image.preset_speed if s is not None else None)

    def recall_preset(self, slot: int, n: int) -> None:
        """Пресет n (1…) камеры в клетке: клетка выбирается, слежение и агент этой камеры выключаются
        до команды (ручное действие сессии)."""
        c = self.grid.cells[slot]
        if c is None:
            return
        if slot != self.grid.selected:
            self.select_slot(slot)
        c.session.recall_preset(n - 1)
        self._after_recall(slot, n)

    def _after_recall(self, slot: int, n: int) -> None:
        self._last_preset[slot] = n
        c = self.grid.cells[slot]
        if c is not None and n not in self._thumbs_of(c.cfg.id):
            self._thumb_watch[slot] = (n, 0, None)  # снимок — когда камера доедет (или через 5 с)
        self._refresh_drawer()

    def store_preset(self, slot: int, n: int) -> None:
        c = self.grid.cells[slot]
        if c is None:
            return
        if slot != self.grid.selected:
            self.select_slot(slot)
        if QMessageBox.question(self, "Пресет", f"Записать текущее положение камеры {c.cfg.name} в пресет {n}?") \
                != QMessageBox.Yes:
            return
        c.session.store_preset(n - 1)
        self._last_preset[slot] = n
        self._capture_thumb_soon(slot, n)

    def _capture_thumb_soon(self, slot: int, n: int) -> None:
        c = self.grid.cells[slot]
        cam_id = c.cfg.id if c is not None else None
        QTimer.singleShot(300, lambda: self._capture_thumb(slot, n, cam_id))

    def _capture_thumb(self, slot: int, n: int, cam_id: str | None = None) -> None:
        c = self.grid.cells[slot]
        if c is not None and cam_id is not None and c.cfg.id != cam_id:
            return  # за 0,3 с камеру в клетке сменили — снимок не её
        frame = c.session.frames.latest() if c is not None else None
        if frame is None:
            return
        ptz = c.session.ptz
        self.thumbs.save(c.cfg.id, n, frame.image, ptz.pan_deg if ptz is not None else None)
        self._thumb_cache.pop(c.cfg.id, None)
        self._refresh_drawer()

    def _watch_thumbs(self) -> None:
        """После вызова пресета без снимка: снять, когда положение два опроса подряд не меняется, или через 5 с."""
        for slot, (n, ticks, last) in list(self._thumb_watch.items()):
            c = self.grid.cells[slot]
            if c is None:
                self._thumb_watch.pop(slot)
                continue
            c.session.poll_position()
            p = c.session.ptz
            pos = (round(p.pan_deg, 1), round(p.tilt_deg, 1), p.zoom_x) if p is not None else None
            if (ticks >= 2 and pos is not None and pos == last) or ticks >= 10:
                self._thumb_watch.pop(slot)
                self._capture_thumb(slot, n)
            else:
                self._thumb_watch[slot] = (n, ticks + 1, pos)

    def _erase_preset(self, slot: int, n: int) -> None:
        c = self.grid.cells[slot]
        if c is None:
            return
        self.thumbs.erase(c.cfg.id, n)
        self._thumb_cache.pop(c.cfg.id, None)
        self._refresh_drawer()
