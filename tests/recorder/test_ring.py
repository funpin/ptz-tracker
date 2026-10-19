import numpy as np

from ptztracker.recorder.ring import RingBuffer
from ptztracker.recorder.writer import BlackBox
from ptztracker.video.frames import Frame


def test_ring_drops_older_than_buffer():
    ring = RingBuffer(seconds=10.0)
    for i in range(30):
        ring.add(float(i), i)
    items = ring.items()
    assert [t for t, _ in items] == [float(i) for i in range(19, 30)]
    assert len(ring) == 11


def test_frames_throttled_to_fps(tmp_path):
    bb = BlackBox(tmp_path, {}, frame_fps=5, start_thread=False)
    img = np.zeros((36, 64, 3), np.uint8)
    for i in range(200):  # 2 с при 100 к/с
        bb.frame(Frame(image=img, t_capture=100.0 + i * 0.01, seq=i))
    assert 9 <= bb.queued_frames() <= 11
    bb.close()


def test_queue_overflow_drops_frames_not_events(tmp_path):
    bb = BlackBox(tmp_path, {}, buffer_s=1e6, frame_fps=1000, start_thread=False)
    img = np.zeros((36, 64, 3), np.uint8)
    for i in range(5000):
        bb.frame(Frame(image=img, t_capture=100.0 + i * 0.01, seq=i))
    for i in range(100):
        bb.event("note", i=i)
    assert bb.dropped_frames == 3000
    bb.flush()
    path = bb.save_snapshot("переполнение").result(timeout=30)
    bb.close()

    from ptztracker.recorder.reader import Episode

    ep = Episode.open(path)
    assert [e["i"] for e in ep.events("note")] == list(range(100))
    assert sum(1 for _ in ep.frames()) == 2000
