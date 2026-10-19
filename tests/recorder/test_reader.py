import time

import numpy as np

from ptztracker.recorder.reader import Episode
from ptztracker.recorder.writer import BlackBox
from ptztracker.video.frames import Frame


def test_snapshot_roundtrip(tmp_path):
    bb = BlackBox(tmp_path, {"version": "test"}, frame_fps=5)
    t0 = time.monotonic()
    img = np.zeros((360, 640, 3), np.uint8)
    img[100:200, 300:400] = (0, 200, 50)
    for i in range(10):
        bb.frame(Frame(image=img, t_capture=t0 + i * 0.2, seq=100 + i))
        bb.event("analysis", t=t0 + i * 0.2, seq=100 + i)
        bb.event("cmd", t=t0 + i * 0.2 + 0.01, text="#PTS5050")
    bb.flush()
    path = bb.save_snapshot("повтор").result(timeout=10)
    bb.close()

    ep = Episode.open(path)
    assert ep.meta["version"] == "test"
    assert {"monotonic", "wallclock"} <= set(ep.meta["clock"])
    analysis = list(ep.events("analysis"))
    assert [e["seq"] for e in analysis] == list(range(100, 110))
    assert [e["t"] for e in analysis] == [t0 + i * 0.2 for i in range(10)]
    assert len(list(ep.events("cmd"))) == 10
    assert len(list(ep.events())) == 20
    frames = list(ep.frames())
    assert [s for _, s, _ in frames] == list(range(100, 110))
    assert [t for t, _, _ in frames] == [t0 + i * 0.2 for i in range(10)]
    assert all(im.shape == (360, 640, 3) for _, _, im in frames)
    assert abs(int(frames[0][2][150, 350, 1]) - 200) < 10
    assert abs(ep.duration - (9 * 0.2 + 0.01)) < 1e-6
