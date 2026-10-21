"""Крючки записи в планировщике, анализе, слежении и сессии."""

import json
import time

from ptztracker.camera.protocol import CameraError
from ptztracker.camera.scheduler import CommandScheduler, Priority
from ptztracker.camera.session import CameraSession
from ptztracker.config import AppConfig, CameraConfig
from ptztracker.recorder.reader import Episode


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def wait_until(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def start_sim(tmp_path, **app):
    cfg = CameraConfig(id="s", name="Сим", ip="sim", kind="sim")
    s = CameraSession(cfg, app_cfg=AppConfig(record_dir=str(tmp_path), **app))
    s.start()
    assert wait_until(lambda: s.analyzer.latest() is not None and len(s.analyzer.latest().tracks) > 0, 4)
    return s


def select_person(s):
    tr = s.analyzer.latest().tracks[0]
    assert s.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2) is not None
    assert wait_until(lambda: s.analyzer.latest().target_state == "tracking", 2)


def test_scheduler_reports_sent_commands():
    clock = FakeClock()
    fail = []

    def send(cmd):
        if fail:
            raise fail.pop()
        return cmd.lstrip("#")

    sent = []
    s = CommandScheduler(send, min_interval=0.130, clock=clock, on_sent=sent.append)
    s.submit("pt", "#PTS6050", Priority.TRACKING)
    clock.t += 0.05
    assert s.pump() == "#PTS6050"
    assert sent == [
        {"cmd": "#PTS6050", "key": "pt", "priority": "TRACKING", "queued_s": sent[0]["queued_s"],
         "reply": "PTS6050", "ok": True}
    ]
    assert abs(sent[0]["queued_s"] - 0.05) < 1e-9

    fail.append(CameraError("er3", "er3"))
    s.submit("zoom", "#Z60", Priority.TRACKING_ZOOM)
    clock.t += 0.2
    assert s.pump() == "#Z60"
    assert sent[1]["ok"] is False and sent[1]["key"] == "zoom" and "er3" in sent[1]["reply"]


def test_scheduler_on_sent_exception_does_not_break_sending():
    clock = FakeClock()
    calls = []

    def send(cmd):
        calls.append(cmd)
        return cmd.lstrip("#")

    def boom(info):
        raise RuntimeError("запись сломалась")

    s = CommandScheduler(send, min_interval=0.130, clock=clock, on_sent=boom)
    s.submit("pt", "#PTS6050", Priority.TRACKING)
    assert s.pump() == "#PTS6050"
    clock.t += 0.2
    s.submit("zoom", "#Z60", Priority.TRACKING)
    assert s.pump() == "#Z60"
    assert calls == ["#PTS6050", "#Z60"]
    s.submit("pt", "#PTS6050", Priority.TRACKING)  # уже отправлено — повтора нет
    assert not s.pending()


def test_sim_session_records_all_kinds(tmp_path):
    s = start_sim(tmp_path)
    try:
        assert s.recorder.active
        select_person(s)
        assert s.follower.enable()
        time.sleep(3.0)
        path = s.save_moment().result(timeout=10)
    finally:
        s.stop()
    assert path.parent == tmp_path and path.name.endswith("_F9.zip")
    ep = Episode.open(path)
    kinds = {e["kind"] for e in ep.events()}
    assert {"analysis", "control", "cmd", "position", "follow", "target"} <= kinds
    assert sum(1 for _ in ep.frames()) > 5

    a = next(e for e in ep.events("analysis") if e["target_id"] is not None)
    assert a["tracks"] and {"seq", "t_capture", "frame_size", "target_state", "infer_ms"} <= a.keys()
    c = next(e for e in ep.events("control") if e["measure"] is not None)
    assert {"nose", "u", "confident"} <= c["measure"].keys()
    assert {"pan", "tilt", "zoom", "state", "error", "size_error"} <= c["out"].keys()
    assert len(c["codes"]) == 3 and c["composition"] == "waist_center" and "zoom_pos" in c
    assert any(e["cmd"].startswith("#PTS") and e["ok"] for e in ep.events("cmd"))
    p = next(ep.events("position"))
    assert isinstance(p["pan"], int) and isinstance(p["tilt"], int) and isinstance(p["zoom"], int)
    assert any(e.get("on") is True for e in ep.events("follow"))
    t = next(ep.events("target"))
    assert t["how"] == "click" and t["id"] is not None and "x" in t and "y" in t
    assert ep.meta["stream_kind"] == "sim" and ep.meta["camera"]["kind"] == "sim"
    assert "pan" in ep.meta["calibration"] and ep.meta["version"]


def test_session_meta_is_plain_json(tmp_path):
    cfg = CameraConfig(id="s", name="Сим", ip="sim", kind="sim")
    s = CameraSession(cfg, app_cfg=AppConfig(record_dir=str(tmp_path)))
    meta = s.recording_meta()
    assert json.loads(json.dumps(meta)) == meta


def test_recording_disabled_gives_null_recorder(tmp_path):
    s = start_sim(tmp_path, record_enabled=False)
    try:
        assert not s.recorder.active
        fut = s.save_moment()
        assert fut.exception(timeout=1) is not None
    finally:
        s.stop()


class BoomRecorder:
    active = True

    def frame(self, fr):
        raise RuntimeError("кадр")

    def event(self, kind, **data):
        raise RuntimeError("событие")

    def save_snapshot(self, reason):
        raise RuntimeError("снимок")

    def close(self):
        pass


def test_recorder_failure_keeps_following(tmp_path):
    s = start_sim(tmp_path)
    try:
        boom = BoomRecorder()
        s.analyzer.recorder = boom
        s.follower.recorder = boom
        s.recorder = boom  # on_sent сессии пишет в s.recorder
        select_person(s)
        assert s.follower.enable()
        n0 = len(s.sim.received)
        time.sleep(1.5)
        assert s.follower.enabled, s.follower.last_reason
        assert s.analyzer.latest().target_state == "tracking"
        new = list(s.sim.received)[n0:]
        assert any(c.startswith("#PTS") for c in new)
        assert any(c.startswith("#APC") for c in new)  # опрос положения тоже идёт
    finally:
        s.stop()


def test_idle_position_poll_is_rare_and_yields_to_manual(tmp_path):
    """Слежение выкл., запись идёт: #APC не чаще раза в 2 с и не в первые 2 с после ручной команды."""
    s = start_sim(tmp_path)
    try:
        assert not s.follower.enabled
        n0 = len(s.sim.received)
        time.sleep(5.0)
        apc = sum(1 for c in list(s.sim.received)[n0:] if c == "#APC")
        assert 1 <= apc <= 3, apc  # при слежении было бы ~10
        end = time.monotonic() + 4.0
        n1 = len(s.sim.received)
        while time.monotonic() < end:  # оператор держит джойстик
            s.set_pan_tilt_velocity(0.2 if int(time.monotonic() * 4) % 2 else -0.2, 0.0)
            time.sleep(0.25)
        s.set_pan_tilt_velocity(0.0, 0.0)
        got = list(s.sim.received)[n1:]
        assert not any(c in ("#APC", "#GZ") for c in got), got
    finally:
        s.stop()
