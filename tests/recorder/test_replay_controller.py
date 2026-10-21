"""Повтор записанного эпизода через регулятор: коды, сводка, графики, командная строка."""

import dataclasses
import math
import time

import cv2
import pytest

from ptztracker.__main__ import main
from ptztracker.control.camera_model import default_calibration
from ptztracker.control.composition import COMPOSITIONS, BodyMeasure
from ptztracker.control.effort import EffortProfile
from ptztracker.control.follower import output_codes
from ptztracker.control.framing import FramingController, FramingInput
from ptztracker.recorder.events import event_line
from ptztracker.recorder.reader import Episode
from ptztracker.recorder.replay import replay_controller
from ptztracker.recorder.writer import write_episode

T0 = 1000.0


def _write(path, events, meta=None):
    meta = meta or {"stream_kind": "sim", "calibration": default_calibration().to_json()}
    items = [(e["t"], event_line(e["t"], e["kind"], {k: v for k, v in e.items() if k not in ("t", "kind")}))
             for e in events]
    return Episode.open(write_episode(path, meta, items, []))


def _following_events(seconds=8.0, effort=0.5, follow_on_late=0.005, latency=None):
    """Слежение как в Follower: такты 25 Гц, новый замер каждый второй такт, человек ходит туда-обратно.

    Событие follow on пишется на follow_on_late позже первого такта — как в жизни, когда поток слежения
    успевает сделать такт раньше, чем enable() записал событие.
    """
    calib, comp, eff = default_calibration(), COMPOSITIONS["waist_center"], EffortProfile.at(effort)
    ctrl = FramingController(comp, eff, calib, calib.video_latency["sim"] if latency is None else latency)
    codes = (50, 50, 50)
    ev = [{"t": T0 + follow_on_late, "kind": "follow", "on": True, "reason": None, "target_id": 3}]
    if latency is not None:
        ev[0]["video_latency"] = latency
    t_cap, m = None, None
    for i in range(int(seconds * 25)):
        now = T0 + i * 0.04
        if i % 2 == 0:
            t_cap = now - 0.01
            x = 0.5 + 0.3 * math.sin(now - T0)
            m = {"nose": [x, 0.25 + 0.02 * math.sin(3 * (now - T0))], "u": 0.17, "confident": True}
            ev.append({"t": t_cap, "kind": "analysis", "target_id": 3, "target_state": "tracking", "tracks": []})
        meas = BodyMeasure(tuple(m["nose"]), m["u"], m["confident"])
        out = ctrl.step(FramingInput(now=now, t_capture=t_cap, measure=meas, zoom_pos=0x555, aspect=16 / 9))
        codes = output_codes(calib, out, 0x555, codes)
        ev.append({"t": now, "kind": "control", "t_capture": t_cap, "measure": m, "zoom_pos": 0x555,
                   "aspect": 16 / 9,
                   "out": {"pan": out.pan, "tilt": out.tilt, "zoom": out.zoom, "state": out.state,
                           "error": list(out.error), "size_error": out.size_error},
                   "codes": list(codes), "composition": comp.key, "effort": dataclasses.asdict(eff)})
    ev.append({"t": T0 + seconds, "kind": "follow", "on": False, "reason": "ручное управление"})
    ev.append({"t": T0 + 1.0, "kind": "position", "pan": 0x8000, "tilt": 0x8000, "zoom": 0x555})
    ev.append({"t": T0 + 2.0, "kind": "position", "pan": 0x8100, "tilt": 0x8000, "zoom": 0x555})
    return ev


def test_replay_reproduces_synthetic_codes(tmp_path):
    ep = _write(tmp_path / "e.zip", _following_events())
    res = replay_controller(ep)
    assert len(res.t) == 200
    assert res.codes_new == res.codes_rec
    assert any(c[0] != 50 for c in res.codes_new)  # камера реально двигалась
    assert not res.partial_start  # follow on позже первого такта — тот же запуск
    assert len(res.segments) == 1 and abs(res.segments[0][1] - res.segments[0][0] - 8.0) < 0.01


def test_replay_flags_episode_starting_mid_follow(tmp_path):
    ev = [e for e in _following_events() if not (e["kind"] == "follow" and e.get("on"))]
    res = replay_controller(_write(tmp_path / "e.zip", ev))
    assert res.partial_start
    assert "посреди слежения" in res.summary().text()


def test_replay_resets_on_each_enable(tmp_path):
    first = _following_events(seconds=3.0)
    second = [dict(e, t=e["t"] + 10.0) for e in first if e["kind"] != "position"]
    for e in second:
        if e["kind"] == "control":
            e["t_capture"] += 10.0
    res = replay_controller(_write(tmp_path / "e.zip", first + second))
    assert len(res.segments) == 2
    assert res.codes_new == res.codes_rec
    assert res.summary().target_switches == 0


@pytest.mark.slow
def test_replay_reproduces_recorded_codes(tmp_path):
    """Review Focus 3: 30 с слежения на симуляторе → повтор даёт те же коды (±1)."""
    from ptztracker.camera.session import CameraSession
    from ptztracker.config import AppConfig, CameraConfig

    cfg = CameraConfig(id="s", name="Сим", ip="sim", kind="sim")
    s = CameraSession(cfg, app_cfg=AppConfig(record_dir=str(tmp_path)))
    s.start()
    try:
        end = time.monotonic() + 4
        while time.monotonic() < end and not (s.analyzer.latest() and s.analyzer.latest().tracks):
            time.sleep(0.02)
        tr = s.analyzer.latest().tracks[0]
        assert s.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2) is not None
        assert s.follower.enable()
        time.sleep(30.0)
        path = s.save_moment("повтор").result(timeout=10)
    finally:
        s.stop()
    res = replay_controller(Episode.open(path))
    assert len(res.t) > 30 * 20
    bad = [(t, a, b) for t, a, b in zip(res.t, res.codes_rec, res.codes_new)
           if max(abs(x - y) for x, y in zip(a, b)) > 1]
    assert not bad, bad[:5]
    assert any(c[0] != 50 or c[1] != 50 for c in res.codes_new)


def test_replay_uses_latency_recorded_at_enable(tmp_path):
    """meta.stream_kind даёт 0,15 с, а Follower.enable() выставил 0,6 с — повтор берёт записанную."""
    ev = _following_events(latency=0.6)
    res = replay_controller(_write(tmp_path / "e.zip", ev))
    assert res.codes_new == res.codes_rec
    for e in ev:
        e.pop("video_latency", None)  # старая запись: задержки в follow on нет
    old = replay_controller(_write(tmp_path / "old.zip", ev))
    assert old.codes_new != old.codes_rec


def test_replay_flags_unreadable_calibration(tmp_path):
    ev = _following_events(seconds=2.0)
    ok = replay_controller(_write(tmp_path / "ok.zip", ev))
    assert ok.calibration_ok and "Калибровка" not in ok.summary().text()
    bad = replay_controller(_write(tmp_path / "bad.zip", ev, meta={"stream_kind": "sim", "calibration": {"pan": 5}}))
    assert not bad.calibration_ok
    assert "Калибровка в записи не читается" in bad.summary().text()


def _with_damage(tmp_path, ev):
    good = [(e["t"], event_line(e["t"], e["kind"], {k: v for k, v in e.items() if k not in ("t", "kind")}))
            for e in ev]
    ctl = next(i for i, (_, line) in enumerate(good) if '"control"' in line)
    damaged = [
        (T0 + 1.0, '{"kind": "analysis", "target_id": 3}'),  # нет t
        (T0 + 1.0, '{"t": "вчера", "kind": "analysis"}'),  # t не число
        (T0 + 1.5, f'{{"t": {T0 + 1.5}, "kind": "position", "tilt": 1}}'),  # нет pan
        (T0 + 2.0, f'{{"t": {T0 + 2.001}, "kind": "control", "measure": {{"nose": "x"}}}}'),  # кривой замер
        (T0 + 2.0, '{"t": 1002.0, "kind": "contr'),  # оборванная строка
    ]
    meta = {"stream_kind": "sim", "calibration": default_calibration().to_json()}
    return write_episode(tmp_path / "e.zip", meta, good[:ctl + 5] + damaged + good[ctl + 5:], [])


def test_replay_skips_damaged_events(tmp_path):
    ev = _following_events()
    res = replay_controller(Episode.open(_with_damage(tmp_path, ev)))
    assert res.skipped_events == 5
    assert res.codes_new == res.codes_rec  # кривой такт не сдвинул состояние регулятора
    assert len(res.t) == 200 and len(res.position) == 2
    assert "Пропущено 5 повреждённых событий" in res.summary().text()


def test_cli_replay_damaged_episode(tmp_path, capsys):
    path = _with_damage(tmp_path, _following_events())
    assert main(["--replay", str(path), "--out", str(tmp_path)]) == 0
    assert "Пропущено 5 повреждённых событий" in capsys.readouterr().out


def test_replay_with_other_effort_changes_codes(tmp_path):
    ep = _write(tmp_path / "e.zip", _following_events(effort=0.0))
    same = replay_controller(ep)
    sharp = replay_controller(ep, effort=EffortProfile.at(1.0))
    assert same.codes_new == same.codes_rec
    assert sharp.codes_new != sharp.codes_rec
    assert sharp.codes_rec == same.codes_rec
    assert max(abs(c[0] - 50) for c in sharp.codes_new) > max(abs(c[0] - 50) for c in same.codes_new)


def test_summary_counts_target_switches(tmp_path):
    ids = [3, 3, 7, 7, None, 3, 3]
    states = ["tracking", "tracking", "tracking", "lost", "none", "tracking", "tracking"]
    ev = [{"t": T0 + i * 0.1, "kind": "analysis", "target_id": tid, "target_state": st, "tracks": []}
          for i, (tid, st) in enumerate(zip(ids, states))]
    ev.reverse()  # события в zip идут не строго по времени
    res = replay_controller(_write(tmp_path / "e.zip", ev))
    s = res.summary()
    assert s.target_switches == 2
    assert s.target_losses == 1
    assert res.t == []
    assert "Смены цели: 2" in s.text()


def test_summary_of_following(tmp_path):
    res = replay_controller(_write(tmp_path / "e.zip", _following_events()))
    s = res.summary()
    assert s.pan_reversals_new == s.pan_reversals_rec >= 1  # человек развернулся — камера тоже
    assert s.max_accel_new > 0
    assert 0.0 <= s.out_of_soft_pct <= 100.0
    assert abs(s.following_s - 8.0) < 0.1
    assert "Развороты pan" in s.text()


def test_plot_written(tmp_path):
    from ptztracker.recorder.plots import render

    res = replay_controller(_write(tmp_path / "e.zip", _following_events()))
    out = render(res, tmp_path / "plot.png")
    img = cv2.imread(str(out))
    assert img.shape == (900, 1600, 3)
    assert img.std() > 10  # не пустая картинка


def test_plot_of_empty_episode(tmp_path):
    from ptztracker.recorder.plots import render

    res = replay_controller(_write(tmp_path / "e.zip", []))
    assert cv2.imread(str(render(res, tmp_path / "plot.png"))).shape == (900, 1600, 3)


def test_cli_replay(tmp_path, capsys):
    _write(tmp_path / "e.zip", _following_events())
    out = tmp_path / "out"
    assert main(["--replay", str(tmp_path / "e.zip"), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "Развороты pan" in text and "Графики:" in text
    assert len(list(out.glob("*.png"))) == 1


def test_cli_replay_analysis_mode_not_yet(tmp_path, capsys):
    _write(tmp_path / "e.zip", [])
    assert main(["--replay", str(tmp_path / "e.zip"), "--mode", "analysis"]) == 2
    assert "появится позже" in capsys.readouterr().err


def test_cli_replay_missing_file(tmp_path, capsys):
    assert main(["--replay", str(tmp_path / "нет.zip")]) == 2
    assert "не удалось открыть" in capsys.readouterr().err.lower()
