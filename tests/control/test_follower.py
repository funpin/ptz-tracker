import time

import numpy as np
import pytest

from ptztracker.camera.session import CameraSession
from ptztracker.config import CameraConfig
from ptztracker.control.follower import APC_POLL_MOVING_S, GZ_POLL_STILL_S

pytestmark = pytest.mark.slow  # реальное время на симуляторе


def wait_until(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def sim_session(calibrated=False, script=None, **cfg):
    if calibrated:
        from ptztracker.control.camera_model import default_calibration

        cfg["calibration"] = default_calibration().to_json()  # как после мастера калибровки
    scene = None
    if script:
        from ptztracker.sim.scene import SyntheticStage

        scene = SyntheticStage(script=script)
    s = CameraSession(CameraConfig(id="s", name="Сим", ip="sim", kind="sim", **cfg), scene=scene)
    s.start()
    assert wait_until(lambda: s.analyzer.latest() is not None and len(s.analyzer.latest().tracks) > 0, 4)
    return s


def select_person(s):
    tr = s.analyzer.latest().tracks[0]
    assert s.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2) is not None
    assert wait_until(lambda: s.analyzer.latest().target_state == "tracking", 2)


def last_pt(s):
    return [c for c in s.sim.received if c.startswith("#PTS")][-1]


def test_enable_requires_target():
    s = sim_session()
    try:
        assert s.follower.enable() is False
        select_person(s)
        assert s.follower.enable() is True and s.follower.enabled
    finally:
        s.stop()


def test_enable_refused_while_polling_paused():
    s = sim_session()
    try:
        select_person(s)
        s.follower.pause_polling()
        before = len(s.sim.received)
        assert s.follower.enable() is False and not s.follower.enabled
        time.sleep(0.5)
        assert [c for c in list(s.sim.received)[before:] if c.startswith(("#PTS", "#Z"))] == []
        s.follower.resume_polling()
        assert s.follower.enable() is True
    finally:
        s.stop()


def test_pause_polling_blocks_enable_before_disabling():
    """Калибровка (рабочий поток): запрет включения встаёт раньше выключения — между ними включить нельзя."""
    s = sim_session()
    try:
        select_person(s)
        assert s.follower.enable() is True
        seen = []
        s.follower.on_change = lambda on, reason: seen.append((on, s.follower.polling_paused))
        s.follower.pause_polling()
        assert seen == [(False, True)]
        assert not s.follower.enabled
    finally:
        s.stop()


def test_follows_walking_sim_person():
    s = sim_session(composition="full", calibrated=True, script="lecture")
    try:
        select_person(s)
        assert s.follower.enable()
        errs, in_frame = [], []
        t_end = time.monotonic() + 16  # «lecture»: стоит 8 с, затем прохаживается
        while time.monotonic() < t_end:
            fr = s.frames.latest()
            kp = fr.meta["people"][0]
            in_frame.append(kp[0, 2] > 0)
            if time.monotonic() > t_end - 8 and kp[0, 2] > 0:
                errs.append(abs(kp[0, 0] / fr.image.shape[1] - 0.5))
            time.sleep(0.1)
        assert all(in_frame), "человек выходил из кадра"
        assert np.mean(errs) < 0.10, f"средняя ошибка {np.mean(errs):.3f}"
    finally:
        s.stop()


def test_manual_joystick_disables_immediately():
    s = sim_session()
    try:
        select_person(s)
        s.follower.enable()
        time.sleep(1.0)
        s.set_pan_tilt_velocity(0.5, 0)
        assert not s.follower.enabled and s.follower.last_reason == "ручное управление"
        time.sleep(0.6)
        assert last_pt(s) == "#PTS7550"  # ручная команда, автоматика больше не шлёт
    finally:
        s.stop()


def test_emergency_stop_disables():
    s = sim_session()
    try:
        select_person(s)
        s.follower.enable()
        s.emergency_stop()
        assert not s.follower.enabled and s.follower.last_reason == "аварийный стоп"
    finally:
        s.stop()


def test_stale_analysis_stops_camera():
    s = sim_session()
    try:
        select_person(s)
        s.follower.enable()
        time.sleep(0.5)
        s.analyzer.stop()
        assert wait_until(lambda: not s.follower.enabled, 1.5)
        time.sleep(0.4)
        assert last_pt(s) == "#PTS5050"
    finally:
        s.stop()


def test_target_lost_stops_camera():
    s = sim_session(composition="full")
    try:
        select_person(s)
        s.follower.enable()
        time.sleep(2.0)
        s.analyzer.clear_target()
        assert wait_until(lambda: last_pt(s).startswith("#PTS50"), 1.5)
        assert s.follower.enabled  # слежение остаётся включённым — ждёт цель
    finally:
        s.stop()


def test_enable_reads_zoom_immediately():
    s = sim_session()
    try:
        s.sim.zoom = 0.5
        time.sleep(0.2)
        select_person(s)
        assert s.follower.enable()  # Qt не ждёт #GZ: зум спрашивает поток опроса сразу после включения
        want = 0x555 + 0.5 * (0xFFF - 0x555)
        # сразу, но первый запрос может получить er2 от занятого симулятора и уйти повторно: 1 с мало (замер 0,3 или 1,9 с)
        deadline = time.monotonic() + 2.5
        while abs(s.follower._zoom_pos - want) >= 30 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert abs(s.follower._zoom_pos - want) < 30
    finally:
        s.stop()


def test_enable_clears_stale_apc_sample():
    s = sim_session()
    try:
        select_person(s)
        s.follower._pan_tilt, s.follower._pan_tilt_t = (0x8000, 0x8000), 1.0  # ответ #APC прошлого включения
        assert s.follower.enable()
        assert s.follower._pan_tilt_t != 1.0  # сторожу нового включения — только новые ответы
    finally:
        s.stop()


def test_preset_recall_not_cancelled_by_follow_stop():
    s = sim_session()
    try:
        select_person(s)
        s.follower.enable()
        time.sleep(1.0)
        s.recall_preset(2)
        time.sleep(0.8)
        rec = list(s.sim.received)
        i_r = max(i for i, c in enumerate(rec) if c == "#R02")
        assert not any(c.startswith(("#PTS", "#Z")) for c in rec[i_r + 1 :]), rec[i_r:]
    finally:
        s.stop()


def test_divergence_disables_with_calibration_hint():
    s = sim_session()
    try:
        select_person(s)
        s.follower.enable()
        s.follower.controller.diverged = True
        assert wait_until(lambda: not s.follower.enabled, 1.0)
        assert "калибровк" in s.follower.last_reason.lower()
    finally:
        s.stop()


def test_uncalibrated_uses_conservative_table():
    from ptztracker.camera.session import UNCALIBRATED_SAFETY
    from ptztracker.control.camera_model import default_calibration

    s = CameraSession(CameraConfig(id="x", name="X", ip="sim", kind="sim"), analysis=False)
    want = UNCALIBRATED_SAFETY * default_calibration().pan.speed(70, 0x73F)
    assert UNCALIBRATED_SAFETY > 1 and abs(s.calibration().pan.speed(70, 0x73F) - want) < 1e-9


def test_zoom_model_matches_camera_on_sim():
    """Review Focus 3 (часть 1): масштаб в модели регулятора отличается от камеры меньше чем на 10%.

    Часть 0, B2: модель считала зум ×3,9 при факте ×9,4 — команды зума ждали за поворотом до 2,4 с.
    """
    import math

    s = sim_session(calibrated=True, script="walk")
    try:
        select_person(s)
        assert s.follower.enable()
        errs, truth = [], []
        t_end = time.monotonic() + 20
        while time.monotonic() < t_end and s.follower.enabled:
            with s.follower._lock:
                ctrl = s.follower.controller
                ref = ctrl._gz_ref
                model = None if ref is None else ref + ctrl.zdyn.position(time.monotonic())
                real = math.log(s.sim.zoom_ratio())  # правда симулятора: ln масштаба от широкого угла
            truth.append(real)
            if model is not None:
                errs.append(abs(math.exp(model - real) - 1))
            time.sleep(0.1)
        assert max(truth) - min(truth) > 0.3, "зум почти не двигался — проверять нечего"
        assert max(errs) < 0.10, f"модель зума расходится с камерой на {max(errs):.0%}"
    finally:
        s.stop()


def test_speed_adapts_on_sim_when_camera_faster_than_table():
    """Часть 1, задача 5: таблица калибровки в 1,6 раза медленнее камеры (симулятор с перспективой).
    За 30 с прохаживания слежение по #APC находит поправку 1,6 ± 0,15, раскачки нет (слежение не выключилось)."""
    from ptztracker.control.camera_model import default_calibration

    cfg = {"calibration": default_calibration(safety=1 / 1.6).to_json()}
    s = sim_session(composition="full", script="lecture", **cfg)
    try:
        select_person(s)
        assert s.follower.enable()
        t_end = time.monotonic() + 34  # «lecture»: стоит 8 с, затем прохаживается
        while time.monotonic() < t_end and s.follower.enabled:
            time.sleep(0.2)
        assert s.follower.enabled, s.follower.last_reason
        scale = s.follower.speed_scale
        assert abs(scale["pan"] - 1.6) <= 0.15, scale
    finally:
        s.stop()


def test_zoom_555_aaa_555_while_walking_no_swing_on_sim():
    """Часть 2, задача 2: симулятор как живая HE42 — °/с от зума не зависят, в долях кадра на приближении камера
    в разы быстрее, чем на общем плане. Без калибровки (таблица в °/с с запасом UNCALIBRATED_SAFETY):
    «Пояс» с общего плана — наезд дальше 0xAAA, прохаживание на приближении, затем на ходу «Общий» — отъезд
    к общему плану (не дальше 0x6EB: «Общему» нужен чуть больший человек, чем на 555). Слежение не выключается (сторож молчит), нос в кадре, поправка — около 1 / запас, у наклона —
    как у поворота. Было (таблица одна на все зумы): на площадке поправка упёрлась в 2,0, наклон качался."""
    from ptztracker.camera.session import UNCALIBRATED_SAFETY
    from ptztracker.control.composition import COMPOSITIONS

    s = sim_session(script="lecture")
    try:
        select_person(s)
        assert s.follower.enable()
        t0, zooms, xs, switched = time.monotonic(), [], [], None
        while time.monotonic() - t0 < 42 and s.follower.enabled:
            el = time.monotonic() - t0
            if switched is None and el > 28:  # «lecture»: со 26 с идёт влево — отъезд на ходу
                s.follower.set_composition(COMPOSITIONS["wide"])
                switched = len(zooms)
            fr = s.frames.latest()
            kp = fr.meta["people"][0]
            zooms.append(s.sim.zoom)
            if kp[0, 2] > 0:
                xs.append(kp[0, 0] / fr.image.shape[1])
            time.sleep(0.05)
        assert s.follower.enabled, s.follower.last_reason
        assert max(zooms[:switched]) >= 0.5 and zooms[-1] <= 0.2, (max(zooms[:switched]), zooms[-1])
        assert 0.1 <= min(xs) and max(xs) <= 0.9, (min(xs), max(xs))
        scale = s.follower.speed_scale
        assert abs(scale["pan"] * UNCALIBRATED_SAFETY - 1) <= 0.15 and scale["tilt"] == scale["pan"], scale
    finally:
        s.stop()


def _count(s, start):
    cmds = list(s.sim.received)[start:]
    return {k: sum(1 for c in cmds if c.startswith(k)) for k in ("#APC", "#GZ")}, len(cmds)


def test_polls_slow_down_while_camera_stands():
    """Камера стоит: #APC ~1/с, #GZ не чаще раза в 2 с."""
    s = sim_session(composition="full", calibrated=True, script="stand")
    try:
        select_person(s)
        assert s.follower.enable()
        t_still, t_end = None, time.monotonic() + 16.0  # кадр доводится ~9 с, затем коды — стоп
        while time.monotonic() < t_end:
            if s.follower.codes != (50, 50, 50):
                t_still = None
            elif t_still is None:
                t_still = time.monotonic()
            elif time.monotonic() - t_still > 1.5:
                break
            time.sleep(0.05)
        assert t_still is not None and time.monotonic() - t_still > 1.5, "камера не успокоилась"
        n0, t0 = len(s.sim.received), time.monotonic()
        time.sleep(6.0)
        polls, _ = _count(s, n0)
        dt = time.monotonic() - t0
        assert dt * 0.7 <= polls["#APC"] <= dt * 1.0 + 1.5, polls
        assert polls["#GZ"] <= dt / 2.0 + 1.5, polls
    finally:
        s.stop()


def test_polls_are_fewer_while_walking():
    """Человек идёт: #APC не чаще 0,5 с, #GZ — пока зум стоит — раз в 2 с.

    До изменения оба опроса шли каждые 0,5 с (по 16 за 8 с, вместе 32); доля опросов среди запросов
    на симуляторе зависит от числа команд движения (они уходят только при смене кода), поэтому считаем сами опросы.
    """
    s = sim_session(composition="full", calibrated=True, script="lecture")
    try:
        select_person(s)
        assert s.follower.enable()
        time.sleep(9.5)  # первые 8 с человек стоит, камера доводит кадр
        n0, t0 = len(s.sim.received), time.monotonic()
        time.sleep(8.0)
        polls, _ = _count(s, n0)
        dt = time.monotonic() - t0
        assert polls["#APC"] <= dt / APC_POLL_MOVING_S + 1.5, polls
        assert polls["#GZ"] <= dt / GZ_POLL_STILL_S + 2.5, polls  # зум почти не едет: редкий опрос
        assert polls["#APC"] + polls["#GZ"] <= 0.7 * 2 * dt / 0.5, polls
    finally:
        s.stop()
