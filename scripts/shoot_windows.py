"""Снимки всех окон программы на симуляторе при 1640×850 — для проверки дизайна перед слиянием.

Запуск на Mac: python scripts/shoot_windows.py [папка] — ~1 мин, камеры не нужны (две виртуальные).
Модальные диалоги открываются без ожидания (exec подменён на show), меню — popup."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QWidget

from ptztracker.config import ConfigStore
from ptztracker.ui.camera_dialog import CameraDialog
from ptztracker.ui.main_window import MainWindow
from ptztracker.ui.theme import apply_dark_theme, install_russian

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "design" / "final-1640"
EPISODE = ROOT / "tests" / "data" / "episodes" / "2026-10-29-r1-waist.zip"


def main() -> None:
    app = QApplication([])
    install_russian(app)
    apply_dark_theme(app)
    QDialog.exec = lambda self: (self.show(), 0)[1]  # не ждать закрытия
    OUT.mkdir(parents=True, exist_ok=True)
    w = MainWindow(ConfigStore(Path(tempfile.mkdtemp()) / "c.json"), use_sim=True)
    w.resize(1640, 850)
    w.show()
    opened: list[QWidget] = []

    def save(name: str, widget: QWidget | None = None) -> None:
        app.processEvents()
        (widget or w).grab().save(str(OUT / f"{name}.png"))
        print("saved", name)

    before: set = set()

    def visible_tops() -> set:
        return {t for t in app.topLevelWidgets() if t.isVisible() and t is not w and not isinstance(t, QMenu)}

    def top_new() -> QWidget | None:
        new = list(visible_tops() - before)
        opened.extend(new)
        return new[0] if new else None

    def close_new() -> None:
        for t in opened:
            t.close()
        opened.clear()

    def menu(m: QMenu, name: str, at: QWidget) -> None:
        m.popup(at.mapToGlobal(QPoint(0, at.height())))
        QTimer.singleShot(400, lambda: (save(name, m), m.close()))

    def dialog(fn, name: str) -> None:
        before.clear()
        before.update(visible_tops())
        fn()
        QTimer.singleShot(700, lambda: (save(name, top_new()), close_new()))

    steps = [
        (4000, lambda: save("01-grid")),
        (0, lambda: w.presets_btn.setChecked(True)),
        (2000, lambda: save("02-grid-presets")),
        (0, lambda: w.presets_btn.setChecked(False)),
        (0, lambda: w.set_single_view(True)),
        (1200, lambda: save("03-single")),
        (0, lambda: w.presets_btn.setChecked(True)),
        (2000, lambda: save("04-single-presets")),
        (0, lambda: (w.presets_btn.setChecked(False), w.set_single_view(False))),
        (900, lambda: w.enter_mouse_mode()),
        (700, lambda: save("05-mouse-mode")),
        (0, lambda: w.exit_mouse_mode()),
        (300, lambda: w.cells[0].video_frame and (w._menu_to(w.cells[0].video_frame), w.camera_menu.open())),
        (700, lambda: save("06-quick-menu")),
        (0, lambda: w.camera_menu.close_menu()),
        (300, lambda: menu(w.main_menu_btn.menu(), "07-main-menu", w.main_menu_btn)),
        (800, lambda: menu(w.cells[0].more_btn.menu(), "08-cell-menu", w.cells[0].more_btn)),
        (800, lambda: dialog(w.show_hotkeys, "09-help")),
        (1000, lambda: dialog(w.show_gamepads, "10-gamepads")),
        (1000, lambda: dialog(w._show_agent_window, "11-agent")),
        (1000, lambda: dialog(w._edit_atem, "12-atem")),
        (1000, lambda: dialog(lambda: CameraDialog(w._current_cfg(), parent=w).show(), "13-camera")),
        (1000, lambda: dialog(w._add_camera, "14-new-camera")),
        (1000, lambda: dialog(w._recording_settings, "15-recording")),
        (1000, lambda: dialog(w._run_calibration, "16-calibration")),
        (1000, lambda: dialog(lambda: w.open_replay(EPISODE), "17-replay")),
        (1500, lambda: (w.close(), app.quit())),
    ]
    def run(i: int = 0) -> None:  # по очереди: следующий шаг — после предыдущего, а не по общим часам
        if i < len(steps):
            delay, fn = steps[i]
            QTimer.singleShot(max(delay, 300), lambda: (fn(), run(i + 1)))

    run()
    app.exec()


if __name__ == "__main__":
    main()
