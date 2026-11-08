"""Помощники тестов записи: короткий H.264 и подсчёт кадров."""

import av
import numpy as np


def make_h264(path, fps=25, seconds=3, gop=5):
    out = av.open(str(path), "w")
    s = out.add_stream("libx264", rate=fps)
    s.width, s.height, s.pix_fmt = 320, 240, "yuv420p"
    s.options = {"g": str(gop), "preset": "ultrafast", "bf": "0"}
    rng = np.random.default_rng(1)
    for _ in range(fps * seconds):
        img = rng.integers(0, 255, (240, 320, 3), np.uint8)
        for p in s.encode(av.VideoFrame.from_ndarray(img, format="bgr24")):
            out.mux(p)
    for p in s.encode():
        out.mux(p)
    out.close()
    return path


def count_frames(path):
    with av.open(str(path)) as c:
        n = 0
        try:
            for _ in c.decode(video=0):
                n += 1
        except av.error.InvalidDataError:
            pass  # оборванный последний фрагмент после сбоя
    return n
