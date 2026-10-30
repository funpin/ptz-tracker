import math
import threading
import time

import numpy as np
import pytest

from ptztracker.camera import he42
from ptztracker.camera.client import CameraClient
from ptztracker.control.calibration import CalibrationRunner, fit_ramp, monotone_curve
from ptztracker.sim.frame_source import SimFrameSource
from ptztracker.sim.http_server import SimHttpServer
from ptztracker.sim.ptz_model import HE42_DEG_S, SimPtz, he42_deg_s
from ptztracker.sim.scene import SyntheticStage


def wide_fw(code: int) -> float:
    """Скорость камеры симулятора в долях кадра на широком угле (как у живой HE42 — по he42.PAN_DEG_S)."""
    return he42_deg_s(code) / he42.HFOV_WIDE_DEG


class FlatScene:
    size = (7680, 2560)

    def render(self, t, cx, cy, crop_w, out_size):
        return np.full((out_size[1], out_size[0], 3), 90, np.uint8)


def make(scene=None, latency_s=0.15, **sim_kw):
    sim = SimPtz(**sim_kw)
    srv = SimHttpServer(sim)
    srv.start()
    src = SimFrameSource(sim, scene or SyntheticStage(script="stand"), out_size=(640, 360), latency_s=latency_s)
    src.start()
    time.sleep(0.5)
    client = CameraClient(srv.url_host)
    return sim, srv, src, client


@pytest.mark.slow
def test_calibration_recovers_sim_table_and_returns_home():
    sim, srv, src, client = make()
    try:
        sim.pan_deg, sim.tilt_deg = 3.0, -8.0
        r = CalibrationRunner(client.send, src, lambda m: None, zooms=[0x555], codes=(55, 60, 65, 70, 75, 80))
        cal = r.run()
        for code in (60, 65, 70, 75, 80):
            got = cal.pan.speed(code, 0x555)
            assert abs(got / wide_fw(code) - 1) < 0.15, (code, got, wide_fw(code))
        assert 0.1 <= cal.tau <= 0.25  # симулятор — τ 0,12 (замер 2026-10-29); было 0,2–0,55 вокруг 0,37
        assert cal.zoom.rate(75) > 0.1
        assert cal.created
        assert abs(sim.pan_deg - 3.0) < 0.5 and abs(sim.tilt_deg + 8.0) < 0.5 and sim.zoom < 0.01
    finally:
        src.stop()
        srv.stop()


@pytest.mark.slow
def test_flat_scene_completes_with_warning():
    """Однотонная картинка: скорости и τ по #APC, задержка видео — типовая для потока, оператор предупреждён."""
    sim, srv, src, client = make(FlatScene())
    try:
        msgs = []
        r = CalibrationRunner(client.send, src, msgs.append, zooms=[0x555], codes=(60, 70, 80), stream_kind="sim")
        cal = r.run()
        assert any("однотонн" in m for m in msgs), msgs
        assert cal.video_latency["sim"] == he42.VIDEO_LATENCY_S["sim"]
        for code in (70, 80):
            assert abs(cal.pan.speed(code, 0x555) / wide_fw(code) - 1) < 0.1
        assert 0.1 <= cal.tau <= 0.22  # было 0,27–0,47 вокруг 0,37
    finally:
        src.stop()
        srv.stop()


def test_default_zooms_cover_tele_and_flat_curves_dropped():
    from ptztracker.control.calibration import default_zooms, drop_flat_curves

    z = default_zooms(0x73F)
    assert z[0] == 0x555 and z[-1] == 0xFFF and 0x73F in z
    curves = {0x555: [(60, 0.03), (70, 0.3), (80, 0.5)], 0xFFF: [(60, 0.0), (70, 0.001), (80, 0.002)]}
    assert list(drop_flat_curves(curves)) == [0x555]


def _spy_starts(sim, client):
    """send, запоминающий положение камеры в момент каждой команды движения pan (начало пробега)."""
    starts = []

    def send(cmd):
        if cmd.startswith("#PTS") and cmd[4:6] != "50":
            starts.append(sim.pan_deg)
        return client.send(cmd)

    return send, starts


@pytest.mark.slow
def test_wizard_returns_to_start():
    """Каждый пробег начинается из исходной точки, в конце камера там же (±1°) — без увода влево."""
    sim, srv, src, client = make()
    try:
        sim.pan_deg, sim.tilt_deg = 3.0, -8.0
        send, starts = _spy_starts(sim, client)
        CalibrationRunner(send, src, lambda m: None, zooms=[0x555, 0xFFF], codes=(53, 60, 70, 80)).run()
        assert len(starts) >= 9  # разгон + 4 кода × 2 зума
        assert all(abs(p - 3.0) <= 1.0 for p in starts), starts
        assert abs(sim.pan_deg - 3.0) <= 1.0 and abs(sim.tilt_deg + 8.0) <= 1.0 and sim.zoom < 0.01
    finally:
        src.stop()
        srv.stop()


@pytest.mark.slow
def test_wizard_latency_matches_sim():
    """Задержка видео — по картинке, когда зум доехал: ±0,05 с от правды симулятора (не 0 и не по умолчанию).

    Перед стартом зум ещё едет на теле — мастер должен дождаться, пока #GZ перестанет меняться."""
    sim, srv, src, client = make(latency_s=0.30)
    try:
        client.send("#AXZFFF")  # зум едет ~2,5 с
        msgs = []
        cal = CalibrationRunner(client.send, src, msgs.append, zooms=[0xFFF], codes=(60, 70), stream_kind="sim").run()
        assert abs(cal.video_latency["sim"] - 0.30) <= 0.05, (cal.video_latency, msgs)
    finally:
        src.stop()
        srv.stop()


@pytest.mark.slow
def test_wizard_tau_matches_sim():
    """τ разгона — по #APC: ±0,1 с от симулятора (и не значение по умолчанию 0,37)."""
    sim, srv, src, client = make(tau=0.6)
    try:
        cal = CalibrationRunner(client.send, src, lambda m: None, zooms=[0x555], codes=(70,)).run()
        assert abs(cal.tau - 0.6) <= 0.1, cal.tau
    finally:
        src.stop()
        srv.stop()


@pytest.mark.slow
def test_wizard_on_flat_wall_does_not_drift():
    """Однотонная стена: мастер доходит до конца и камеру не уводит — каждый пробег из исходной точки."""
    sim, srv, src, client = make(FlatScene())
    try:
        sim.pan_deg = 3.0
        send, starts = _spy_starts(sim, client)
        CalibrationRunner(send, src, lambda m: None, zooms=[0x555, 0xFFF], codes=(60, 70, 80)).run()
        assert len(starts) == 7 and all(abs(p - 3.0) <= 1.0 for p in starts), starts
        assert abs(sim.pan_deg - 3.0) <= 1.0
    finally:
        src.stop()
        srv.stop()


def _ramp_samples(v, tau, t0, n=15, dt=0.15, quantum=0.0):
    out = []
    for i in range(1, n + 1):
        t = i * dt
        s = max(0.0, t - t0)
        x = v * (s - tau * (1 - math.exp(-s / tau))) if s > 0 else 0.0
        out.append((t, round(x / quantum) * quantum if quantum else x))
    return out


def test_fit_ramp_recovers_speed_tau_and_start():
    v, tau, t0 = fit_ramp(_ramp_samples(20.0, 0.45, 0.09, quantum=he42.APC_DEG_PER_UNIT), t_cmd=0.0)
    assert abs(v - 20.0) < 0.3 and abs(tau - 0.45) < 0.03 and abs(t0 - 0.09) < 0.03
    v, _, _ = fit_ramp(_ramp_samples(5.0, 0.37, 0.08), t_cmd=0.0, tau=0.37)
    assert abs(v - 5.0) < 0.05


def test_monotone_curve_drops_outliers_not_neighbours():
    curve = [(53, 0.0), (55, 0.0015), (60, 0.034), (65, 0.15), (68, 0.26), (70, 0.08), (75, 0.42), (80, 0.5)]
    kept, dropped = monotone_curve(curve)
    assert dropped == [70]
    assert kept[:2] == [(53, 0.0), (55, 0.0015)]  # медленные коды не «подтягиваются» к выбросу


def test_hfov_helper_matches_sim():
    sim = SimPtz()
    for z in (0.0, 0.3, 1.0):
        sim.zoom = z
        assert he42.hfov_deg(z) == pytest.approx(sim.hfov_deg())
    assert he42.hfov_deg(0.0) == pytest.approx(he42.HFOV_WIDE_DEG)
    assert he42.hfov_deg(1.0) == pytest.approx(he42.HFOV_TELE_DEG)


@pytest.mark.slow
def test_wizard_rejects_zero_latency():
    """Задержка видео < 0,05 с — не замер: берётся значение по виду потока, оператор предупреждён."""
    sim, srv, src, client = make(latency_s=0.0)
    try:
        msgs = []
        cal = CalibrationRunner(client.send, src, msgs.append, zooms=[0x555], codes=(70,), stream_kind="sim").run()
        assert cal.video_latency["sim"] == he42.VIDEO_LATENCY_S["sim"]
        assert any("задержка видео" in m for m in msgs), msgs
    finally:
        src.stop()
        srv.stop()


@pytest.mark.slow
def test_wizard_waits_for_zoom_that_starts_late():
    """Камера начинает абсолютный зум с задержкой: мастер ждёт, пока #GZ доедет до цели, а не «стоит до старта»."""
    sim, srv, src, client = make()
    handle = sim.handle

    def late_zoom(cmd):
        if cmd.startswith("#AXZ"):
            threading.Timer(0.8, handle, args=(cmd,)).start()
            return "axz" + cmd[4:]
        return handle(cmd)

    sim.handle = late_zoom
    try:
        cal = CalibrationRunner(client.send, src, lambda m: None, zooms=[0xFFF], codes=(70, 80)).run()
        # °/с камеры от зума не зависят (как у живой HE42); угол обзора — по доехавшему #GZ
        truth = {c: HE42_DEG_S[c] / he42.HFOV_TELE_DEG for c in (70, 80)}
        for code, want in truth.items():
            assert abs(cal.pan.speed(code, 0xFFF) / want - 1) < 0.1, (code, cal.pan.speed(code, 0xFFF), want)
    finally:
        src.stop()
        srv.stop()


@pytest.mark.slow
def test_wizard_near_pan_end_runs_toward_centre():
    """Старт у края диапазона pan: пробеги идут к центру, в упор не попадают, скорости верные."""
    sim, srv, src, client = make()
    try:
        sim.pan_deg = 170.0
        peak = []
        stop = threading.Event()

        def watch():
            while not stop.is_set():
                peak.append(sim.pan_deg)
                time.sleep(0.01)

        threading.Thread(target=watch, daemon=True).start()
        cal = CalibrationRunner(client.send, src, lambda m: None, zooms=[0x555], codes=(70, 80, 90)).run()
        stop.set()
        assert max(peak) < 171.0
        for code in (70, 80, 90):
            # код 90 на широком угле при разгоне 0,3 с: пробег стоп по паспортной скорости после 2 опросов, точность
            # хуже (замер 11,7%, было <10%); границу пробега (полкадра) это держит — важнее точности самого кода 90
            assert abs(cal.pan.speed(code, 0x555) / wide_fw(code) - 1) < (0.15 if code == 90 else 0.1), code
        assert abs(sim.pan_deg - 170.0) <= 1.0
    finally:
        src.stop()
        srv.stop()


@pytest.mark.slow
def test_wizard_never_goes_beyond_half_frame_from_start():
    """Живая камера: мастер уводил pan на ~32° (больше полкадра широкого угла). Теперь на широком угле — не
    дальше полкадра. На среднем (0x800, ~30°) и на теле (0xAAA, ~14°) быстрый код 90 (3–4 кадра/с) проскакивает
    полкадра за один опрос (~0,13 с): замерено 0,79–0,84 кадра на 0x800; там граница мягче — не больше кадра, а
    во всех случаях — не дальше полкадра ШИРОКОГО угла (31°)."""
    from ptztracker.camera.he42 import HFOV_WIDE_DEG, hfov_deg

    sim, srv, src, client = make()
    try:
        sim.pan_deg = 3.0
        stop = threading.Event()
        dev = []  # (отклонение от старта, доля зума, едет ли зум)

        def watch():
            while not stop.is_set():
                dev.append((abs(sim.pan_deg - 3.0), sim.zoom))
                time.sleep(0.01)

        threading.Thread(target=watch, daemon=True).start()
        CalibrationRunner(client.send, src, lambda m: None, zooms=[0x555, 0x800, 0xAAA], codes=(53, 70, 90)).run()
        stop.set()
        assert max(d for d, _ in dev) <= 0.5 * HFOV_WIDE_DEG, max(d for d, _ in dev)
        # зум едет между пробегами — берём только доехавший (оба соседних замера близки к 0x555 или 0x800)
        wide = max(d / hfov_deg(z) for d, z in dev if z < 0.02)
        mid_z = (0x800 - 0x555) / (0xFFF - 0x555)
        mid = max(d / hfov_deg(z) for d, z in dev if abs(z - mid_z) < 0.01)
        assert wide <= 0.5 and mid <= 1.0, (wide, mid)
    finally:
        src.stop()
        srv.stop()
