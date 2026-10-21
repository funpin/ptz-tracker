from PySide6.QtCore import Qt

from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow


def make(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.show()
    return w


def select(qtbot, w):
    qtbot.waitUntil(lambda: bool(w.session.analyzer.latest() and w.session.analyzer.latest().tracks), timeout=8000)
    tr = w.session.analyzer.latest().tracks[0]
    w.session.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2)


def test_credit_visible(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert "by funpin.ru & Claude" in w.credit_label.text()
    w.close()


def test_stop_button_is_emergency_stop(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    select(qtbot, w)
    w.follow_panel.follow_btn.click()
    assert w.session.follower.enabled
    w.controls.stop_btn.click()
    assert not w.session.follower.enabled
    qtbot.wait(400)
    assert [c for c in w.session.sim.received if c.startswith("#PTS")][-1] == "#PTS5050"
    w.close()


def test_tracking_tag_follows_state(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    select(qtbot, w)
    qtbot.keyClick(w, Qt.Key_Space)
    qtbot.waitUntil(lambda: w.video_frame.tracking_tag.text() == "СЛЕЖЕНИЕ", timeout=2000)
    qtbot.keyClick(w, Qt.Key_Space)
    qtbot.waitUntil(lambda: w.video_frame.tracking_tag.text() == "ВЫКЛ", timeout=2000)
    w.close()


def test_tile_height_constant_in_window(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    before = w.follow_panel.follow_btn.height()
    select(qtbot, w)
    w.follow_panel.follow_btn.click()
    qtbot.wait(300)
    assert before == w.follow_panel.follow_btn.height() == 118
    w.close()


def test_window_fits_1640x850(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert w.minimumSizeHint().height() <= 760
    assert w.width() <= 1640
    w.close()
