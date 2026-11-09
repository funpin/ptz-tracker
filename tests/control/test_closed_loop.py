"""Поведение «как оператор» в замкнутом контуре на симуляторе (реальное время, ~2,5 мин)."""

import time

import numpy as np
import pytest

from ptztracker.camera.session import UNCALIBRATED_SAFETY, CameraSession
from ptztracker.config import CameraConfig
from ptztracker.control.camera_model import default_calibration
from ptztracker.control.composition import COMPOSITIONS
from ptztracker.control.effort import EffortProfile
from ptztracker.control.framing import CATCH_UP
from ptztracker.sim.scene import SCRIPTS, SyntheticStage

pytestmark = pytest.mark.slow


def wait_until(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def run_follow(
    script, seconds, comp="waist_center", effort=0.5, sim_latency=None, calib_latency=None, calibrated=True,
    safety=1.0,
):
    """calibrated=True — как после мастера калибровки (таблица совпадает с камерой симулятора)."""
    cfg = CameraConfig(id="s", name="Сим", ip="sim", kind="sim", composition=comp, effort=effort)
    if calibrated or calib_latency is not None:
        c = default_calibration(safety=safety)
        if calib_latency is not None:
            c.video_latency["sim"] = calib_latency
        cfg.calibration = c.to_json()
    s = CameraSession(cfg, scene=SyntheticStage(script=script), sim_latency=sim_latency)
    s.start()
    try:
        assert wait_until(lambda: s.analyzer.latest() is not None and len(s.analyzer.latest().tracks) > 0, 4)
        tr = s.analyzer.latest().tracks[0]
        s.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2)
        assert wait_until(lambda: s.analyzer.latest().target_state == "tracking", 2)
        assert s.follower.enable()
        rows = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            fr = s.frames.latest()
            kp = fr.meta["people"][0]
            w, h = fr.image.shape[1], fr.image.shape[0]
            u = (kp[5, 1] + kp[6, 1]) / 2 / h - kp[0, 1] / h if kp[0, 2] > 0 and kp[5, 2] > 0 else None
            with s.sim._lock:
                cam_fw = s.sim.pan_vel / s.sim.hfov_deg()  # скорость поворота камеры, доли кадра/с
                k = s.sim.zoom_ratio()
            rows.append(
                dict(t=time.monotonic() - t0, x=kp[0, 0] / w, y=kp[0, 1] / h, vis=kp[0, 2] > 0, u=u,
                     pan=s.sim.pan_code, zoom=s.sim.zoom_code, cam=cam_fw, k=k,
                     st=fr.t_capture - s.frames.latency_s - s.frames._t0,  # время сцены этого кадра
                     tracking=s.analyzer.latest().target_state == "tracking")
            )
            time.sleep(0.1)
        assert s.follower.enabled, f"слежение выключилось: {s.follower.last_reason}"
        return rows
    finally:
        s.stop()


def reversals(codes):
    signs = [1 if c > 50 else -1 for c in codes if c != 50]
    return sum(1 for a, b in zip(signs, signs[1:]) if a != b)


def test_static_speaker_no_hunting():
    # человек переминается ±10 см: камера не ходит за каждым качанием, а держит середину
    rows = run_follow("stand", 22)
    late = [r for r in rows if r["t"] > 10]
    assert reversals([r["pan"] for r in late]) <= 1
    assert np.mean([r["pan"] == 50 for r in rows if r["t"] > 14]) >= 0.9


def test_shuffling_speaker_no_chasing():
    # заметно шатается (±18 см — на «Поясе» это ±0,11 кадра): камера почти не движется и держит середину
    rows = run_follow("shuffle", 25)
    late = [r for r in rows if r["t"] > 12]
    assert reversals([r["pan"] for r in late]) <= 3
    assert np.mean([r["pan"] == 50 for r in late]) >= 0.75
    assert abs(np.mean([r["x"] for r in late]) - 0.5) <= 0.08


def test_walk_and_stop_like_operator():
    rows = run_follow("lecture", 30)
    e = EffortProfile.at(0.5)
    ax = COMPOSITIONS["waist_center"].anchor[0]
    assert all(r["vis"] for r in rows), "человек выходил из кадра"
    warm = [r for r in rows if r["t"] > 6]
    errs = [abs(r["x"] - ax) for r in warm]
    # На старте ходьбы с крупного плана камера отстаёт (разгон ~0.85 с + задержка): главное — человек в кадре
    # средняя ошибка гуляет ±0.02 от прогона к прогону (реальное время); в неё входят и стояния с переминанием ±8 см
    assert np.mean(errs) < 0.14 and max(errs) <= 0.45
    # Резкость — по ускорению самой камеры (доли кадра/с², по скорости поворота симулятора), а не по коду.
    # Обычный разгон — не больше a_max (далеко от метки до CATCH_UP·a_max), торможение — BRAKE·a_max;
    # запас 20% — на шаг замера 0,1 с и неточность τ.
    acc = [abs(b["cam"] - a["cam"]) / max(0.05, b["t"] - a["t"]) for a, b in zip(rows, rows[1:])]
    assert max(acc) <= 1.2 * CATCH_UP * e.a_max, max(acc)
    assert reversals([r["pan"] for r in rows]) <= 5  # две ходьбы и остановки, плюс подправки при переминании


def test_uncalibrated_still_keeps_person_in_frame():
    # до калибровки модель с запасом считает камеру быстрее: ведёт свободнее, но устойчиво
    rows = run_follow("lecture", 30, calibrated=False)
    assert all(r["vis"] for r in rows), "человек выходил из кадра"
    assert np.mean([abs(r["x"] - 0.5) for r in rows if r["t"] > 6]) < 0.12
    assert reversals([r["pan"] for r in rows]) <= 3


def test_soft_vs_sharp():
    soft = run_follow("lecture", 26, effort=0.0)
    sharp = run_follow("lecture", 26, effort=1.0)

    def max_speed(rows):
        return max(abs(r["pan"] - 50) for r in rows)

    def code_changes(rows):
        return sum(1 for a, b in zip(rows, rows[1:]) if a["pan"] != b["pan"])

    # «Мягко» — спокойнее: не быстрее и не суетливее, чем «Резко»
    assert max_speed(soft) <= max_speed(sharp)
    # Число смен кода — по реальному времени с потоками и считается на одном прогоне: на загруженной машине
    # «Мягко» бывало 48 против 45 у «Резко». Допуск 25% + 3 смены; строгое сравнение держит max_speed.
    assert code_changes(soft) <= code_changes(sharp) * 1.25 + 3
    assert all(r["vis"] for r in soft + sharp)


def test_zoom_converges_to_waist():
    rows = run_follow("stand", 20)
    us = [r["u"] for r in rows if r["t"] > 15 and r["u"] is not None]
    assert us and abs(np.mean(us) / COMPOSITIONS["waist_center"].u_frac - 1) < 0.15
    zsigns = [1 if r["zoom"] > 50 else -1 for r in rows if r["zoom"] != 50]
    assert sum(1 for a, b in zip(zsigns, zsigns[1:]) if a != b) <= 2


def test_nose_held_at_anchor_height_not_centre():
    # «Пояс · центр» на проективном симуляторе: когда камера успокоилась, нос стоит на высоте якоря (≈0,25 кадра),
    # а не в центре (0,5) — иначе кадр снят «от груди», а не «от лица».
    rows = run_follow("stand", 20)
    ay = COMPOSITIONS["waist_center"].anchor[1]
    ys = [r["y"] for r in rows if r["t"] > 15 and r["vis"]]
    assert ys and abs(np.mean(ys) - ay) <= 0.05, np.mean(ys)


def test_with_h264_latency():
    # таблица как у камеры в деле — со стандартным запасом: при ρ = 1 и задержке 0,6 с первая ходьба перелетала
    # цель и камера разворачивалась (4–6 смен знака в ~40% прогонов, лимит 3)
    rows = run_follow("lecture", 26, sim_latency=0.6, calib_latency=0.6, safety=UNCALIBRATED_SAFETY)
    assert all(r["vis"] for r in rows), "человек выходил из кадра"
    assert reversals([r["pan"] for r in rows]) <= 3


def _walk_phases(rows, script, skip_s=8.0):
    """По каждой ходьбе сценария (по времени сцены): средняя |x−якорь| без первой 1 с, ближе всего к краю,
    отъезд зума (масштаб в начале / наименьший за ходьбу). Первые skip_s — захват и наезд с общего плана."""
    sa, wr, sb, wl = SCRIPTS[script]
    cyc = sa + wr + sb + wl
    ax = COMPOSITIONS["waist_center"].anchor[0]
    out = []
    for c in range(10):
        for ws, wd in ((c * cyc + sa, wr), (c * cyc + sa + wr + sb, wl)):
            walk = [r for r in rows if ws <= r["st"] < ws + wd]
            if ws < rows[0]["st"] + skip_s or not walk or ws + wd > rows[-1]["st"]:
                continue
            late = [r for r in walk if r["st"] >= ws + 1.0]
            out.append(dict(start=ws, mean=float(np.mean([abs(r["x"] - ax) for r in late])),
                            edge=min(min(r["x"], 1 - r["x"]) for r in walk),
                            zoom_out=walk[0]["k"] / min(r["k"] for r in walk)))
    return out


def test_lecture_walk_no_lag_on_projective_sim():
    """Симулятор с перспективой: прохаживается (~0,5 м/с), «Пояс», «Обычно». Без первой 1 с каждой ходьбы средняя
    ошибка ≤ 0,095 кадра (человек чуть впереди метки — цена меньшего перелёта при остановке, DECISIONS),
    человек не ближе 0,15 к краю, зум не отъезжает больше чем в 1,3 раза."""
    rows = run_follow("lecture", 80)
    phases = _walk_phases(rows, "lecture")
    assert len(phases) >= 3
    assert np.mean([p["mean"] for p in phases]) <= 0.095, phases
    for p in phases:
        assert p["edge"] >= 0.15 and p["zoom_out"] <= 1.3, p


def test_fast_walk_not_lost_on_projective_sim():
    """Review Focus 1, быстрая ходьба (сценарий walk: вправо 1,25 м/с с места, влево 0,83 м/с) на «Поясе».

    Камера трогается через ~0,4 с после шага (видео, анализ, очередь, мёртвое время) и разгоняется с τ, поэтому
    на 1,25 м/с с места нос на мгновение выходит за край кадра. Слежение при этом не сбрасывается: камера
    идёт за ушедшим ещё LOST_COAST_S, отъезжает зумом, и цель снова ведётся не позже чем через 1 с (замер:
    ~0,5 с). Было: человек терялся на каждой ходьбе вправо до следующего прохода."""
    rows = run_follow("walk", 68)
    after = [r for r in rows if r["t"] > 8]
    lost_since, longest = None, 0.0
    for r in after:
        if not r["tracking"] and lost_since is None:
            lost_since = r["t"]
        elif r["tracking"] and lost_since is not None:
            longest, lost_since = max(longest, r["t"] - lost_since), None
    if lost_since is not None:  # прогон кончился во время пропажи
        longest = max(longest, after[-1]["t"] - lost_since)
    assert longest <= 1.0, longest
    assert np.mean([r["tracking"] for r in after]) >= 0.95
    for p in _walk_phases(rows, "walk"):
        walk = [r["zoom"] for r in rows if p["start"] <= r["st"] < p["start"] + 6.0]
        assert reversals(walk) <= 3, p  # зум за ходьбу не дёргается туда-обратно (18↔50)
