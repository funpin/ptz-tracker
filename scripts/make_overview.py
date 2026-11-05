"""Картинка-обзор окна для README: окно на симуляторе и лаймовые номера на элементах.

Запуск на Mac: python scripts/make_overview.py — на ~6 с откроет окно и запишет docs/assets/overview.png
в разрешении Retina. Снимаем только на Mac (решение пользователя 2026-11-05); Windows выглядит так же —
шрифт и цвета у программы свои. Номера совпадают со списком под картинкой в README.md: поменяли окно —
пересоберите картинку и сверьте список."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow
from ptztracker.ui.theme import C, apply_dark_theme, install_russian, qcolor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets" / "overview.png"


def callouts(w: MainWindow) -> list[tuple[QWidget, str]]:
    """(виджет, где ставить номер: tl — левый верхний угол, up — над ним, на пустом месте) по порядку списка README."""
    cell = w.cells[0]
    return [
        (w.track_btn, "tl"),  # 1 TRACK и квадратики камер
        (w.blackbox_btn, "tl"),  # 2 журнал для разбора
        (w.main_menu_btn, "tl"),  # 3 меню ☰
        (cell.combo, "tl"),  # 4 шапка клетки: номер и камера
        (cell.state_tag, "tl"),  # 5 эфир и состояние слежения
        (cell.power_btn, "tl"),  # 6 питание и ⋯
        (cell.info_label, "tl"),  # 7 разрешение и анализ
        (w._service["summary"], "tl"),  # 8 сводка
        (w._service["position"], "tl"),  # 9 положение камер
        (w.follow_panel.comp_buttons["waist_center"], "tl"),  # 10 кадр
        (w.controls.camera_header, "up"),  # 11 стик, зум, чувствительность
        (w.controls.image_header, "up"),  # 12 изображение
        (w.controls.stop_btn, "tl"),  # 13 стоп
        (w.presets_drawer, "tl"),  # 14 пресеты
        (w.help_btn, "tl"),  # 15 справка
    ]


def draw_numbers(img, w: MainWindow, scale: float) -> None:
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    f = QFont(w.font())
    f.setPixelSize(round(12 * scale))
    f.setBold(True)
    p.setFont(f)
    r = 11 * scale
    for i, (wd, corner) in enumerate(callouts(w), start=1):
        if not wd.isVisible():
            continue
        tl = wd.mapTo(w, QPoint(0, 0))
        c = QPointF(tl.x() * scale - r * 0.2, tl.y() * scale - r * 0.2)
        if corner == "up":  # заголовок раздела: номер над первой буквой, а не поверх неё
            c = QPointF(tl.x() * scale + r, tl.y() * scale - r * 1.2)
        c = QPointF(max(r + 2, c.x()), max(r + 2, c.y()))
        p.setPen(QPen(qcolor(C.BG), 3 * scale))
        p.setBrush(qcolor(C.LIME))
        p.drawEllipse(c, r, r)
        p.setPen(qcolor(C.BG))
        p.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignCenter, str(i))
    p.end()


def main() -> int:
    app = QApplication(sys.argv[:1])
    install_russian(app)
    apply_dark_theme(app)
    tmp = Path(tempfile.mkdtemp())
    w = MainWindow(ConfigStore(tmp / "config.json"), use_sim=True)
    w.resize(1640, 850)
    w.show()

    def stage() -> None:
        w.presets_btn.click()
        for slot in (0, 1):
            for n in (1, 2, 3):
                w._capture_thumb(slot, n)
        cam = w.grid.cells[0].cfg.id
        for n, name in ((1, "Трибуна"), (2, "Пояс центр"), (3, "Общий")):
            w.thumbs.rename(cam, n, name)
        w._thumb_cache.clear()
        w._last_preset[0] = 2
        w._update_status()

    def shot() -> None:
        w.statusBar().clearMessage()  # служебные сообщения запуска (например, занятый порт API) — не для README
        app.processEvents()
        img = w.grab().toImage()
        # QPainter на картинке Retina уже рисует в логических точках окна
        draw_numbers(img, w, img.width() / img.devicePixelRatio() / w.width())
        OUT.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(OUT))
        app.quit()

    QTimer.singleShot(4000, stage)
    QTimer.singleShot(5500, shot)
    app.exec()
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
