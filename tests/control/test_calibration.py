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
from ptztracker.sim.ptz_model import SimPtz, he42_deg_s
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
    assert 0xD55 in z and 0xEA9 in z  # тело: поворот там замедлен (BACKLOG «точку 0xD55 в мастер»)
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
def test_wizard_latency_matches_known_sim_latency():
    """Задержка 0,15 с (MJPEG (1) по прямому замеру 2026-11-10) и камера, быстрая как живая HE40: мастер даёт 0,15 ± 0,02.

    У камер клуба мастер записал 0,079 с при прямом замере ≈ 0,15 с — проверяем, не занижает ли он сам."""
    sim, srv, src, client = make(latency_s=0.15, tau=0.05, dead_time=0.05)
    try:
        msgs = []
        cal = CalibrationRunner(client.send, src, msgs.append, zooms=[0xAAA], codes=(60, 70), stream_kind="sim").run()
        assert abs(cal.video_latency["sim"] - 0.15) <= 0.02, (cal.video_latency, msgs)
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
        # °/с камеры до 0xD54 от зума не зависят (как у живой HE42), на теле — замедлены (HE40, he42.TELE_PAN_DEG_S);
        # угол обзора — по доехавшему #GZ
        truth = {c: he42_deg_s(c, 0xFFF) / he42.HFOV_TELE_DEG for c in (70, 80)}
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


def test_tele_curve_extended_above_measured_codes_by_rated_shape():
    """На теле мастер меряет коды до 75; быстрее — по форме паспортной кривой от замера, а не скорость кода 75
    до 99 (тогда регулятор считал бы камеру в разы медленнее и раскачал её)."""
    from ptztracker.control.calibration import extend_tele_curve

    curve = [(55, 0.1), (63, 0.4), (75, 1.6)]
    out = extend_tele_curve(curve, 0xFFF, (53, 70, 75, 80, 90))
    assert out[:3] == curve
    v80 = 1.6 * he42.pan_deg_s(80, 0xFFF) / he42.pan_deg_s(75, 0xFFF)
    assert [c for c, _ in out] == [55, 63, 75, 80, 90] and abs(out[3][1] - v80) < 1e-4


@pytest.mark.slow
def test_wizard_measures_tilt_separately_and_returns():
    """Наклон меряется отдельно (два кода, вверх и вниз): у симулятора наклон ×0,7 от поворота — в калибровке
    доля ~0,7, регулятор берёт её вместо «наклон = поворот». Камера в конце на месте (±1°)."""
    sim, srv, src, client = make(tilt_factor=0.7)
    try:
        sim.pan_deg, sim.tilt_deg = 3.0, -8.0
        lo = []
        stop = threading.Event()

        def watch():
            while not stop.is_set():
                lo.append(abs(sim.tilt_deg + 8.0))
                time.sleep(0.01)

        threading.Thread(target=watch, daemon=True).start()
        cal = CalibrationRunner(client.send, src, lambda m: None, zooms=[0x555], codes=(55, 63, 70, 75, 80)).run()
        stop.set()
        assert [c for c, _ in cal.tilt] == [63, 75]
        for _, r in cal.tilt:
            assert abs(r / 0.7 - 1) < 0.1, cal.tilt
        assert abs(cal.pan_fw(70, 0x555, axis="tilt") / (0.7 * cal.pan_fw(70, 0x555)) - 1) < 0.1
        # пробег наклона — короткий: не дальше полкадра по высоте широкого угла
        assert max(lo) <= 0.5 * he42.HFOV_WIDE_DEG * 9 / 16, max(lo)
        assert abs(sim.pan_deg - 3.0) <= 1.0 and abs(sim.tilt_deg + 8.0) <= 1.0
    finally:
        src.stop()
        srv.stop()


@pytest.mark.slow
def test_full_wizard_measures_tele_in_three_minutes():
    """Мастер по умолчанию с рабочего зума 0x800: точки тела 0xD55 и 0xEA9 меряются по #APC (коды 63/70/75 —
    в пределах 15% от симулятора, т. е. от he42.TELE_PAN_DEG_S), наклон измерен, сведения о камере записаны;
    весь мастер — не дольше 3 мин, камера в конце на месте (±1°) и не уходит дальше полкадра широкого угла."""
    from ptztracker.control.calibration import hfov_at

    sim, srv, src, client = make()
    try:
        client.send("#AXZ800")
        time.sleep(2.0)
        sim.pan_deg, sim.tilt_deg = 3.0, -8.0
        dev = []
        stop = threading.Event()

        def watch():
            while not stop.is_set():
                dev.append(abs(sim.pan_deg - 3.0))
                time.sleep(0.01)

        threading.Thread(target=watch, daemon=True).start()
        t0 = time.monotonic()
        cal = CalibrationRunner(client.send, src, lambda m: None, stream_kind="sim", model="AW-HE42").run()
        took = time.monotonic() - t0
        stop.set()
        # ~168 с на симуляторе (DECISIONS); предел 200 с — запас на загруженную машину (CI), «около 3 минут»
        assert took <= 200, took
        assert {0xD55, 0xEA9, 0xFFF} <= set(cal.zoom_points) and cal.zoom_points == sorted(cal.pan.points)
        for z in (0xD55, 0xEA9, 0xFFF):
            for code in (63, 70, 75):
                want = he42_deg_s(code, z) / hfov_at(z)
                assert abs(cal.pan.speed(code, z) / want - 1) < 0.15, (hex(z), code, cal.pan.speed(code, z), want)
            assert cal.pan.speed(90, z) > cal.pan.speed(75, z)  # быстрые коды — не скорость кода 75
        assert cal.tilt and all(abs(r - 1) < 0.1 for _, r in cal.tilt), cal.tilt
        assert (cal.model, cal.firmware, cal.stream_kind) == ("AW-HE42", None, "sim")
        assert max(dev) <= 0.5 * he42.HFOV_WIDE_DEG, max(dev)
        assert abs(sim.pan_deg - 3.0) <= 1.0 and abs(sim.tilt_deg + 8.0) <= 1.0
        assert abs(sim.zoom - 0.25) < 0.01
    finally:
        src.stop()
        srv.stop()


@pytest.mark.slow
@pytest.mark.parametrize(
    "step, zooms, codes, tilt_codes",
    [
        ("Поворот на зуме FFF", [0xFFF], (55, 70), ()),  # пробег на теле
        ("Наклон", [0x555], (70,), (63,)),
        ("Скорость зума", [0x555], (70,), ()),
    ],
)
def test_wizard_cancel_stops_returns_home_and_saves_nothing(step, zooms, codes, tilt_codes):
    """Оператор прервал мастер посреди движения (пробег на теле, наклон, замер зума): стоп этой оси отправлен
    сразу, камера вернулась на место (±1°, зум тоже), калибровки нет — run бросает CalibrationCancelled."""
    from ptztracker.control.calibration import CalibrationCancelled

    sim, srv, src, client = make()
    try:
        sim.pan_deg, sim.tilt_deg = 3.0, -8.0
        cancel = threading.Event()
        sent, t_cancel, cur = [], [], [None]

        def moving(cmd):
            return (cmd.startswith("#PTS") and cmd != "#PTS5050") or (cmd.startswith("#Z") and cmd != "#Z50")

        def on_sample(kind, data):
            if kind == "step":
                cur[0] = data["title"]
            elif kind == "cmd" and cur[0] == step and moving(data["cmd"]) and not t_cancel:
                t_cancel.append(None)  # первое движение этого шага: отмена через 0,3 с
                threading.Timer(0.3, lambda: (t_cancel.__setitem__(0, time.monotonic()), cancel.set())).start()

        def send(cmd):
            sent.append((time.monotonic(), cmd))
            return client.send(cmd)

        r = CalibrationRunner(send, src, lambda m: None, zooms=zooms, codes=codes, tilt_codes=tilt_codes,
                              cancel=cancel, on_sample=on_sample)
        with pytest.raises(CalibrationCancelled):
            r.run()
        assert t_cancel and t_cancel[0] is not None, step
        stop = "#Z50" if step == "Скорость зума" else "#PTS5050"
        stops = [t for t, c in sent if c == stop and t >= t_cancel[0]]
        # отмена видна в ближайшем опросе (темп камеры 0,15 с) или паузе (0,1 с), стоп — в следующем окне команд
        assert stops and stops[0] - t_cancel[0] < 0.5, (stops[:1], t_cancel)
        assert any(c == "#Z50" for t, c in sent if t >= t_cancel[0])
        assert abs(sim.pan_deg - 3.0) <= 1.0 and abs(sim.tilt_deg + 8.0) <= 1.0 and sim.zoom < 0.01
    finally:
        src.stop()
        srv.stop()


@pytest.mark.slow
def test_wizard_error_still_returns_home():
    """Сбой посреди пробега (не отмена, например связь): ошибка доходит до окна, но камера сначала возвращена."""
    sim, srv, src, client = make()
    try:
        sim.pan_deg, sim.tilt_deg = 3.0, -8.0
        failed = []

        def send(cmd):
            if cmd == "#PTS3750" and not failed:  # пробег поворота кодом 63 (к центру — влево)
                failed.append(cmd)
                raise RuntimeError("связь пропала")
            return client.send(cmd)

        with pytest.raises(RuntimeError, match="связь пропала"):
            CalibrationRunner(send, src, lambda m: None, zooms=[0x555], codes=(63, 70), tilt_codes=()).run()
        assert failed
        assert abs(sim.pan_deg - 3.0) <= 1.0 and abs(sim.tilt_deg + 8.0) <= 1.0 and sim.zoom < 0.01
    finally:
        src.stop()
        srv.stop()


@pytest.mark.slow
def test_wizard_reports_samples_and_survives_failing_observer():
    """Наблюдатель (окно мастера) получает положение, команды, шаги и итоги; его ошибка мастер не ломает."""
    sim, srv, src, client = make()
    try:
        got: dict[str, list] = {}

        def on_sample(kind, data):
            got.setdefault(kind, []).append(data)
            raise ValueError("сбой окна")

        cal = CalibrationRunner(client.send, src, lambda m: None, zooms=[0x555], codes=(63, 70), tilt_codes=(70,),
                                on_sample=on_sample).run()
        assert cal.tilt and cal.pan.speed(70, 0x555) > 0
        apc = got["apc"][-1]
        assert set(apc) == {"t", "pan", "tilt", "zoom"} and apc["zoom"] is not None
        cmds = [d["cmd"] for d in got["cmd"]]
        assert "#PTS7050" in cmds and "#PTS5070" in cmds and "#APC" not in cmds and "#GZ" not in cmds
        steps = got["step"]
        assert [d["index"] for d in steps] == list(range(steps[0]["total"])) and steps[0]["total"] == 5
        names = {d["name"] for d in got["result"]}
        assert {"tau", "video_latency", "dead_time", "pan", "tilt", "zoom"} <= names
        pan = next(d for d in got["result"] if d["name"] == "pan")
        assert pan["zoom"] == 0x555 and [c for c, _, _ in pan["codes"]] == [63, 70]
        for _, deg, model in pan["codes"]:
            assert abs(deg / model - 1) < 0.15  # симулятор = таблица he42
    finally:
        src.stop()
        srv.stop()
