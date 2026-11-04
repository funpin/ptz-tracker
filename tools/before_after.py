"""«Было / стало»: один сценарий слежения на симуляторе с перспективой, два раза — на старом коде и на текущем.

Запуск (из корня репозитория, окно не нужно, камеры не касается):

    .venv/bin/python tools/before_after.py                      # сцена walk (быстрая ходьба), 60 с
    .venv/bin/python tools/before_after.py --scene lecture      # сцена lecture (прохаживается)
    .venv/bin/python tools/before_after.py --compose-only       # пересобрать видео по уже снятым прогонам

«Было» — коммит 9ceffb5 (до части 1), он на время прогона кладётся во временный `git worktree` и потом удаляется.
«Стало» — рабочая папка как есть. Каждый прогон — отдельный процесс с PYTHONPATH=<папка>/src, так что оба
берут СВОЙ код. Прогон идёт в реальном времени: камера «Пояс · центр», характер «Обычно», без калибровки
(как в части 0), чёрный ящик включён. Если сторож выключил слежение, через 2 с оператор (скрипт) включает его снова.

Результат в --out (по умолчанию ~/Desktop/ptz-before-after):
  before_after_<сцена>.mp4  — слева «было», справа «стало», рамка цели, внизу ошибка по горизонтали;
  summary_<сцена>.json      — числа по каждому прогону;
  <сцена>/before, <сцена>/after — сырые данные прогона (кадры, trace.json, zip чёрного ящика).

Рамка на видео — истинное положение человека из симулятора (не то, что увидел детектор): так сравнение честное.
Ошибка — на сколько нос ушёл от центра кадра по горизонтали, в долях ширины кадра (0,5 — край кадра).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "9ceffb5"  # последний коммит до части 1 (разбор части 0)
OUT_DEFAULT = "~/Desktop/ptz-before-after"
FPS = 25
PANEL = (640, 360)  # каждая половина видео
STRIP_H = 200  # график ошибки внизу
ERR_RANGE = 0.5  # график показывает ошибку в пределах ±0,5 кадра
WATCHDOG_REASON = "камера раскачивалась"  # начало текста причины выключения сторожем (Follower.disable)
REENABLE_AFTER_S = 2.0
ANCHOR_X = 0.5  # «Пояс · центр»
WALK_SKIP_S = 1.0  # первую секунду каждой ходьбы в «установившейся» ошибке не считаем (часть 0: старт не догнать)
COLORS = {"before": (40, 140, 255), "after": (90, 200, 70)}  # BGR: оранжевый и зелёный
LABELS = {"before": "было", "after": "стало"}


# --- чистые помощники: числа и картинка (их проверяют тесты) ------------------------------------------------


def compute_metrics(rows: list[dict], trips: list[float]) -> dict:
    """Числа одного прогона по строкам trace.json.

    Строка: t — секунды сцены с момента включения слежения; err_x — ошибка по горизонтали (доли кадра, + вправо);
    edge — расстояние носа до ближайшего бокового края (доли кадра, < 0 — за краем); in_frame — нос виден;
    zoom — приближение относительно самого широкого угла (×); walk — человек идёт (None — неизвестно).
    """
    if not rows:
        return {"frames": 0}
    err = np.array([abs(r["err_x"]) for r in rows])
    out: dict = {
        "frames": len(rows),
        "duration_s": round(rows[-1]["t"] - rows[0]["t"], 1),
        "mean_abs_err_x": round(float(err.mean()), 3),
        "max_abs_err_x": round(float(err.max()), 3),
        "out_of_frame_pct": round(100.0 * sum(not r["in_frame"] for r in rows) / len(rows), 1),
        "min_edge_dist": round(min(r["edge"] for r in rows), 3),
        "watchdog_trips": len(trips),
        "watchdog_trip_times_s": [round(t, 1) for t in trips],
    }
    walk = [r for r in rows if r.get("walk")]
    if walk:
        zs = [r["zoom"] for r in walk]
        out["zoom_min_walk"], out["zoom_max_walk"] = round(min(zs), 1), round(max(zs), 1)
        rel, start_z, prev = [], None, False
        steady = []
        t_walk0 = 0.0
        for r in rows:
            if r.get("walk"):
                if not prev:
                    start_z, t_walk0 = r["zoom"], r["t"]
                rel.append(r["zoom"] / start_z)
                if r["t"] - t_walk0 >= WALK_SKIP_S:
                    steady.append(abs(r["err_x"]))
            prev = bool(r.get("walk"))
        # во сколько раз зум на ходу ушёл от того, что был в начале этой ходьбы (меньше 1 — отъехал)
        out["zoom_ratio_walk_min"], out["zoom_ratio_walk_max"] = round(min(rel), 2), round(max(rel), 2)
        out["walk_mean_abs_err_x"] = round(float(np.mean([abs(r["err_x"]) for r in walk])), 3)
        if steady:
            out["walk_steady_mean_abs_err_x"] = round(float(np.mean(steady)), 3)
    return out


def make_strip(runs: dict[str, list[dict]], trips: dict[str, list[float]], duration: float,
               size: tuple[int, int], walk_spans: list[tuple[float, float]]) -> np.ndarray:
    """Основа графика ошибки по горизонтали: ходьба — светлые полосы, сетка ±0,15/±0,25, линии «было» и «стало»."""
    w, h = size
    img = np.full((h, w, 3), 28, np.uint8)
    pad_l, pad_t, pad_b = 8, 8, 18
    plot_h = h - pad_t - pad_b

    def x_of(t: float) -> int:
        return int(round(pad_l + (w - 2 * pad_l) * min(max(t / duration, 0.0), 1.0)))

    def y_of(e: float) -> int:
        e = max(-ERR_RANGE, min(ERR_RANGE, e))
        return int(round(pad_t + plot_h * (0.5 - e / (2 * ERR_RANGE))))

    for t0, t1 in walk_spans:
        cv_rect(img, (x_of(t0), pad_t), (x_of(t1), pad_t + plot_h), (48, 44, 40))
    for e, c in ((0.0, 110), (0.25, 62), (-0.25, 62), (0.15, 45), (-0.15, 45)):
        cv_line(img, (pad_l, y_of(e)), (w - pad_l, y_of(e)), (c, c, c), 1)
    for key in ("before", "after"):
        pts = [(x_of(r["t"]), y_of(r["err_x"])) for r in runs.get(key, [])]
        cv_poly(img, pts, COLORS[key])
        for t in trips.get(key, []):  # сторож сработал — красная метка внизу
            cv_line(img, (x_of(t), pad_t + plot_h), (x_of(t), h - 2), (60, 60, 230), 3)
    return img


def compose_frame(left: np.ndarray | None, right: np.ndarray | None, strip: np.ndarray, t: float, duration: float,
                  labels: dict[str, np.ndarray | None] | None = None) -> np.ndarray:
    """Один кадр итогового видео: две картинки рядом (None — чёрный кадр), ниже график с бегущей отметкой времени."""
    pw, ph = PANEL
    panels = []
    for img in (left, right):
        if img is None:
            img = np.zeros((ph, pw, 3), np.uint8)
        elif img.shape[1] != pw or img.shape[0] != ph:
            img = cv_resize(img, (pw, ph))
        panels.append(img.copy())
    top = np.hstack(panels)
    bottom = strip.copy()
    pad_l = 8
    x = int(round(pad_l + (bottom.shape[1] - 2 * pad_l) * min(max(t / duration, 0.0), 1.0)))
    cv_line(bottom, (x, 0), (x, bottom.shape[0] - 1), (255, 255, 255), 1)
    if labels:
        for name, target, pos in (("before", top, (10, 8)), ("after", top, (pw + 10, 8)), ("legend", bottom, (10, 10))):
            if labels.get(name) is not None:
                blend_label(target, labels[name], *pos)
    return np.vstack([top, bottom])


def blend_label(dst: np.ndarray, label: np.ndarray, x: int, y: int) -> None:
    """Наложить BGRA-картинку подписи на BGR-кадр (с обрезкой по краям)."""
    h, w = label.shape[:2]
    h = min(h, dst.shape[0] - y)
    w = min(w, dst.shape[1] - x)
    if h <= 0 or w <= 0:
        return
    a = label[:h, :w, 3:4].astype(np.float32) / 255.0
    region = dst[y:y + h, x:x + w].astype(np.float32)
    dst[y:y + h, x:x + w] = (region * (1 - a) + label[:h, :w, :3].astype(np.float32) * a).astype(np.uint8)


# тонкие обёртки над cv2: импорт cv2 откладываем, чтобы дочерний прогон под старым кодом не зависел от порядка


def cv_line(img, p0, p1, color, thick):
    import cv2

    cv2.line(img, p0, p1, color, thick, cv2.LINE_AA)


def cv_rect(img, p0, p1, color):
    import cv2

    cv2.rectangle(img, p0, p1, color, -1)


def cv_poly(img, pts, color):
    import cv2

    if len(pts) > 1:
        cv2.polylines(img, [np.array(pts, np.int32)], False, color, 2, cv2.LINE_AA)


def cv_resize(img, size):
    import cv2

    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)


# --- подписи (кириллица: cv2.putText её не рисует, берём Qt) -------------------------------------------------


def render_labels() -> dict[str, np.ndarray]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

    _app = QGuiApplication.instance() or QGuiApplication([])

    def make(text: str, color: tuple[int, int, int], px: int) -> np.ndarray:
        font = QFont()
        font.setPixelSize(px)
        font.setBold(True)
        img = QImage(max(60, len(text) * px), px + 16, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        p.setFont(font)
        p.setPen(QColor(0, 0, 0, 200))
        p.drawText(2, px + 2, text)  # тень
        p.setPen(QColor(color[2], color[1], color[0]))
        p.drawText(0, px, text)
        p.end()
        arr = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.width(), 4)
        return arr.copy()  # BGRA

    return {
        "before": make("было", COLORS["before"], 30),
        "after": make("стало", COLORS["after"], 30),
        "legend": make("ошибка по горизонтали (линия по центру = 0, красная метка = сработал сторож)",
                       (200, 200, 200), 13),
    }


# --- один прогон (запускается в дочернем процессе под нужным кодом) -----------------------------------------


def run_one(scene_name: str, duration: float, out: Path) -> int:
    """Прогон на том коде, что лежит в PYTHONPATH: пишет out/trace.json и out/frames/*.jpg."""
    import cv2

    import ptztracker
    from ptztracker.camera.session import CameraSession
    from ptztracker.config import AppConfig, CameraConfig
    from ptztracker.control.composition import COMPOSITIONS
    from ptztracker.control.effort import EffortProfile
    from ptztracker.sim.scene import SyntheticStage

    out.mkdir(parents=True, exist_ok=True)
    frames_dir = out / "frames"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir()
    s = CameraSession(CameraConfig(id="s", name="Сим", ip="sim", kind="sim"),
                      app_cfg=AppConfig(record_dir=str(out)), scene=SyntheticStage(script=scene_name))
    s.start()
    trips: list[float] = []
    state = {"off_at": None, "t0": None}

    def on_change(enabled: bool, reason: str | None) -> None:
        if enabled:
            state["off_at"] = None
        elif state["t0"] is not None:
            state["off_at"] = time.monotonic()
            if reason and reason.startswith(WATCHDOG_REASON):
                trips.append(round(state["off_at"] - state["t0"], 3))

    rows: list[dict] = []
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            r = s.analyzer.latest()
            if r is not None and r.tracks:
                break
            time.sleep(0.05)
        else:
            print("Симулятор не показал человека", file=sys.stderr)
            return 1
        tr = s.analyzer.latest().tracks[0]
        s.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and s.analyzer.latest().target_state != "tracking":
            time.sleep(0.02)
        f = s.follower
        f.set_composition(COMPOSITIONS["waist_center"])
        f.set_effort(EffortProfile.at(0.5))
        f.on_change = on_change
        frames = s.frames
        frames._t0 = time.monotonic()  # время сцены с нуля: у обоих прогонов ходьба начинается на одной секунде
        state["t0"] = time.monotonic()
        if not f.enable():
            print("Слежение не включилось", file=sys.stderr)
            return 1
        try:
            from ptztracker.camera import he42

            hfov_wide = float(he42.HFOV_WIDE_DEG)
        except Exception:
            hfov_wide = float(s.sim.hfov_deg())
        scene = frames.scene
        last_seq = -1
        while True:
            now = time.monotonic()
            if now - state["t0"] >= duration:
                break
            off_at = state["off_at"]
            if off_at is not None and now - off_at >= REENABLE_AFTER_S:
                state["off_at"] = None
                f.enable()
            fr = frames.latest()
            if fr is None or fr.seq == last_seq:
                time.sleep(0.005)
                continue
            last_seq = fr.seq
            kp = (fr.meta or {}).get("people", [None])[0]
            if kp is None:
                continue
            h_px, w_px = fr.image.shape[:2]
            t_scene = fr.t_capture - frames.latency_s - frames._t0
            nx, ny = float(kp[0][0]) / w_px, float(kp[0][1]) / h_px
            walking = bool(scene._walking(t_scene)) if hasattr(scene, "_walking") else None
            idx = len(rows)
            # точки, которых симулятор не посчитал (за камерой), лежат в (0, 0) с conf 0 — в рамку их не берём
            valid = [p for p in kp if p[2] > 0 or p[0] != 0 or p[1] != 0] or [kp[0]]
            xs, ys = [float(p[0]) for p in valid], [float(p[1]) for p in valid]
            cv2.imwrite(str(frames_dir / f"{idx:05d}.jpg"), cv2.resize(fr.image, PANEL, interpolation=cv2.INTER_AREA),
                        [cv2.IMWRITE_JPEG_QUALITY, 80])
            rows.append({
                "t": round(t_scene, 3), "err_x": round(nx - ANCHOR_X, 4), "edge": round(min(nx, 1 - nx), 4),
                "in_frame": bool(0 <= nx < 1 and 0 <= ny < 1),
                "zoom": round(hfov_wide / float(s.sim.hfov_deg()), 2), "walk": walking,
                "on": bool(f.enabled), "nose": [round(nx, 4), round(ny, 4)],
                # рамка человека в долях кадра (по истинным точкам симулятора)
                "box": [round(min(xs) / w_px, 4), round(min(ys) / h_px, 4),
                        round(max(xs) / w_px, 4), round(max(ys) / h_px, 4)],
            })
        try:
            s.save_moment("before-after").result(timeout=30)
        except Exception as e:
            print(f"Чёрный ящик не сохранён: {e}", file=sys.stderr)
    finally:
        s.stop()
    meta = {"ptztracker_file": str(Path(ptztracker.__file__).resolve()), "version": getattr(ptztracker, "__version__", "?")}
    (out / "trace.json").write_text(json.dumps({"meta": meta, "rows": rows, "trips": trips}, ensure_ascii=False))
    return 0


# --- оркестратор -------------------------------------------------------------------------------------------


def _spawn(checkout: Path, scene: str, duration: float, out: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(checkout / "src")}
    env.pop("PYTHONHOME", None)
    cmd = [sys.executable, str(Path(__file__).resolve()), "--child", "--scene", scene, "--duration", str(duration),
           "--out", str(out)]
    print(f"прогон {out.name}: {checkout}", flush=True)
    subprocess.run(cmd, env=env, check=True, cwd=str(checkout))
    got = json.loads((out / "trace.json").read_text())["meta"]["ptztracker_file"]
    if not Path(got).is_relative_to(checkout.resolve()):
        raise RuntimeError(f"Прогон взял чужой код: {got} (ждали {checkout})")


def run_both(scene: str, duration: float, base_out: Path, baseline: str) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="ptz-baseline-"))
    wt = tmp / "wt"
    subprocess.run(["git", "worktree", "add", "--detach", str(wt), baseline], cwd=ROOT, check=True,
                   capture_output=True)
    try:
        _spawn(wt, scene, duration, base_out / scene / "before")
        _spawn(ROOT, scene, duration, base_out / scene / "after")
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT, capture_output=True)
        subprocess.run(["git", "worktree", "prune"], cwd=ROOT, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def walk_spans(rows: list[dict]) -> list[tuple[float, float]]:
    spans, start = [], None
    for r in rows:
        if r.get("walk") and start is None:
            start = r["t"]
        elif not r.get("walk") and start is not None:
            spans.append((start, r["t"]))
            start = None
    if start is not None:
        spans.append((start, rows[-1]["t"]))
    return spans


def compose(scene: str, base_out: Path) -> dict:
    import cv2

    data = {k: json.loads((base_out / scene / k / "trace.json").read_text()) for k in ("before", "after")}
    rows = {k: d["rows"] for k, d in data.items()}
    trips = {k: d["trips"] for k, d in data.items()}
    duration = max(r[-1]["t"] for r in rows.values() if r)
    summary = {
        "scene": scene, "baseline": BASELINE,
        "runs": {k: {"version": data[k]["meta"]["version"], **compute_metrics(rows[k], trips[k])} for k in data},
    }
    (base_out / f"summary_{scene}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    labels = render_labels()
    strip = make_strip(rows, trips, duration, (2 * PANEL[0], STRIP_H), walk_spans(max(rows.values(), key=len)))
    path = base_out / f"before_after_{scene}.mp4"
    size = (2 * PANEL[0], PANEL[1] + STRIP_H)
    writer = None
    for fourcc in ("avc1", "mp4v"):
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), FPS, size)
        if writer.isOpened():
            break
    if writer is None or not writer.isOpened():
        raise RuntimeError("Не удалось открыть запись mp4")
    idx = {k: 0 for k in rows}
    t0 = min(r[0]["t"] for r in rows.values() if r)
    for n in range(int((duration - t0) * FPS)):
        t = t0 + n / FPS
        imgs = {}
        for k in rows:
            r = rows[k]
            while idx[k] + 1 < len(r) and r[idx[k] + 1]["t"] <= t:
                idx[k] += 1
            img = cv2.imread(str(base_out / scene / k / "frames" / f"{idx[k]:05d}.jpg"))
            if img is not None:
                draw_box(img, r[idx[k]])
            imgs[k] = img
        writer.write(compose_frame(imgs["before"], imgs["after"], strip, t, duration, labels))
    writer.release()
    return summary


def draw_box(img: np.ndarray, row: dict) -> None:
    import cv2

    h, w = img.shape[:2]
    x0, y0, x1, y1 = row["box"]
    color = (80, 220, 80) if row["in_frame"] else (60, 60, 230)
    cv2.rectangle(img, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), color, 2)
    cv2.line(img, (w // 2, 0), (w // 2, 10), (255, 255, 255), 1)  # центр кадра
    cv2.circle(img, (int(row["nose"][0] * w), int(row["nose"][1] * h)), 4, (0, 255, 255), -1)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scene", default="walk", choices=("walk", "lecture"))
    p.add_argument("--duration", type=float, default=60.0, help="длительность слежения, с")
    p.add_argument("--out", default=OUT_DEFAULT)
    p.add_argument("--baseline", default=BASELINE, help="коммит «было»")
    p.add_argument("--compose-only", action="store_true", help="не гонять симулятор, собрать видео по готовым данным")
    p.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    out = Path(a.out).expanduser()
    if a.child:
        return run_one(a.scene, a.duration, out)
    out.mkdir(parents=True, exist_ok=True)
    if not a.compose_only:
        run_both(a.scene, a.duration, out, a.baseline)
    summary = compose(a.scene, out)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nВидео: {out / f'before_after_{a.scene}.mp4'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
