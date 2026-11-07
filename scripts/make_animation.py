"""Анимации для README: окно на симуляторе ~12 с — выбор человека, слежение с наездом до пояса, вид «1 камера».

Запуск на Mac: python scripts/make_animation.py — ~25 с, нужен ffmpeg (brew install ffmpeg).
Две записи, каждая в своём процессе: docs/assets/overview.gif (всё окно, в конце вид «1 камера») и
docs/assets/guide/tracking.gif (клетка камеры 1 без смены вида — наезд до пояса доходит до конца).
Камера 1 в эфире (PGM), камера 2 — в превью: ATEM подменён снимком, как в shoot_guide.py."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtWidgets import QApplication

from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow
from ptztracker.ui.theme import apply_dark_theme, install_russian

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shoot_guide import FakeAtem  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HERO = ROOT / "docs" / "assets" / "overview.gif"
TRACKING = ROOT / "docs" / "assets" / "guide" / "tracking.gif"
FPS = 10
# запись: (секунд, когда включить вид «1 камера» или None, файл, ширина GIF, только клетка камеры 1)
TAKES = {
    "hero": (12.0, 9.5, HERO, 1100, False),
    "tracking": (15.0, None, TRACKING, 600, True),  # камера начинает наезд ~через 9 с слежения
}
HOLD = 1.0  # последний кадр стоит перед повтором, чтобы GIF не обрывался на движении


def gif(frames: Path, out: Path, width: int, crop: QRect | None = None) -> None:
    """Кадры frames/%04d.png → GIF с общей палитрой (ffmpeg palettegen/paletteuse)."""
    vf = f"scale={width}:-1:flags=lanczos,tpad=stop_mode=clone:stop_duration={HOLD}"
    if crop is not None:
        vf = f"crop={crop.width()}:{crop.height()}:{crop.x()}:{crop.y()}," + vf
    pal = f"{vf},split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS), "-i", str(frames / "%04d.png")]
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([*cmd, "-filter_complex", pal, "-loop", "0", str(out)], check=True)
    print(out, f"{out.stat().st_size / 1e6:.1f} МБ")


def record(take: str) -> None:
    seconds, single_at, out, width, cell_only = TAKES[take]
    app = QApplication([])
    install_russian(app)
    apply_dark_theme(app)
    tmp = Path(tempfile.mkdtemp())
    frames = tmp / "frames"
    frames.mkdir()
    w = MainWindow(ConfigStore(tmp / "c.json"), use_sim=True)
    w.resize(1640, 850)
    w.setAttribute(Qt.WA_TransparentForMouseEvents)  # курсор над окном не рисует подсказки зоны в записи
    w.show()
    n = [0]
    cell_rect: list[QRect] = []

    def grab() -> None:
        w.statusBar().clearMessage()
        img = w.grab().toImage().scaled(w.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        img.save(str(frames / f"{n[0]:04d}.png"))
        n[0] += 1
        if n[0] >= seconds * FPS:
            timer.stop()
            app.quit()

    def setup() -> None:
        for i, c in enumerate(w.grid.cells[:2]):
            object.__setattr__(c.cfg, "atem_input", i + 1)
        w.atem = FakeAtem()
        w.select_slot(0)
        s = w.follow_panel.effort_slider
        s.setValue(s.maximum())  # «Резко»: наезд до пояса успевает за запись
        cell = w.cells[0]
        cell_rect.append(QRect(cell.mapTo(w, QPoint(0, 0)), cell.size()).adjusted(-2, -2, 2, 2))

    def pick() -> None:
        a = w.grid.cells[0].session.analyzer
        r = a.latest() if a else None
        if r and r.tracks:
            x1, y1, x2, y2 = r.tracks[0].box
            a.select_at((x1 + x2) / 2, (y1 + y2) / 2)
            w._update_status()

    def at(sec: float, fn) -> None:
        QTimer.singleShot(int(sec * 1000), fn)

    timer = QTimer()
    timer.timeout.connect(grab)
    at(3.5, setup)

    def start() -> None:
        timer.start(1000 // FPS)
        at(0.8, pick)
        at(1.6, w.toggle_follow)
        if single_at is not None:
            at(single_at, lambda: w.set_single_view(True))

    at(4.5, start)
    app.exec()
    w.close()
    gif(frames, out, width, crop=cell_rect[0] if cell_only else None)
    shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    if shutil.which("ffmpeg") is None:
        raise SystemExit("нужен ffmpeg: brew install ffmpeg")
    if len(sys.argv) > 1:
        record(sys.argv[1])
        return
    for take in TAKES:  # каждое окно — в чистом процессе: одна программа на машине, свой симулятор
        subprocess.run([sys.executable, __file__, take], check=True)


if __name__ == "__main__":
    main()
