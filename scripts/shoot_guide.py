"""Картинки для руководства оператора (docs/OPERATOR.md): части окна на симуляторе.

Запуск на Mac: python scripts/shoot_guide.py — ~25 с, камеры не нужны (две виртуальные).
Камера 1 ведёт человека и стоит в эфире (PGM), камера 2 — в превью (PVW): ATEM подменён снимком,
сеть не нужна. Окна и меню снимаются целиком, остальное — вырезками из окна 1640×850.
Поменяли вид — перезапустите скрипт и проверьте картинки в руководстве."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QWidget

from ptztracker.config import CameraConfig, ConfigStore
from ptztracker.tally.atem import TALLY_PGM, TALLY_PVW, TallySnapshot
from ptztracker.ui.camera_dialog import CameraDialog
from ptztracker.ui.main_window import MainWindow
from ptztracker.ui.theme import apply_dark_theme, install_russian

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "assets" / "guide"
EPISODE = ROOT / "tests" / "data" / "episodes" / "sim_walk_30s.zip"


class FakeAtem:
    """Микшер без сети: камера 1 в эфире, камера 2 в превью."""

    def snapshot(self) -> TallySnapshot:
        return TallySnapshot(online=True, flags=(TALLY_PGM, TALLY_PVW))

    def stop(self) -> None:
        pass


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
    before: set = set()

    def save(name: str, widget: QWidget | None = None, rect: QRect | None = None) -> None:
        app.processEvents()
        w.statusBar().clearMessage()
        wd = widget or w
        (wd.grab(rect) if rect is not None else wd.grab()).save(str(OUT / f"{name}.png"))
        print("saved", name)

    def area(*widgets: QWidget, pad: int = 0) -> QRect:
        """Прямоугольник окна, в который входят все виджеты (с полями pad)."""
        r = QRect()
        for wd in widgets:
            r = r.united(QRect(wd.mapTo(w, QPoint(0, 0)), wd.size()))
        return r.adjusted(-pad, -pad, pad, pad).intersected(w.rect())

    def visible_tops() -> set:
        return {t for t in app.topLevelWidgets() if t.isVisible() and t is not w and not isinstance(t, QMenu)}

    def dialog(fn, name: str) -> None:
        before.clear()
        before.update(visible_tops())
        fn()

        def shot() -> None:
            new = list(visible_tops() - before)
            opened.extend(new)
            if new:
                save(name, new[0])
            for t in opened:
                t.close()
            opened.clear()

        QTimer.singleShot(700, shot)

    def menu(m: QMenu, name: str, at: QWidget) -> None:
        m.popup(at.mapToGlobal(QPoint(0, at.height())))
        QTimer.singleShot(400, lambda: (save(name, m), m.close()))

    def stage() -> None:
        for c in w.grid.cells[:2]:
            object.__setattr__(c.cfg, "atem_input", w.grid.cells.index(c) + 1)
        w.atem = FakeAtem()
        a = w.grid.cells[0].session.analyzer
        r = a.latest() if a else None
        if r and r.tracks:
            x1, y1, x2, y2 = r.tracks[0].box
            a.select_at((x1 + x2) / 2, (y1 + y2) / 2)
        w.select_slot(0)
        w.toggle_follow()

    def presets() -> None:
        w.presets_btn.setChecked(True)
        for slot in (0, 1):
            for n in (1, 2, 3):
                w._capture_thumb(slot, n)
        cam = w.grid.cells[0].cfg.id
        for n, name in ((1, "Трибуна"), (2, "Пояс центр"), (3, "Общий")):
            w.thumbs.rename(cam, n, name)
        w._thumb_cache.clear()

    def middle() -> None:  # повтор — с середины записи, где человек уже идёт
        for t in app.topLevelWidgets():
            if t.isVisible() and hasattr(t, "slider"):
                t.slider.setValue(t.slider.maximum() // 2)

    cell0 = w.cells[0]
    steps = [
        (4500, stage),
        (3500, lambda: save("top-bar", rect=area(w._top).adjusted(area(w.atem_pill).left() - 260, 0, 0, 0))),
        (0, lambda: save("camera-tracking", rect=area(cell0, pad=2))),
        (0, lambda: save("tally", rect=area(*w.cells[:2], pad=2))),
        (0, lambda: save("right-column", rect=area(w.side_scroll))),
        (0, lambda: save("summary-positions", rect=area(*w.cells[2:4], pad=2))),
        (0, presets),
        (2000, lambda: save("presets", rect=area(w.presets_drawer))),
        (0, lambda: w.presets_btn.setChecked(False)),
        (800, lambda: (w._menu_to(cell0.video_frame), w.camera_menu.open())),
        (700, lambda: save("quick-menu", rect=area(cell0, pad=2))),
        (0, lambda: w.camera_menu.close_menu()),
        (300, lambda: w.toggle_follow()),
        (300, lambda: w.enter_mouse_mode()),
        (700, lambda: save("mouse-mode", rect=area(cell0, pad=2))),
        (0, lambda: w.exit_mouse_mode()),
        (300, lambda: menu(w.main_menu_btn.menu(), "main-menu", w.main_menu_btn)),
        (800, lambda: menu(cell0.more_btn.menu(), "camera-menu", cell0.more_btn)),
        (800, lambda: dialog(w.show_hotkeys, "help")),
        (1000, lambda: dialog(w.show_gamepads, "gamepads")),
        (1000, lambda: dialog(w._show_agent_window, "agent")),
        (1000, lambda: dialog(w._edit_atem, "atem")),
        (1000, lambda: dialog(lambda: CameraDialog(CameraConfig("hall", "Зал, центр", "10.0.0.12", atem_input=3), parent=w).show(), "camera")),
        (1000, lambda: dialog(w._recording_settings, "recording")),
        (1000, lambda: dialog(w._run_calibration, "calibration")),
        (1000, lambda: dialog(lambda: (w.open_replay(EPISODE), QTimer.singleShot(300, middle)), "replay")),
        (1500, lambda: (w.close(), app.quit())),
    ]

    def run(i: int = 0) -> None:  # по очереди: следующий шаг — после предыдущего
        if i < len(steps):
            delay, fn = steps[i]
            QTimer.singleShot(max(delay, 300), lambda: (fn(), run(i + 1)))

    run()
    app.exec()


if __name__ == "__main__":
    main()
