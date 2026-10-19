from PySide6.QtCore import Qt

from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow


def make(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.show()
    return w


def test_main_window_sim_preview_and_arrow(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.waitUntil(lambda: w.preview.has_frame(), timeout=3000)
    qtbot.keyPress(w, Qt.Key_Right)
    qtbot.wait(500)
    qtbot.keyRelease(w, Qt.Key_Right)
    qtbot.wait(400)
    sim = w.session.sim
    assert sim.pan_deg > 1
    assert [c for c in sim.received if c.startswith("#PTS")][-1] == "#PTS5050"
    w.close()


def test_close_stops_camera(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    sim = w.session.sim
    qtbot.keyPress(w, Qt.Key_Right)
    qtbot.wait(400)
    w.close()
    assert [c for c in sim.received if c.startswith("#PTS")][-1] == "#PTS5050"


def test_escape_is_emergency_stop(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    sim = w.session.sim
    w.session.set_pan_tilt_velocity(1, 0)
    qtbot.wait(300)
    qtbot.keyPress(w, Qt.Key_Escape)
    qtbot.wait(400)
    assert [c for c in sim.received if c.startswith("#PTS")][-1] == "#PTS5050"
    w.close()


def test_deactivation_releases_held_keys(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    sim = w.session.sim
    qtbot.keyPress(w, Qt.Key_Left)
    qtbot.wait(300)
    w.release_all_held()  # то же, что вызывается при потере активности окна
    qtbot.wait(400)
    assert [c for c in sim.received if c.startswith("#PTS")][-1] == "#PTS5050"
    w.close()


def test_sim_camera_not_persisted_and_last_camera_saved(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.close()
    cfg = ConfigStore(tmp_path / "c.json").load()
    assert all(c.kind != "sim" for c in cfg.cameras)
    assert [c.ip for c in cfg.cameras] == ["10.0.0.12"]


def test_found_rtsp_url_saved_without_password(qtbot, tmp_path):
    store = ConfigStore(tmp_path / "c.json")
    w = MainWindow(store)
    qtbot.addWidget(w)
    w.session.found_rtsp_url = "rtsp://admin:secret@10.0.0.12/MediaInput/h264/stream_1"
    w._update_status()
    w.close()
    cam = store.load().cameras[0]
    assert cam.rtsp_url == "rtsp://10.0.0.12/MediaInput/h264/stream_1"
