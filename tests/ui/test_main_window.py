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


def test_stream_combo_saves_and_restarts(qtbot, tmp_path):
    store = ConfigStore(tmp_path / "c.json")
    w = MainWindow(store)
    qtbot.addWidget(w)
    old = w.session
    w.stream_combo.setCurrentIndex(w.stream_combo.findData("h264_2"))
    assert w.session is not old
    assert w.session.frames.urls == ["rtsp://10.0.0.12/MediaInput/h264/stream_2"]
    w.close()
    assert store.load().cameras[0].stream == "h264_2"


def test_stream_combo_disabled_for_sim(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    assert not w.stream_combo.isEnabled()
    w.close()


# --- «Сохранить момент» (F9) ---------------------------------------------


def make_rec(qtbot, tmp_path):
    """Окно с симулятором; записи пишутся в tmp_path, а не на рабочий стол."""
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.cfg.record_dir = str(tmp_path / "rec")
    w._activate(w._current_cfg())  # новый сеанс подхватывает настройки записи
    w.show()
    return w


def test_f9_saves_without_disabling_follow(qtbot, tmp_path):
    w = make_rec(qtbot, tmp_path)
    qtbot.waitUntil(lambda: w.preview.has_frame(), timeout=3000)
    f = w.session.follower
    f.enabled = True  # слежение включено; F9 не должно его трогать
    qtbot.keyClick(w, Qt.Key_F9)
    assert f.enabled
    qtbot.waitUntil(lambda: "Запись сохранена" in w.statusBar().currentMessage(), timeout=10000)
    zips = list((tmp_path / "rec").glob("*.zip"))
    assert len(zips) == 1
    assert zips[0].name in w.statusBar().currentMessage()
    assert f.enabled
    w.close()


def test_f9_without_camera_shows_message(qtbot, tmp_path):
    w = make_rec(qtbot, tmp_path)
    w._activate(None)
    w.save_moment_action.trigger()
    assert "камер" in w.statusBar().currentMessage()
    w.close()


def test_f9_with_recording_off_shows_message(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.cfg.record_enabled = False
    w._activate(w._current_cfg())
    w.save_moment_action.trigger()
    qtbot.waitUntil(lambda: "Запись выключена" in w.statusBar().currentMessage(), timeout=3000)
    assert not w.record_label.text()
    w.close()


def test_save_runs_in_background(qtbot, tmp_path):
    from concurrent.futures import Future

    w = make_rec(qtbot, tmp_path)
    fut: Future = Future()
    w.session.save_moment = lambda reason="F9": fut  # zip ещё «пишется»
    w.save_moment_action.trigger()
    assert "Сохраняю" in w.statusBar().currentMessage()
    qtbot.wait(50)  # окно отвечает, пока Future не завершён
    assert w.isVisible()
    fut.set_result(tmp_path / "rec" / "moment.zip")  # завершение приходит из чужого потока
    qtbot.waitUntil(lambda: "Запись сохранена: moment.zip" in w.statusBar().currentMessage(), timeout=3000)
    assert w.show_folder_btn.isVisible()
    w.close()


def test_record_indicator_shows_buffer(qtbot, tmp_path):
    w = make_rec(qtbot, tmp_path)
    w._update_status()
    assert "запись 5 мин" in w.record_label.text()
    w.close()


def test_recording_settings_saved(qtbot, tmp_path):
    from ptztracker.ui.recording_dialog import RecordingDialog

    store = ConfigStore(tmp_path / "c.json")
    cfg = store.load()
    d = RecordingDialog(cfg)
    qtbot.addWidget(d)
    d.minutes.setValue(10)
    d.to_disk.setChecked(True)
    d.folder.setText(str(tmp_path / "x"))
    d.apply()
    assert (cfg.record_buffer_s, cfg.record_to_disk, cfg.record_dir) == (600, True, str(tmp_path / "x"))


def test_calibration_silences_polling_and_resumes(qtbot, tmp_path, monkeypatch):
    """Во время мастера калибровки пульс и опрос положения молчат (иначе камера отвечает eR2), после — возобновляются."""
    seen = {}

    class FakeDialog:
        def __init__(self, factory, on_saved, parent=None):
            self.factory = factory

        def exec(self):
            self.factory(lambda m: None)  # как рабочий поток мастера
            s = w.session
            seen["quiet"] = s._quiet
            seen["follower_stopped"] = s.follower._stop.is_set()
            seen["heartbeat_stopped"] = s._hb_stop.is_set()

    monkeypatch.setattr("ptztracker.ui.calibration_dialog.CalibrationDialog", FakeDialog)
    w = make(qtbot, tmp_path)
    s = w.session
    old_follower = s.follower
    w._run_calibration()
    assert seen == {"quiet": True, "follower_stopped": True, "heartbeat_stopped": True}
    assert not s._quiet and not s._hb_stop.is_set()
    assert s.follower is not old_follower and not s.follower._stop.is_set()
    w.close()
