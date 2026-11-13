import numpy as np

from ptztracker.vision.analyzer import AnalysisResult, Analyzer
from ptztracker.vision.tracker import Track


class _Src:
    def latest(self):
        return None

    def stats(self):
        return None

    def start(self): ...

    def stop(self, timeout=2.0): ...


def _tr(i, cx, seen=1.0):
    return Track(id=i, box=(cx - 20, 100, cx + 20, 300), score=0.9, keypoints=np.zeros((17, 3), np.float32), last_seen=seen)


def _setup(centers, target=None, unseen=()):
    a = Analyzer(_Src(), lambda: None)
    tracks = [_tr(i, cx, 0.5 if i in unseen else 1.0) for i, cx in enumerate(centers)]
    for t in tracks:
        a.tracker._tracks[t.id] = t
    a.tracker._t = 1.0
    if target is not None:
        a.tracker.select(target)
    a._latest = AnalysisResult(
        seq=1, t_capture=1.0, frame_size=(1000, 500), tracks=tuple(tracks), target_id=target,
        target_state="tracking" if target is not None else "none", infer_ms=1, fps=10, detector="x", device="cpu",
    )
    return a


def test_neighbor_right_of_target():
    a = _setup([200, 500, 800], target=0)
    assert a.select_neighbor(+1) == 1
    assert a.latest().target_id == 1
    assert a.select_neighbor(-1) == 0


def test_neighbor_none_on_edge_keeps_target():
    a = _setup([200, 500, 800], target=2)
    assert a.select_neighbor(+1) is None
    assert a.tracker.target_id == 2


def test_neighbor_without_target_from_center():
    a = _setup([200, 600, 800])
    assert a.select_neighbor(+1) == 1
    a = _setup([200, 400, 800])
    assert a.select_neighbor(-1) == 1


def test_neighbor_ignores_unseen_tracks():
    a = _setup([200, 500, 800], target=0, unseen=(1,))
    assert a.select_neighbor(+1) == 2


def _off_stage(a, *ids):
    for t in a.latest().tracks:
        if t.id in ids:
            t.on_stage = False


def test_neighbor_skips_people_outside_stage_area():
    a = _setup([200, 500, 800], target=0)
    _off_stage(a, 1)
    assert a.select_neighbor(+1) == 2


def test_neighbor_as_before_when_nobody_else_is_on_stage():
    a = _setup([200, 500, 800], target=0)
    _off_stage(a, 1, 2)
    assert a.select_neighbor(+1) == 1


def test_central_person_is_chosen_among_people_on_stage():
    a = _setup([200, 520, 760])
    assert a.central_track().id == 1
    _off_stage(a, 1)
    assert a.central_track().id == 2  # ближе всех к центру (500) из тех, кто на сцене
    _off_stage(a, 0, 2)
    assert a.central_track().id == 1  # на сцене никого — как раньше


def test_click_outside_area_selects():
    """Клик оператора главнее области: человек вне её выбирается как обычно."""
    a = _setup([200, 500, 800])
    _off_stage(a, 1)
    assert a.select_at(500, 200) == 1 and a.latest().target_id == 1
