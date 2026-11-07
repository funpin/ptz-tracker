import time

import pytest

from ptztracker.camera.scheduler import Priority
from ptztracker.camera.session import CameraSession
from ptztracker.config import CameraConfig


def sim_cfg():
    return CameraConfig(id="s", name="Сим", ip="sim", kind="sim")


def boom(*a, **k):
    raise RuntimeError("boom")


def test_sim_session_manual_pan():
    s = CameraSession(sim_cfg())
    s.start()
    try:
        s.set_pan_tilt_velocity(1, 0)
        time.sleep(0.6)
        s.set_pan_tilt_velocity(0, 0)
        time.sleep(2.5)  # симулятор тормозит как HE42 (~0.8 с + хвост)
        assert s.sim.pan_deg > 5
        p = s.sim.pan_deg
        time.sleep(0.3)
        assert abs(s.sim.pan_deg - p) < 0.2
    finally:
        s.stop()


def test_sim_session_has_video_and_online():
    s = CameraSession(sim_cfg())
    s.start()
    try:
        time.sleep(0.8)
        assert s.frames.latest() is not None
        assert s.online is True
    finally:
        s.stop()


def test_zoom_and_presets_reach_camera():
    s = CameraSession(sim_cfg())
    s.start()
    try:
        s.set_zoom_velocity(1.0)
        time.sleep(0.4)
        s.set_zoom_velocity(0)
        s.store_preset(2)
        s.set_iris(1.0)
        s.set_focus_auto(False)
        time.sleep(0.8)
        rec = list(s.sim.received)
        assert "#Z99" in rec and "#Z50" in rec and "#M02" in rec
        assert "#AXIFFF" in rec and "#D10" in rec
    finally:
        s.stop()


def test_stop_sends_emergency_stop():
    s = CameraSession(sim_cfg())
    s.start()
    s.set_pan_tilt_velocity(1, 0)
    time.sleep(0.3)
    s.stop()
    pts = [c for c in s.sim.received if c.startswith("#PTS")]
    assert pts[-1] == "#PTS5050"
    assert "#Z50" in s.sim.received


def test_unreachable_camera_session_does_not_block():
    s = CameraSession(CameraConfig(id="x", name="X", ip="10.255.255.1"))
    t0 = time.monotonic()
    s.start()
    s.set_pan_tilt_velocity(1, 0)
    assert time.monotonic() - t0 < 0.5
    time.sleep(1.5)
    assert s.online is False
    t0 = time.monotonic()
    s.stop()
    assert time.monotonic() - t0 < 3.5


def test_stop_does_not_freeze_ui_on_dead_camera():
    # Final review I5: камера «висит» (TCP принимает, но не отвечает) — смена камеры/закрытие не подвешивают окно
    import socket

    hole = socket.socket()
    hole.bind(("127.0.0.1", 0))
    hole.listen(50)
    host = f"127.0.0.1:{hole.getsockname()[1]}"
    try:
        s = CameraSession(CameraConfig(id="x", name="X", ip=host))
        s.start()
        s.set_pan_tilt_velocity(1, 0)
        time.sleep(1.2)
        t0 = time.monotonic()
        s.stop()
        assert time.monotonic() - t0 < 0.7
    finally:
        hole.close()


def test_observe_only_session_sends_no_movement_or_stops():
    """Сессия наблюдения: ни ручные команды, ни аварийный стоп, ни stop() не шлют камере движений и стопов."""
    s = CameraSession(sim_cfg(), observe_only=True)
    s.start()
    try:
        s.set_pan_tilt_velocity(1, 0)
        s.set_zoom_velocity(1)
        s.emergency_stop()
        time.sleep(0.5)
    finally:
        s.stop()
    time.sleep(0.3)
    assert [c for c in list(s.sim.received) if c.startswith(("#PTS", "#Z", "#D1", "#AXF", "#F"))] == []


# --- питание и изображение ---


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def test_power_from_heartbeat():
    s = CameraSession(sim_cfg(), analysis=False)
    assert s.power == "unknown"
    s.start()
    try:
        assert _wait(lambda: s.power == "on")
    finally:
        s.stop()


def test_standby_roundtrip_blocks_motion():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        s.sim.power_on_s = 0.5
        assert _wait(lambda: s.power == "on")
        s.set_power(False)
        assert _wait(lambda: s.power == "standby", 1.0)
        n = len([c for c in s.sim.received if c.startswith("#PTS")])
        s.set_pan_tilt_velocity(0.5, 0)
        s.recall_preset(3)
        time.sleep(0.5)
        assert len([c for c in s.sim.received if c.startswith("#PTS")]) == n
        assert "#R03" not in s.sim.received
        s.set_power(True)
        assert _wait(lambda: s.power in ("switching", "on"), 1.0)
        assert _wait(lambda: s.power == "on", 4.0)
    finally:
        s.stop()


def test_power_on_from_unknown_state_keeps_emergency_stop():
    """Состояние питания неизвестно (нет ответа опроса), а камера включена: ответ p1 на #O1 не должен переводить
    в «switching» — там аварийный стоп стопов не шлёт (и теряет MANUAL-стопы, которые только что поставил disable())."""
    from ptztracker.camera import protocol as P

    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.power == "on")
        s._power = "unknown"
        s._on_reply({"cmd": P.POWER_ON, "reply": "p1", "ok": True})
        assert s.power == "on"
        s.set_pan_tilt_velocity(0.5, 0)
        time.sleep(0.3)
        n = len([c for c in s.sim.received if c.startswith("#PTS")])
        s.emergency_stop()
        assert _wait(lambda: len([c for c in s.sim.received if c.startswith("#PTS")]) > n and
                     [c for c in s.sim.received if c.startswith("#PTS")][-1] == "#PTS5050", 1.5)
    finally:
        s.stop()


def test_power_on_from_standby_goes_through_switching():
    from ptztracker.camera import protocol as P

    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.power == "on")
        s._power = "standby"
        s._on_reply({"cmd": P.POWER_ON, "reply": "p1", "ok": True})
        assert s.power == "switching"
    finally:
        s.stop()


def test_power_off_runs_manual_first():
    s = CameraSession(sim_cfg(), analysis=False)
    calls = []
    s.on_manual = lambda: calls.append("manual")
    s.start()
    try:
        s.set_power(False)
        assert calls == ["manual"]
        assert _wait(lambda: "#O0" in s.sim.received, 1.0)
    finally:
        s.stop()


def test_image_state_read_and_set():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.image.gain == 0x08 and s.image.wb == 0 and s.image.color_temp == 0x1A)
        s.set_gain(0x0B)
        s.set_wb(9)
        s.set_color_temp(0x20)
        assert _wait(lambda: s.image.gain == 0x0B and s.image.wb == 9 and s.image.color_temp == 0x20, 1.5)
    finally:
        s.stop()


def test_rejected_gain_falls_back_to_camera_value():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.image.gain == 0x08)
        s.scheduler.submit("gain", "OGU:09", Priority.MANUAL)
        s.refresh_image()
        time.sleep(0.8)
        assert s.image.gain == 0x08
    finally:
        s.stop()


def test_unknown_power_reply_keeps_commands_flowing():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        orig = s.sim._dispatch
        s.sim._dispatch = lambda cmd: "ER1:O" if cmd == "#O" else orig(cmd)
        time.sleep(2.5)
        assert s.power == "unknown"
        s.recall_preset(4)
        assert _wait(lambda: "#R04" in s.sim.received, 1.0)
    finally:
        s.stop()


def test_iris_step_from_auto_switches_manual_without_jump():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.image.iris == s.sim.iris_pos)
        x = s.sim.iris_pos
        assert s.sim.iris_auto
        s.iris_step(+1)
        # шаг стоит в очереди за дюжиной стартовых запросов (по 130+ мс): на сервере сборки — больше 2 с
        assert _wait(lambda: any(c.startswith("#AXI") for c in s.sim.received), 5.0)
        assert s.sim.received.index("#D30") < next(i for i, c in enumerate(s.sim.received) if c.startswith("#AXI"))
        assert not s.sim.iris_auto
        assert 0 < s.sim.iris_pos - x <= 0x1000 // 16 + 1
    finally:
        s.stop()


def test_iris_step_unknown_queries_then_applies():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        s._image = type(s._image)()  # значение ещё неизвестно
        x = s.sim.iris_pos
        s.iris_step(-1)
        assert _wait(lambda: s.sim.iris_pos < x, 5.0)  # за стартовыми запросами, как выше
    finally:
        s.stop()


def test_iris_step_does_not_disable_follow():
    s = CameraSession(sim_cfg(), analysis=False)
    calls = []
    s.on_manual = lambda: calls.append(1)
    s.start()
    try:
        assert _wait(lambda: s.image.iris is not None)
        s.iris_step(+1)
        s.gain_step(+1)
        time.sleep(0.4)
        assert calls == []
    finally:
        s.stop()


def test_gain_step_clamps_and_goes_manual_from_auto():
    from ptztracker.camera import protocol as P

    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.image.gain == 0x08)
        s.gain_step(+1)
        assert _wait(lambda: s.image.gain == 0x0B and s.sim.gain_code == 0x0B, 1.5)
        for _ in range(3):
            s.gain_step(-1)
        assert _wait(lambda: s.sim.gain_code == 0x08, 1.5)
        s.gain_step(-1)
        time.sleep(0.3)
        assert s.sim.gain_code == 0x08
        s.set_gain(P.GAIN_AUTO)
        assert _wait(lambda: s.image.gain == P.GAIN_AUTO, 1.5)
        s.gain_step(+1)
        assert _wait(lambda: s.sim.gain_code == 0x08, 1.5)
    finally:
        s.stop()


def test_iris_step_stale_cache_requeries():
    from ptztracker.camera import protocol as P

    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.image.iris == s.sim.iris_pos)
        s.iris_step(+1)  # теперь кэш «ручной»
        assert _wait(lambda: not s.sim.iris_auto, 1.5)
        time.sleep(1.2)  # кэш устарел
        s.sim.iris_pos = 0x900  # камера изменилась без нас
        assert s.iris_step(+1) is True
        assert _wait(lambda: abs(s.sim.iris_pos - (0x900 + (P.LENS_MAX - P.LENS_MIN) // 16)) <= 1, 2.0)
    finally:
        s.stop()


@pytest.mark.parametrize("d", [+1, -1])
def test_gain_step_auto_without_manual_goes_to_0db(d):
    """Ручного значения не видели: первое нажатие снимает «Авто» и ставит 0 dB (решение 2026-11-05), без шага."""
    from ptztracker.camera import protocol as P

    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.image.gain is not None)
        s.set_gain(P.GAIN_AUTO)
        assert _wait(lambda: s.sim.gain_code == P.GAIN_AUTO and s.image.gain == P.GAIN_AUTO, 1.5)
        s._last_manual_gain = None
        assert s.gain_step(d) is True
        assert s.image.gain == P.GAIN_CODES[0]
        assert _wait(lambda: s.sim.gain_code == P.GAIN_CODES[0], 1.5)
        assert s.gain_step(+1) is True  # дальше — обычные шаги
        assert _wait(lambda: s.sim.gain_code == P.GAIN_CODES[1], 1.5)
    finally:
        s.stop()


def test_gain_step_unknown_auto_without_manual_goes_to_0db_after_reply():
    from ptztracker.camera import protocol as P

    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.image.gain is not None)
        s.set_gain(P.GAIN_AUTO)
        assert _wait(lambda: s.sim.gain_code == P.GAIN_AUTO, 5.0)
        s._image = type(s._image)()  # ещё не знаем, что на камере
        s._last_manual_gain = None
        assert s.gain_step(+1) is True
        assert _wait(lambda: s.sim.gain_code == P.GAIN_CODES[0], 5.0)  # за стартовыми запросами
    finally:
        s.stop()


def test_gain_step_auto_with_manual_returns_to_it_without_step():
    from ptztracker.camera import protocol as P

    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.image.gain == 0x08)
        s.set_gain(0x0B)
        assert _wait(lambda: s.sim.gain_code == 0x0B)
        s.set_gain(P.GAIN_AUTO)
        assert _wait(lambda: s.image.gain == P.GAIN_AUTO, 1.5)
        assert s.gain_step(+1) is True
        assert _wait(lambda: s.sim.gain_code == 0x0B, 1.5)
    finally:
        s.stop()


def test_pending_step_dropped_on_failed_query_standby_and_timeout():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    t = time.monotonic()
    s._pending_steps["iris"] = (1, t)
    s._on_reply({"cmd": "#GI", "reply": "ER1", "ok": False})
    assert "iris" not in s._pending_steps
    s._pending_steps["gain"] = (1, t)
    s._on_reply({"cmd": "#O0", "reply": "p0", "ok": True})
    assert s._pending_steps == {}
    s._pending_steps["iris"] = (1, t - 3.0)
    s._upd(iris=0x800)
    n = len(s.sim.received)
    s._on_reply({"cmd": "#GI", "reply": "gi800", "ok": True})
    assert len(s.sim.received) == n and "iris" not in s._pending_steps
    s.stop()


def test_iris_step_after_camera_side_auto_resends_manual():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.image.iris is not None)
        s.iris_step(+1)
        assert _wait(lambda: not s.sim.iris_auto, 1.5)
        time.sleep(1.2)
        s.sim.iris_auto = True  # оператор включил авто на пульте камеры
        n = len(s.sim.received)
        s.iris_step(+1)
        assert _wait(lambda: any(c.startswith("#AXI") for c in list(s.sim.received)[n:]), 2.0)
        new = list(s.sim.received)[n:]
        assert "#D30" in new and new.index("#D30") < next(i for i, c in enumerate(new) if c.startswith("#AXI"))
    finally:
        s.stop()


def test_gain_auto_without_manual_stale_cache_requeries():
    from ptztracker.camera import protocol as P

    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.image.gain == 0x08)
        s._last_manual_gain = None
        s._upd(gain=P.GAIN_AUTO)
        s._img_t["gain"] -= 5.0  # кэш устарел
        assert s.gain_step(+1) is True  # не отказ по старому AUTO: спрашиваем камеру
        assert "gain" in s._pending_steps or s.image.gain == 0x0B
        assert _wait(lambda: s.sim.gain_code == 0x0B, 2.0)  # камера реально на 0x08 — не авто
    finally:
        s.stop()


def test_standby_does_not_flood_queue_and_power_button_works():
    # ревью: в ожидании камера отвечает ER2 на запросы и стопы, а er2 планировщик повторял без конца —
    # пульс #O голодал, а #O1 после Esc устаревал, и камера не включалась
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        s.sim.power_on_s = 0.5
        assert _wait(lambda: s.power == "on")
        s.set_power(False)
        assert _wait(lambda: s.power == "standby", 1.0)
        s.refresh_image()
        s.emergency_stop()
        time.sleep(0.3)
        n0 = len(s.sim.received)
        time.sleep(2.2)
        sent = list(s.sim.received)[n0:]
        assert "#O" in sent  # пульс идёт
        assert len(sent) <= 4, sent  # и больше ничего не повторяется
        s.set_power(True)
        assert _wait(lambda: s.power == "on", 4.0)
    finally:
        s.stop()


def test_standby_from_remote_counts_as_manual_and_clears_queue():
    s = CameraSession(sim_cfg(), analysis=False)
    calls = []
    s.on_manual = lambda: calls.append("manual")
    s.start()
    try:
        assert _wait(lambda: s.power == "on")
        s.sim._standby = True  # выключили с пульта (handle() мог бы попасть в окно 130 мс и получить eR2)
        assert _wait(lambda: s.power == "standby", 3.0)
        assert calls == ["manual"]
        assert not s.scheduler.pending() or _wait(lambda: not s.scheduler.pending(), 2.5)
    finally:
        s.stop()


def test_gain_resent_after_remote_change():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.image.gain == 0x08)
        s.set_gain(0x0B)
        assert _wait(lambda: s.sim.gain_code == 0x0B, 1.0)
        s.sim.gain_code = 0x08  # вернули с пульта
        s.set_gain(0x0B)
        assert _wait(lambda: s.sim.gain_code == 0x0B, 1.0)
    finally:
        s.stop()


# --- модель камеры и возможности, которых у неё нет ---------------------

def test_session_asks_model_on_connect_and_exposes_it():
    s = CameraSession(sim_cfg(), analysis=False)
    assert s.model is None and s.firmware is None
    s.start()
    try:
        assert _wait(lambda: s.model == "AW-HE42", 2.0)
        assert s.sim.received.count("QID") == 1  # один раз, не на каждом пульсе
        time.sleep(2.2)
        assert s.sim.received.count("QID") == 1
        info = s.camera_info()
        assert (info.model, info.firmware, info.unsupported) == ("AW-HE42", None, ())
        assert s.profile.name == "AW-HE42"
    finally:
        s.stop()


def test_model_from_config_is_shown_before_the_answer_and_replaced_by_it():
    cfg = sim_cfg()
    cfg.model, cfg.firmware = "AW-HE40", "1.54"
    s = CameraSession(cfg, analysis=False)
    assert (s.model, s.firmware) == ("AW-HE40", "1.54")
    s.start()
    try:
        assert _wait(lambda: s.model == "AW-HE42", 2.0)  # камера по тому же адресу другая — верим ответу
        assert s.firmware == "1.54"
    finally:
        s.stop()


def test_model_query_does_not_block_start():
    s = CameraSession(sim_cfg(), analysis=False)
    t0 = time.monotonic()
    s.start()
    try:
        assert time.monotonic() - t0 < 1.0
    finally:
        s.stop()


def test_model_is_asked_again_after_busy_and_not_after_er1():
    s = CameraSession(sim_cfg(), analysis=False)
    s._on_reply({"cmd": "QID", "reply": "Камера ответила ошибкой er2: ER2:QID", "ok": False})
    assert s._model_answered is False  # занята или в ожидании — спросим позже
    s._on_reply({"cmd": "QID", "reply": "Камера ответила ошибкой er1: ER1:QID", "ok": False})
    assert s._model_answered is True and s.model is None  # QID не знает — больше не спрашиваем
    s2 = CameraSession(sim_cfg(), analysis=False)
    s2._on_reply({"cmd": "QID", "reply": "OID:AW-HE40", "ok": True})
    assert s2._model_answered is True and s2.model == "AW-HE40"
    s3 = CameraSession(sim_cfg(), analysis=False)
    s3._on_reply({"cmd": "QID", "reply": "мусор", "ok": True})
    assert s3._model_answered is True and s3.model is None


def test_changed_model_forgets_unsupported_features():
    cfg = sim_cfg()
    cfg.model, cfg.unsupported = "AW-HE40", ["gain"]
    s = CameraSession(cfg, analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.model == "AW-HE42", 2.0)
        assert s.camera_info().unsupported == ()
    finally:
        s.stop()


@pytest.mark.parametrize("cmd,feature", [
    ("QGU", "gain"), ("OGU:0B", "gain"), ("QAW", "wb"), ("OAW:4", "wb"),
    ("QSD:B1", "color_temp"), ("OSD:B1:01A", "color_temp"), ("#O", "power"), ("#O1", "power"), ("#O0", "power"),
])
def test_er1_marks_feature_unsupported_and_success_clears_it(cmd, feature):
    s = CameraSession(sim_cfg(), analysis=False)  # без start: только разбор ответов
    s._on_reply({"cmd": cmd, "reply": "Камера ответила ошибкой er1: ER1:X", "ok": False})
    assert s.camera_info().unsupported == (feature,)
    assert s.supports(feature) is False
    s._on_reply({"cmd": cmd, "reply": "OGU:08", "ok": True})
    assert s.camera_info().unsupported == () and s.supports(feature) is True


def test_other_errors_and_other_commands_do_not_mark_anything():
    s = CameraSession(sim_cfg(), analysis=False)
    s._on_reply({"cmd": "OGU:09", "reply": "Камера ответила ошибкой er3: ER3:OGU", "ok": False})  # вне диапазона
    s._on_reply({"cmd": "QGU", "reply": "ER2:QGU", "ok": False})  # занята / ожидание
    s._on_reply({"cmd": "#GI", "reply": "ER1", "ok": False})  # ирис не из списка
    s._on_reply({"cmd": "#PTS5050", "reply": "ER1", "ok": False})
    assert s.camera_info().unsupported == ()


def test_unsupported_from_config_is_remembered_and_listed_sorted():
    cfg = sim_cfg()
    cfg.unsupported = ["wb", "gain"]
    s = CameraSession(cfg, analysis=False)
    assert s.camera_info().unsupported == ("gain", "wb")
    assert s.supports("gain") is False and s.supports("power") is True


def test_er1_on_a_live_session_reaches_the_snapshot():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        orig = s.sim._dispatch
        s.sim._dispatch = lambda cmd: "ER1:QGU" if cmd == "QGU" else orig(cmd)
        s.refresh_image()
        assert _wait(lambda: not s.supports("gain"), 2.0)
        assert s.supports("wb")
    finally:
        s.stop()


def test_recording_meta_has_live_model():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.model is not None, 5.0)  # QID — в конце стартовых запросов
        assert s.recording_meta()["camera_model"] == "AW-HE42"
    finally:
        s.stop()


# --- Task 5 (несколько камер): анализ на ходу и положение P/T/Z ---------------------------------


def test_analysis_on_off_at_runtime():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert s.analyzer is None and s.follower is None
        s.set_analysis(True)
        assert _wait(lambda: s.analyzer.latest() is not None)
        assert s.follower is not None
        f = s.follower
        s.set_analysis(True)  # повтор — ничего
        assert s.follower is f
        assert _wait(lambda: len(s.analyzer.latest().tracks) > 0)
        tr = s.analyzer.latest().tracks[0]
        s.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2)
        assert _wait(lambda: s.analyzer.latest().target_state == "tracking")
        assert f.enable() is True
        s.set_analysis(False)
        assert s.analyzer is None and s.follower is None
        assert f.enabled is False and f.last_reason == "анализ отдан другой камере"
    finally:
        s.stop()


def test_disable_analysis_turns_tracking_off_first():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        s.set_analysis(True)
        calls = []

        class Spy:
            enabled = True

            def disable(self, reason):
                calls.append(("disable", reason))

            def stop(self):
                calls.append(("stop",))

        real = s.follower
        s.follower = Spy()
        s.set_analysis(False)
        real.stop()
        assert [c[0] for c in calls] == ["disable", "stop"]
    finally:
        s.stop()


def test_ptz_from_poll():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        s.sim.pan_deg = 10.0
        s.sim.tilt_deg = -5.0
        s.poll_position()
        assert _wait(lambda: s.ptz is not None and abs(s.ptz.pan_deg - 10.0) < 0.5)
        assert abs(s.ptz.tilt_deg + 5.0) < 0.5
        assert _wait(lambda: s.ptz.zoom_x is not None)
        assert 1.0 <= s.ptz.zoom_x <= 20.0
    finally:
        s.stop()


def test_ptz_history_trimmed():
    from ptztracker.camera.session import PtzPosition

    s = CameraSession(sim_cfg(), analysis=False)
    now = time.monotonic()
    for dt in (40.0, 20.0, 1.0):
        s._add_ptz(PtzPosition(now - dt, 0.0, 0.0, 1.0))
    assert [round(now - p.t) for p in s.ptz_history()] == [20, 1]
    assert [round(now - p.t) for p in s.ptz_history(5.0)] == [1]


def test_poll_position_rate_limited():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        s.poll_position()
        assert _wait(lambda: s.ptz is not None)
        n = sum(1 for c in s.sim.received if c == "#APC")
        s.poll_position()
        time.sleep(0.3)
        assert sum(1 for c in s.sim.received if c == "#APC") == n
    finally:
        s.stop()


def test_set_analysis_after_stop_does_nothing():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    s.stop()
    s.set_analysis(True)
    assert s.analyzer is None and s.follower is None


def test_poll_position_silent_in_standby():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        s._power = "standby"
        s.poll_position()
        time.sleep(0.3)
        assert "#APC" not in list(s.sim.received)
    finally:
        s.stop()


def test_bad_ptz_reply_ignored():
    s = CameraSession(sim_cfg(), analysis=False)
    s._on_reply({"cmd": "#APC", "reply": "мусор", "ok": True})
    s._on_reply({"cmd": "#APC", "reply": "er2", "ok": False})
    assert s.ptz is None


def test_zoom_reply_does_not_suppress_pan_tilt_poll():
    s = CameraSession(sim_cfg(), analysis=False)
    s._on_reply({"cmd": "#APC", "reply": "aPC80008000", "ok": True})
    s._apc_t = time.monotonic() - 1.0  # #APC давно
    s._ptz_poll_t = -1e9
    s._on_reply({"cmd": "#GZ", "reply": "gz800", "ok": True})

    class Sch:
        sent = []

        def submit(self, key, cmd, *a, **k):
            self.sent.append(cmd)

    s.scheduler = Sch()
    s._power = "on"
    s.poll_position()
    assert "#APC" in Sch.sent


def test_rare_image_settings_reach_camera_and_state():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        assert _wait(lambda: s.image.shutter == 0 and s.image.pedestal == 0 and s.image.detail == 1)
        s.set_shutter(0xB)
        s.set_synchro(0x20)
        s.set_pedestal(-3)
        s.set_detail(2)
        s.set_preset_speed(30)
        assert _wait(lambda: (s.image.shutter, s.image.synchro, s.image.pedestal, s.image.detail,
                              s.image.preset_speed) == (0xB, 0x20, -3, 2, 30))
        assert s.follower is None or not s.follower.enabled  # картинка не двигает камеру
    finally:
        s.stop()


def test_emergency_stop_sent_even_if_follower_raises():
    """Сбой подготовительного шага (слежение) не отменяет сам стоп."""
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        s.set_pan_tilt_velocity(1, 0)
        assert _wait(lambda: any(c.startswith("#PTS") and c != "#PTS5050" for c in s.sim.received))

        class Boom:
            enabled = True
            note_manual = boom
            disable = boom

        s.follower = Boom()
        s.emergency_stop()
        assert _wait(lambda: [c for c in s.sim.received if c.startswith("#PTS")][-1] == "#PTS5050")
    finally:
        s.follower = None
        s.stop()
