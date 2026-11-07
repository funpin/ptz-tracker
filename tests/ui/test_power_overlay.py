"""Камера в режиме ожидания: круглая кнопка питания поверх видео вместо ошибки."""

from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow
from ptztracker.ui.state import no_video_text


def make(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.show()
    return w


def test_overlay_shows_on_standby(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.waitUntil(lambda: w.session.power == "on", timeout=4000)
    assert not w.power_overlay.isVisible()
    w.session.set_power(False)
    qtbot.waitUntil(lambda: w.power_overlay.isVisible(), timeout=3000)
    assert "режим ожидания" in w.power_overlay.title_label.text()
    assert "Симулятор" in w.power_overlay.title_label.text()
    assert w.power_overlay.power_btn.isEnabled()
    w.close()


def test_power_button_turns_on(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.session.sim.power_on_s = 0.6
    qtbot.waitUntil(lambda: w.session.power == "on", timeout=4000)
    w.session.set_power(False)
    qtbot.waitUntil(lambda: w.power_overlay.isVisible(), timeout=3000)
    w.power_overlay.power_btn.click()
    qtbot.waitUntil(lambda: "#O1" in w.session.sim.received, timeout=2000)
    qtbot.waitUntil(lambda: not w.power_overlay.power_btn.isEnabled()
                    or not w.power_overlay.isVisible(), timeout=2000)
    qtbot.waitUntil(lambda: not w.power_overlay.isVisible(), timeout=5000)
    w.close()


def test_overlay_hidden_when_power_unknown(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    orig = w.session.sim._dispatch
    w.session.sim._dispatch = lambda cmd: "ER1:O" if cmd == "#O" else orig(cmd)
    qtbot.waitUntil(lambda: w.session.power == "unknown", timeout=8000)  # пульс раз в 2 с, за стартовыми запросами
    assert not w.power_overlay.isVisible()
    w.close()


def test_no_video_text_is_human():
    err = ("[Errno 1094995529] Invalid data found when processing input: "
           "'rtsp://admin:***@10.0.0.12:554/MediaInput/h264/stream_1'")
    text = no_video_text("KZ_PTZ_2", err)
    first, second = text.split("\n")
    assert first == "Нет видео с камеры KZ_PTZ_2"
    assert "Errno" not in second and "rtsp://" not in second and len(second) <= 60
    assert no_video_text("KZ", None) == "Нет видео с камеры KZ"
    assert "не отвечает" in no_video_text("KZ", "timed out after 5 s")


def test_target_controls_on_video(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert w.reset_target_btn.parent() is not None
    assert w.video_frame.isAncestorOf(w.reset_target_btn)
    assert w.video_frame.isAncestorOf(w.skeleton_check) and w.video_frame.isAncestorOf(w.target_label)
    w.skeleton_check.setChecked(True)
    assert w.preview.show_skeleton
    w.close()


def test_standby_asks_and_stops_follow(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    w = make(qtbot, tmp_path)
    qtbot.waitUntil(lambda: bool(w.session.analyzer.latest() and w.session.analyzer.latest().tracks), timeout=8000)
    tr = w.session.analyzer.latest().tracks[0]
    w.session.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2)
    w.track_btn.click()
    qtbot.waitUntil(lambda: w.session.follower.enabled, timeout=2000)
    w.agent_panel.allow_check.setChecked(True)
    asked = []

    def no(*a, **k):
        asked.append(w.session.follower.enabled)  # к вопросу слежение уже выключено
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", no)
    w.power_action.trigger()
    assert asked == [False]
    assert not w.agent_panel.allow_check.isChecked()
    qtbot.wait(300)
    assert "#O0" not in w.session.sim.received
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    w.power_action.trigger()
    qtbot.waitUntil(lambda: "#O0" in w.session.sim.received, timeout=1500)
    w.close()


def test_power_action_text_follows_state(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.waitUntil(lambda: w.session.power == "on", timeout=4000)
    w._update_status()
    assert "режим ожидания" in w.power_action.text()
    w.session.set_power(False)
    qtbot.waitUntil(lambda: w.power_action.text() == "Включить камеру", timeout=3000)
    w.power_action.trigger()
    qtbot.waitUntil(lambda: "#O1" in w.session.sim.received, timeout=1500)
    w.close()
