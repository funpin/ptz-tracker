"""Запись: журнал F9, просмотр записей, запись видео в файл."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QWidget

from ptztracker.camera.session import CameraSession

log = logging.getLogger(__name__)


class RecordingMixin:
    """Часть MainWindow (ui/main_window.py): состояние окна — в его __init__."""

    def save_moment(self, reason: str = "F9", slot: int | None = None):
        """F9: сохранить момент у каждой открытой камеры. Возвращает запись клетки `slot` (по умолчанию —
        выбранной или первой открытой)."""
        cells = [c for c in self.grid.cells if c is not None]
        if not cells:
            self.statusBar().showMessage("Нечего сохранять: камера не выбрана.", 5000)
            return None
        self.statusBar().showMessage("Сохраняю запись…")
        batch = {"left": len(cells), "paths": [], "errors": []}
        chosen = None
        want = self.grid.cells[slot] if slot is not None else self.selected_cell
        for c in cells:
            fut = c.session.save_moment(reason)
            if chosen is None or c is want:
                chosen = fut
            fut.add_done_callback(lambda f, b=batch: self._saveFinished.emit((b, f)))  # рабочий поток → сигнал
        return chosen

    def _on_save_finished(self, item) -> None:
        batch, fut = item
        batch["left"] -= 1
        try:
            batch["paths"].append(Path(fut.result()))
        except Exception as e:
            log.warning("Запись не сохранена: %s", e)
            batch["errors"].append(e)
        if batch["left"] > 0:
            return
        paths, errors = batch["paths"], batch["errors"]
        if len(paths) + len(errors) == 1:
            if errors:
                self.statusBar().showMessage(f"Запись не сохранена: {errors[0]}", 10000)
            else:
                self.statusBar().showMessage(f"Запись сохранена: {paths[0].name}", 10000)
        elif paths:
            bad = f", не удалось: {len(errors)}" if errors else ""
            self.statusBar().showMessage(f"Записей сохранено: {len(paths)}{bad}", 10000)
        else:
            self.statusBar().showMessage(f"Записи не сохранены: {errors[0]}", 10000)
        if paths:  # после showMessage: оно прячет временные виджеты строки состояния
            self._saved_path = paths[-1]
            self.show_folder_btn.show()
            self._hide_folder_timer.start(10000)

    def _show_saved_folder(self) -> None:
        if self._saved_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._saved_path.parent)))

    def _choose_replay(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from ptztracker.config import record_dir

        path, _ = QFileDialog.getOpenFileName(self, "Открыть запись", str(record_dir(self.cfg)), "Записи (*.zip)")
        if path:
            self.open_replay(Path(path))

    def open_replay(self, path: Path) -> QWidget | None:
        """Окно просмотра записи; камера и слежение не затрагиваются."""
        from ptztracker.recorder.reader import Episode
        from ptztracker.ui.replay_window import ReplayWindow

        try:
            # отдельное окно: клавиши в нём не управляют камерой (фильтр главного окна его не считает своим)
            w = ReplayWindow(Episode.open(path), parent=self, on_escape=self.emergency_stop,
                             on_open_other=self._choose_replay)
        except Exception as e:
            QMessageBox.warning(self, "Запись", f"Не удалось открыть запись {path.name}: {e}")
            return None
        w.setAttribute(Qt.WA_DeleteOnClose)
        w.show()
        return w

    def _video_settings(self) -> None:
        from ptztracker.ui.video_dialog import VideoDialog

        d = VideoDialog(self.cfg, parent=self)
        if d.exec():
            d.apply()
            self._save()
            self.video.disk.refresh()

    def _recording_settings(self) -> None:
        from ptztracker.ui.recording_dialog import RecordingDialog

        d = RecordingDialog(self.cfg, parent=self)
        if d.exec():
            d.apply()
            self._save()

    def _video_sessions(self) -> list[tuple[int, CameraSession]]:
        return [(i, c.session) for i, c in enumerate(self.grid.cells) if c is not None]

    def toggle_video(self, slot: int) -> None:
        c = self.grid.cells[slot]
        if c is None:
            return
        refusal = self.video.toggle(c.session)
        if refusal:
            self.statusBar().showMessage(refusal, 8000, error=True)
        self._refresh_video()

    def stop_video_slot(self, slot: int) -> None:
        self.video.stop_slot(slot)
        self._refresh_video()

    def stop_all_video(self) -> None:
        self.video.stop_all()
        self._refresh_video()

    def _open_video_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.video.current_folder())))

    def _refresh_video(self) -> None:
        for i, cell in enumerate(self.cells):
            c = self.grid.cells[i]
            cell.preview.set_rec(c.session.video_state() if c else None)
        summary, messages = self.video.tick()
        self.rec_pill.set_summary(summary)
        for m in messages:
            self.statusBar().showMessage(m, 8000, error=True)

    def _confirm_close_with_video(self) -> bool:
        ans = QMessageBox.question(self, "Запись видео", "Идёт запись видео. Остановить её и выйти?")
        return ans == QMessageBox.Yes
