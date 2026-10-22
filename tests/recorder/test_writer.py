import json
import time
import zipfile

import numpy as np

from ptztracker.recorder import NullRecorder, Recorder
from ptztracker.recorder.events import KINDS, redact_meta, track_to_json
from ptztracker.recorder.reader import Episode
from ptztracker.recorder.writer import BlackBox
from ptztracker.video.frames import Frame
from ptztracker.vision.tracker import Track

PASSWORD = "Sim-Secret-42"


def _frame(seq, t, w=64, h=36):
    img = np.full((h, w, 3), seq % 255, np.uint8)
    return Frame(image=img, t_capture=t, seq=seq)


def test_recorders_satisfy_protocol(tmp_path):
    null = NullRecorder()
    assert isinstance(null, Recorder) and not null.active
    null.frame(_frame(1, 0.0))
    null.event("note", text="x")
    null.close()
    bb = BlackBox(tmp_path, {})
    assert isinstance(bb, Recorder) and bb.active
    bb.close()


def test_snapshot_contains_no_password(tmp_path):
    meta = {
        "camera": {"host": "10.0.0.5", "username": "admin", "password": PASSWORD},
        "url": f"rtsp://admin:{PASSWORD}@10.0.0.5/MediaInput/h264",
        "urls": [f"http://admin:{PASSWORD}@10.0.0.5/cgi-bin/mjpeg"],
    }
    bb = BlackBox(tmp_path, meta)
    bb.frame(_frame(1, time.monotonic()))
    bb.event("note", url=f"rtsp://admin:{PASSWORD}@10.0.0.5/x", password=PASSWORD)
    bb.flush()
    path = bb.save_snapshot("пароль").result(timeout=10)
    bb.close()
    raw = path.read_bytes()
    assert PASSWORD.encode() not in raw
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            assert PASSWORD.encode() not in z.read(name), name
    ep = Episode.open(path)
    assert ep.meta["camera"]["host"] == "10.0.0.5"
    assert "password" not in ep.meta["camera"]
    assert ep.meta["url"] == "rtsp://admin:***@10.0.0.5/MediaInput/h264"


def test_event_never_raises(tmp_path):
    bb = BlackBox(tmp_path, {})
    bb.event("control", bad=object())
    bb.event("control", value=np.float32(1.5), arr=np.arange(3))
    bb.flush()
    path = bb.save_snapshot("ошибка").result(timeout=10)
    bb.close()
    events = list(Episode.open(path).events())
    assert events[0]["kind"] == "note" and "control" in events[0]["error"]
    assert events[1]["kind"] == "control" and events[1]["value"] == 1.5 and events[1]["arr"] == [0, 1, 2]


def test_snapshot_does_not_block(tmp_path):
    bb = BlackBox(tmp_path, {}, frame_fps=1000, start_thread=False)
    img = np.zeros((360, 640, 3), np.uint8)  # сжимается хорошо: быстрый набор не тратит память на шум
    img[100:200, 200:300] = (0, 180, 255)
    t0 = time.monotonic()
    for i in range(1500):
        bb.frame(Frame(image=img, t_capture=t0 + i * 0.01, seq=i))
        if i % 40 == 39:  # очередь кадров маленькая: разбираем по ходу
            bb.flush()
    bb.flush()
    assert bb.buffered_frames() == 1500
    start = time.perf_counter()
    fut = bb.save_snapshot("скорость")
    elapsed = time.perf_counter() - start
    fut.result(timeout=60)
    bb.close()
    assert elapsed < 0.020


def test_snapshot_file_name(tmp_path):
    bb = BlackBox(tmp_path, {})
    p1 = bb.save_snapshot("F9 / ручное").result(timeout=10)
    p2 = bb.save_snapshot("F9 / ручное").result(timeout=10)
    bb.close()
    assert p1.parent == tmp_path and p1 != p2
    assert p1.name.startswith("ПТЗ_") and p1.name.endswith("_F9-ручное.zip")
    assert time.strftime("%Y-%m-%d") in p1.name


def test_close_does_not_hang(tmp_path):
    bb = BlackBox(tmp_path, {})
    start = time.perf_counter()
    bb.close()
    bb.close()
    assert time.perf_counter() - start < 1.0
    bb.event("note", text="после закрытия")  # не падает


def test_to_disk_writes_minute_chunks(tmp_path):
    bb = BlackBox(tmp_path, {"x": 1}, to_disk=True, frame_fps=1)
    for i in range(150):  # 2,5 минуты по 1 с
        bb.frame(_frame(i, 1000.0 + i))
        bb.event("note", t=1000.0 + i, i=i)
    bb.close()
    sessions = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(sessions) == 1
    chunks = sorted(sessions[0].glob("*.zip"))
    assert len(chunks) == 3
    seen = []
    for c in chunks:
        ep = Episode.open(c)
        assert ep.meta["x"] == 1
        seen += [e["i"] for e in ep.events("note")]
    assert seen == list(range(150))


def test_track_to_json_and_redact_meta():
    kp = np.zeros((17, 3), np.float32)
    kp[0] = (100.1234, 50.5678, 0.91234)
    tr = Track(id=3, box=(1.04, 2.0, 3.0, 4.0), score=np.float32(0.876), keypoints=kp, last_seen=5.0)
    j = track_to_json(tr)
    assert j["id"] == 3 and j["box"] == [1.0, 2.0, 3.0, 4.0] and j["score"] == 0.88
    assert len(j["keypoints"]) == 17 and j["keypoints"][0] == [100.1, 50.6, 0.91]
    assert all(type(v) is float for row in j["keypoints"] for v in row)
    assert redact_meta({"a": {"password": "x", "u": "rtsp://u:p@h/"}}) == {"a": {"u": "rtsp://u:***@h/"}}
    secrets = {"cam_pwd": 1, "Passwd": 2, "api_token": 3, "client_secret": 4, "host": "h"}
    assert redact_meta(secrets) == {"host": "h"}
    assert redact_meta(["rtsp://admin:p@ss@10.0.0.5/x"]) == ["rtsp://admin:***@10.0.0.5/x"]
    assert "analysis" in KINDS and "note" in KINDS


def test_bad_frame_does_not_lose_events(tmp_path):
    bb = BlackBox(tmp_path, {}, frame_fps=1000, start_thread=False)
    bb.event("note", i=0)
    bb.frame(_frame(1, 10.0))
    bb.frame(Frame(image=np.zeros((0, 0, 3), np.uint8), t_capture=10.1, seq=2))  # пустая картинка
    bb.frame(_frame(3, 10.2))
    bb.event("note", i=1)
    bb.flush()  # не бросает
    path = bb.save_snapshot("битый кадр").result(timeout=10)
    bb.close()
    ep = Episode.open(path)
    assert [e["i"] for e in ep.events("note")] == [0, 1]
    assert [s for _, s, _ in ep.frames()] == [1, 3]


def test_large_frames_downscaled_to_640x360(tmp_path):
    bb = BlackBox(tmp_path, {}, frame_fps=1000, start_thread=False)
    bb.frame(_frame(1, 10.0, w=1920, h=1080))
    bb.frame(_frame(2, 10.1, w=1000, h=1000))
    bb.frame(_frame(3, 10.2, w=64, h=36))
    bb.flush()
    path = bb.save_snapshot("размер").result(timeout=10)
    bb.close()
    ep = Episode.open(path)
    assert [im.shape[:2] for _, _, im in ep.frames()] == [(360, 640), (360, 360), (36, 64)]
    with zipfile.ZipFile(path) as z:
        index = [json.loads(line) for line in z.read("frames.jsonl").decode().splitlines()]
    assert [(f["w"], f["h"]) for f in index] == [(640, 360), (360, 360), (64, 36)]


def test_recent_events_since_and_limit_without_frames(tmp_path):
    bb = BlackBox(tmp_path, {}, start_thread=False)
    for i in range(10):
        bb.event("note", t=100.0 + i, n=i)
    bb.frame(_frame(1, 105.0))
    bb.flush()
    ev = bb.recent_events(since=104.0)
    assert [e["n"] for e in ev] == [5, 6, 7, 8, 9]  # строго новее since, по порядку
    assert all(e["kind"] == "note" for e in ev)
    assert [e["n"] for e in bb.recent_events(since=0.0, limit=3)] == [7, 8, 9]  # самые новые
    assert bb.recent_events(since=200.0) == []
    assert NullRecorder().recent_events(0.0) == []
    bb.close()


def test_frame_queue_is_small_and_events_still_queue(tmp_path):
    """Фоновый поток встал: в очереди не больше 50 полных кадров, лишние выбрасываются, события — нет."""
    bb = BlackBox(tmp_path, {}, frame_fps=1000, start_thread=False)
    for i in range(80):
        bb.frame(Frame(image=np.zeros((720, 1280, 3), np.uint8), t_capture=10.0 + i * 0.01, seq=i))
        bb.event("cmd", t=10.0 + i * 0.01, cmd="#O")
    assert bb.queued_frames() == 50 and bb.dropped_frames == 30
    bb.flush()
    assert len(list(bb.recent_events(0.0, 1000))) == 80
