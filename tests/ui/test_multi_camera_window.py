"""Главное окно на сетке 2×2: несколько сессий, выбранная клетка, анализ по лимиту."""

import time

from PySide6.QtCore import QPoint, Qt

from ptztracker.config import CameraConfig, ConfigStore
from ptztracker.ui.main_window import MainWindow


def make(qtbot, tmp_path, cameras=None, max_analyzed=1):
    store = ConfigStore(tmp_path / "c.json")
    if cameras is not None or max_analyzed != 1:
        cfg = store.load()
        if cameras is not None:
            cfg.cameras = cameras
        cfg.max_analyzed = max_analyzed
        store.save(cfg)
    w = MainWindow(store, use_sim=True)
    qtbot.addWidget(w)
    w.show()
    return w


def pts(w, slot):
    return [c for c in w.grid.cells[slot].session.sim.received if c.startswith("#PTS")]


def test_two_sims_open_in_grid(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert w.grid.ids() == ["sim1", "sim2", None, None]
    assert w.grid.selected == 0
    qtbot.waitUntil(lambda: w.cells[0].preview.has_frame() and w.cells[1].preview.has_frame(), timeout=3000)
    assert not w.cells[2].preview.has_frame()
    w.close()


def test_keyboard_drives_selected_only(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.select_slot(1)
    qtbot.keyPress(w, Qt.Key_Right)
    qtbot.wait(500)
    qtbot.keyRelease(w, Qt.Key_Right)
    qtbot.wait(400)
    assert pts(w, 1) and pts(w, 1)[-1] == "#PTS5050"
    assert w.grid.cells[1].session.sim.pan_deg > 0.3  # стрелки — через кривую чувствительности
    assert not pts(w, 0)
    w.close()


def test_click_selects_cell_first(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.waitUntil(lambda: w.cells[1].preview.has_frame(), timeout=3000)
    qtbot.mouseClick(w.cells[1].preview, Qt.LeftButton, pos=QPoint(5, 5))
    assert w.grid.selected == 1
    assert w.session is w.grid.cells[1].session
    w.close()


def test_escape_stops_all(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    for c in w.grid.cells[:2]:
        c.session.set_pan_tilt_velocity(1, 0)
    qtbot.wait(300)
    qtbot.keyPress(w, Qt.Key_Escape)
    qtbot.wait(400)
    assert pts(w, 0)[-1] == "#PTS5050" and pts(w, 1)[-1] == "#PTS5050"
    w.close()


def test_escape_fast_with_dead_camera(qtbot, tmp_path):
    dead = CameraConfig(id="dead", name="Мёртвая", ip="10.255.255.1")
    w = make(qtbot, tmp_path, cameras=[dead])
    w.set_cell_camera(2, "dead")
    t0 = time.monotonic()
    w.emergency_stop()
    assert time.monotonic() - t0 < 0.3
    qtbot.wait(300)
    assert pts(w, 0)[-1] == "#PTS5050" if pts(w, 0) else True
    w.close()


def test_cell_choices_exclude_other_cells(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    ids1 = [w.cells[1].combo.itemData(i) for i in range(w.cells[1].combo.count())]
    assert None in ids1 and "sim2" in ids1 and "sim1" not in ids1
    ids3 = [w.cells[3].combo.itemData(i) for i in range(w.cells[3].combo.count())]
    assert "sim1" not in ids3 and "sim2" not in ids3 and "sim3" in ids3
    import pytest

    with pytest.raises(ValueError):
        w.set_cell_camera(2, "sim1")
    assert w.grid.cells[2] is None
    w.close()


def test_none_closes_session_with_stop(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    sim = w.grid.cells[1].session.sim
    w.grid.cells[1].session.set_pan_tilt_velocity(1, 0)
    qtbot.wait(300)
    w.set_cell_camera(1, None)
    assert w.grid.cells[1] is None
    qtbot.waitUntil(lambda: [c for c in sim.received if c.startswith("#PTS")][-1] == "#PTS5050", timeout=2000)
    assert not w.cells[1].preview.has_frame()
    w.close()


def test_tracking_refused_over_limit(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.waitUntil(lambda: w.grid.cells[0].session.follower is not None, timeout=3000)
    w.grid.cells[0].session.follower.enabled = True  # слежение на клетке 1 включено
    w.select_slot(1)
    w._on_follow_requested(True)
    assert "Уже следит Симулятор 1" in w.follow_panel.status_label.text()
    assert w.grid.cells[1].session.follower is None or not w.grid.cells[1].session.follower.enabled
    w.grid.cells[0].session.follower.enabled = False
    w.close()


def test_analysis_follows_selection(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert w.grid.cells[0].session.analyzer is not None and w.grid.cells[1].session.analyzer is None
    w.select_slot(1)
    assert w.grid.cells[1].session.analyzer is not None and w.grid.cells[0].session.analyzer is None
    assert w.cells[1].preview._analyzer is w.grid.cells[1].session.analyzer
    assert w.cells[0].preview._analyzer is None
    w.close()


def test_max_analyzed_two(qtbot, tmp_path):
    w = make(qtbot, tmp_path, max_analyzed=2)
    assert all(c.session.analyzer is not None for c in w.grid.cells[:2])
    next(a for a in w.max_analyzed_actions if a.data() == 1).trigger()
    assert w.cfg.max_analyzed == 1
    assert sum(c.session.analyzer is not None for c in w.grid.cells[:2]) == 1
    w.close()


def test_grid_not_saved_in_sim(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.select_slot(1)
    w.set_cell_camera(2, "sim3")
    w.close()
    cfg = ConfigStore(tmp_path / "c.json").load()
    assert cfg.grid == [None] * 4
    assert cfg.last_camera_id is None


def test_grid_saved_for_real_cameras(qtbot, tmp_path):
    cams = [CameraConfig(id="a", name="A", ip="10.0.0.12"), CameraConfig(id="b", name="B", ip="10.0.0.13")]
    store = ConfigStore(tmp_path / "c.json")
    cfg = store.load()
    cfg.cameras = cams
    store.save(cfg)
    w = MainWindow(store)
    qtbot.addWidget(w)
    w.set_cell_camera(1, "b")
    w.select_slot(1)
    w.close()
    cfg = store.load()
    assert cfg.grid == ["a", "b", None, None]
    assert cfg.last_camera_id == "b"
