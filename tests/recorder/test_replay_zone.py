from ptztracker.control.composition import COMPOSITIONS
from ptztracker.recorder.replay import _composition


def test_replay_takes_recorded_zone_point():
    base = COMPOSITIONS["waist_center"]
    assert _composition("waist_center", base, [0.4, 0.3]).anchor == (0.4, 0.3)
    assert _composition("waist_center", base, None) == base  # старые записи — без точки
    assert _composition("waist_center", base, ["x"]) == base


def test_replay_applies_mark_moved_by_operator(tmp_path):
    """Запись F9 2026-11-10: оператор на 20-й секунде передвинул метку (событие follow с anchor), а повтор считал
    по прежней — коды наклона расходились с записанными до 8. В тактах старых записей метки нет."""
    import dataclasses

    from ptztracker.control.camera_model import default_calibration
    from ptztracker.control.composition import BodyMeasure
    from ptztracker.control.effort import EffortProfile
    from ptztracker.control.follower import output_codes
    from ptztracker.control.framing import FramingController, FramingInput
    from ptztracker.recorder.events import event_line
    from ptztracker.recorder.reader import Episode
    from ptztracker.recorder.replay import replay_controller
    from ptztracker.recorder.writer import write_episode

    calib, eff = default_calibration(), EffortProfile.at(0.5)
    moved = dataclasses.replace(COMPOSITIONS["waist_center"], anchor=(0.5, 0.4))
    ctrl = FramingController(COMPOSITIONS["waist_center"], eff, calib, calib.video_latency["sim"])
    ev = [{"t": 1000.0, "kind": "follow", "on": True, "reason": None, "target_id": 1}]
    codes = (50, 50, 50)
    for i in range(150):
        now = 1000.0 + i * 0.04
        if i == 25:  # оператор двигает метку вниз
            ctrl.set_composition(moved)
            ev.append({"t": now - 0.001, "kind": "follow", "composition": "waist_center", "anchor": [0.5, 0.4],
                       "effort": dataclasses.asdict(eff)})
        out = ctrl.step(FramingInput(now=now, t_capture=now - 0.01, measure=BodyMeasure((0.5, 0.25), 0.19, True),
                                     zoom_pos=0x555))
        codes = output_codes(calib, out, 0x555, codes)
        ev.append({"t": now, "kind": "control", "t_capture": now - 0.01,
                   "measure": {"nose": [0.5, 0.25], "u": 0.19, "confident": True}, "zoom_pos": 0x555, "aspect": 16 / 9,
                   "out": {"pan": out.pan, "tilt": out.tilt, "zoom": out.zoom, "state": out.state,
                           "error": list(out.error), "size_error": out.size_error},
                   "codes": list(codes), "composition": "waist_center", "effort": dataclasses.asdict(eff)})
    assert any(c["codes"][1] != 50 for c in ev if c["kind"] == "control"), "метка сдвинута — наклон должен ехать"
    items = [(e["t"], event_line(e["t"], e["kind"], {k: v for k, v in e.items() if k not in ("t", "kind")})) for e in ev]
    meta = {"stream_kind": "sim", "calibration": calib.to_json()}
    res = replay_controller(Episode.open(write_episode(tmp_path / "e.zip", meta, items, [])))
    assert res.summary().max_code_diff == 0
