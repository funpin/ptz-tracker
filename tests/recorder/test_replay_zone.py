from ptztracker.control.composition import COMPOSITIONS
from ptztracker.recorder.replay import _composition


def test_replay_takes_recorded_zone_point():
    base = COMPOSITIONS["waist_center"]
    assert _composition("waist_center", base, [0.4, 0.3]).anchor == (0.4, 0.3)
    assert _composition("waist_center", base, None) == base  # старые записи — без точки
    assert _composition("waist_center", base, ["x"]) == base
