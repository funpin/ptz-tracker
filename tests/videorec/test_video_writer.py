"""Файлы записи: безопасные имена, фрагментированный MP4, деление на части."""

import subprocess
import sys
from datetime import datetime

import av
import numpy as np
from video_helpers import count_frames

from ptztracker.videorec.writer import CopyWriter, EncodeWriter, file_stem, next_path

SRC = str(__import__("pathlib").Path(__file__).parents[2] / "src")


def copy_all(src, w):
    with av.open(str(src)) as c:
        s = c.streams.video[0]
        w_ = w(s)
        for p in c.demux(s):
            if p.dts is not None:
                w_.write_packet(p)
        w_.close()
    return w_


def test_stem_is_safe_for_windows():
    s = file_stem('Сцена: 1/2 "A"?.', datetime(2026, 11, 8, 20, 35))
    assert not set('<>:"/\\|?*') & set(s) and s.endswith("_2026-11-08_20-35")
    assert not s.split("_2026")[0].endswith((".", " "))


def test_stem_of_nameless_camera_is_not_empty():
    assert file_stem("///", datetime(2026, 11, 8, 20, 35)).split("_2026")[0].strip("_ ") != ""


def test_next_path_suffixes(tmp_path):
    (tmp_path / "a.mp4").touch()
    assert next_path(tmp_path, "a").name == "a_02.mp4"
    (tmp_path / "a_02.mp4").touch()
    assert next_path(tmp_path, "a").name == "a_03.mp4"


def test_two_writers_with_one_name_do_not_overwrite(h264_file, tmp_path):
    a = copy_all(h264_file, lambda s: CopyWriter(tmp_path, "cam", s))
    b = copy_all(h264_file, lambda s: CopyWriter(tmp_path, "cam", s))
    assert a.paths != b.paths and all(p.exists() for p in a.paths + b.paths)


def test_copy_is_readable_in_cyrillic_folder(h264_file, tmp_path):
    w = copy_all(h264_file, lambda s: CopyWriter(tmp_path / "Запись камер", "Сцена", s))
    assert w.paths[0].parent.name == "Запись камер"
    assert count_frames(w.paths[0]) == count_frames(h264_file) and w.bytes_written > 0


def test_copy_splits_on_keyframe_without_loss(h264_file, tmp_path):
    w = copy_all(h264_file, lambda s: CopyWriter(tmp_path, "c", s, split_bytes=300_000))
    assert len(w.paths) >= 2
    assert [p.name for p in w.paths[:2]] == ["c.mp4", "c_02.mp4"]
    assert sum(count_frames(p) for p in w.paths) == count_frames(h264_file)
    for p in w.paths:
        with av.open(str(p)) as c:
            assert next(c.demux(video=0)).is_keyframe


def test_each_part_starts_at_zero(h264_file, tmp_path):
    w = copy_all(h264_file, lambda s: CopyWriter(tmp_path, "c", s, split_bytes=300_000))
    with av.open(str(w.paths[1])) as c:
        assert float(c.streams.video[0].start_time or 0) * c.streams.video[0].time_base < 0.2


CRASH = """
import os, sys, av
sys.path.insert(0, sys.argv[3])
from ptztracker.videorec.writer import CopyWriter
c = av.open(sys.argv[1]); s = c.streams.video[0]
w = CopyWriter(__import__("pathlib").Path(sys.argv[2]), "crash", s)
for i, p in enumerate(c.demux(s)):
    if p.dts is None: continue
    w.write_packet(p)
    if i == 60: os._exit(9)
"""


def test_file_survives_kill(h264_file, tmp_path):
    r = subprocess.run([sys.executable, "-c", CRASH, str(h264_file), str(tmp_path), SRC], capture_output=True)
    assert r.returncode == 9, r.stderr[-300:]
    assert count_frames(tmp_path / "crash.mp4") > 30


def frame():
    return np.random.default_rng(0).integers(0, 255, (240, 320, 3), np.uint8)


def test_encode_writer_frames_and_duration(tmp_path):
    w = EncodeWriter(tmp_path, "e", 320, 240)
    img = frame()
    for i in range(50):
        w.write_frame(img, 100.0 + i * 0.04)
    w.close()
    assert count_frames(w.paths[0]) == 50
    with av.open(str(w.paths[0])) as c:
        assert 1.6 < float(c.duration or 0) / av.time_base < 2.4


def test_encode_writer_splits(tmp_path):
    w = EncodeWriter(tmp_path, "e", 320, 240, split_bytes=100_000)
    rng = np.random.default_rng(0)
    for i in range(100):
        w.write_frame(rng.integers(0, 255, (240, 320, 3), np.uint8), i * 0.04)
    w.close()
    assert len(w.paths) >= 2 and sum(count_frames(p) for p in w.paths) == 100


def test_no_file_until_first_data(tmp_path, h264_file):
    EncodeWriter(tmp_path / "e", "e", 320, 240).close()
    with av.open(str(h264_file)) as c:
        CopyWriter(tmp_path / "c", "c", c.streams.video[0]).close()
    assert list(tmp_path.iterdir()) == []


def test_encode_writer_accepts_frames_faster_than_fps(tmp_path):
    w = EncodeWriter(tmp_path, "e", 320, 240)
    img = frame()
    for i in range(30):
        w.write_frame(img, 5.0 + i * 0.006)
    w.close()
    assert count_frames(w.paths[0]) == 30


def test_next_path_reserves_the_name_between_threads(tmp_path):
    import threading

    got, start = [], threading.Barrier(8)

    def pick():
        start.wait()
        got.append(next_path(tmp_path, "cam"))

    threads = [threading.Thread(target=pick) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(got)) == 8


def _peak_second_bits(path):
    with av.open(str(path)) as c:
        s = c.streams.video[0]
        per = {}
        for p in c.demux(s):
            if p.dts is not None and p.size:
                k = int(p.dts * s.time_base)
                per[k] = per.get(k, 0) + p.size * 8
    return max(per.values())


def _quiet_then_burst(w):
    rng = np.random.default_rng(3)
    still = np.full((240, 320, 3), 90, np.uint8)
    for i in range(250):  # 10 с: 6 с покоя, 4 с шума
        img = rng.integers(0, 255, (240, 320, 3), np.uint8) if i >= 150 else still
        w.write_frame(img, i * 0.04)
    w.close()


def test_max_rate_limits_bursts(tmp_path):
    free = EncodeWriter(tmp_path / "a", "e", 320, 240, bit_rate=150_000)
    capped = EncodeWriter(tmp_path / "b", "e", 320, 240, bit_rate=150_000, max_rate=300_000)
    _quiet_then_burst(free)
    _quiet_then_burst(capped)
    assert _peak_second_bits(capped.paths[0]) < 300_000 * 1.6
    assert _peak_second_bits(free.paths[0]) > _peak_second_bits(capped.paths[0])

