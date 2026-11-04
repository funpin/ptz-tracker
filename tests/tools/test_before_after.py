"""tools/before_after.py: только чистые помощники (числа по строкам прогона и кадр видео); сам прогон 2×60 с не запускается."""

import importlib.util
from pathlib import Path

import numpy as np

_SPEC = importlib.util.spec_from_file_location("before_after", Path(__file__).resolve().parents[2] / "tools" / "before_after.py")
ba = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ba)


def _row(t, err, walk=False, zoom=2.0, in_frame=True, edge=0.4):
    return {"t": t, "err_x": err, "walk": walk, "zoom": zoom, "in_frame": in_frame, "edge": edge}


def test_metrics_basic():
    rows = [_row(0.0, 0.1), _row(1.0, -0.3), _row(2.0, 0.2, in_frame=False, edge=-0.05), _row(3.0, 0.0)]
    m = ba.compute_metrics(rows, [1.5])
    assert m["mean_abs_err_x"] == 0.15
    assert m["max_abs_err_x"] == 0.3
    assert m["out_of_frame_pct"] == 25.0
    assert m["min_edge_dist"] == -0.05
    assert m["watchdog_trips"] == 1 and m["watchdog_trip_times_s"] == [1.5]
    assert "zoom_min_walk" not in m  # никто не шёл


def test_metrics_walk_zoom_and_steady_error():
    # две ходьбы; зум считается относительно начала каждой, первая секунда не входит в «установившуюся» ошибку
    rows = [_row(0.0, 0.0)]
    rows += [_row(1.0, 0.5, True, 4.0), _row(1.5, 0.5, True, 2.0), _row(2.5, 0.1, True, 2.0), _row(3.0, 0.1, True, 2.0)]
    rows += [_row(4.0, 0.0), _row(5.0, 0.2, True, 3.0), _row(6.5, 0.1, True, 3.9)]
    m = ba.compute_metrics(rows, [])
    assert (m["zoom_min_walk"], m["zoom_max_walk"]) == (2.0, 4.0)
    assert m["zoom_ratio_walk_min"] == 0.5 and m["zoom_ratio_walk_max"] == 1.3
    assert m["walk_steady_mean_abs_err_x"] == 0.1  # 0.5 при t-t0 < 1 с отброшены
    assert m["walk_mean_abs_err_x"] == round((0.5 + 0.5 + 0.1 + 0.1 + 0.2 + 0.1) / 6, 3)


def test_metrics_empty():
    assert ba.compute_metrics([], []) == {"frames": 0}


def test_walk_spans():
    rows = [_row(0, 0), _row(1, 0, True), _row(2, 0, True), _row(3, 0), _row(4, 0, True)]
    assert ba.walk_spans(rows) == [(1, 3), (4, 4)]


def test_compose_frame_shape_and_cursor():
    pw, ph = ba.PANEL
    strip = ba.make_strip({"before": [_row(0, 0.0), _row(10, 0.3)], "after": [_row(0, 0.0), _row(10, -0.1)]},
                          {"before": [5.0], "after": []}, 10.0, (2 * pw, ba.STRIP_H), [(2.0, 4.0)])
    assert strip.shape == (ba.STRIP_H, 2 * pw, 3)
    left = np.full((720, 1280, 3), 100, np.uint8)  # другого размера — подгоняется
    f0 = ba.compose_frame(left, None, strip, 0.0, 10.0)
    f1 = ba.compose_frame(left, None, strip, 10.0, 10.0)
    assert f0.shape == (ph + ba.STRIP_H, 2 * pw, 3)
    assert (f0[:ph, :pw] == 100).all() and not f0[:ph, pw:].any()  # слева кадр, справа чёрный
    bottom0, bottom1 = f0[ph:], f1[ph:]
    mid = ba.STRIP_H // 2
    assert bottom0[mid, 8].min() > 200 and bottom1[mid, 2 * pw - 8].min() > 200  # бегущая отметка времени
    assert bottom0[mid, 2 * pw - 8].min() < 100


def test_blend_label_clips_at_border():
    dst = np.zeros((20, 20, 3), np.uint8)
    lab = np.zeros((10, 10, 4), np.uint8)
    lab[..., :3], lab[..., 3] = 200, 255
    ba.blend_label(dst, lab, 15, 15)  # торчит за край — не падает
    assert (dst[15:, 15:] == 200).all() and not dst[:15].any()
