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
