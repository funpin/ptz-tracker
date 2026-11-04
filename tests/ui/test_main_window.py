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
    next(a for a in w.stream_actions.actions() if a.data() == "h264_1").trigger()
    assert w.session is not old
    assert w.session.frames.urls == ["rtsp://10.0.0.12/MediaInput/h264/stream_1"]
    w.close()
    assert store.load().cameras[0].stream == "h264_1"


def test_stream_combo_disabled_for_sim(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    assert not any(a.isEnabled() for a in w.stream_actions.actions())
    w.close()


# --- «Сохранить момент» (F9) ---------------------------------------------


def make_rec(qtbot, tmp_path):
    """Окно с симулятором; записи пишутся в tmp_path, а не на рабочий стол."""
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.cfg.record_dir = str(tmp_path / "rec")
    w.set_cell_camera(1, None)  # одна открытая камера: одна запись на F9
    w.set_cell_camera(w.grid.selected, w._current_cfg().id)  # новый сеанс подхватывает настройки записи
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
    w.set_cell_camera(w.grid.selected, None)
    w.save_moment_action.trigger()
    assert "камер" in w.statusBar().currentMessage()
    w.close()


def test_f9_with_recording_off_shows_message(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.cfg.record_enabled = False
    w.set_cell_camera(1, None)  # одна открытая камера
    w.set_cell_camera(w.grid.selected, w._current_cfg().id)
    w.save_moment_action.trigger()
    qtbot.waitUntil(lambda: "Запись выключена" in w.statusBar().currentMessage(), timeout=3000)
    w._update_status()
    assert "выключен" in w.blackbox_btn.toolTip()
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
    assert "последних 5 мин" in w.blackbox_btn.toolTip() and w.blackbox_btn.text() == ""
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


def test_recording_dialog_ok_keeps_default_folder_empty(qtbot, tmp_path):
    from ptztracker.ui.recording_dialog import RecordingDialog

    cfg = ConfigStore(tmp_path / "c.json").load()
    assert cfg.record_dir == ""
    d = RecordingDialog(cfg)
    qtbot.addWidget(d)
    d.apply()  # папку не трогали
    assert cfg.record_dir == ""


def test_calibration_silences_polling_and_resumes(qtbot, tmp_path, monkeypatch):
    """Во время мастера калибровки пульс и опрос положения молчат (иначе камера отвечает eR2), после — возобновляются."""
    seen = {}

    class FakeDialog:
        def __init__(self, factory, on_saved, parent=None):
            self.factory = factory

        def windowTitle(self):  # noqa: N802
            return "Калибровка"

        def setWindowTitle(self, _t):  # noqa: N802
            pass

        def exec(self):
            self.factory(lambda m: None)  # как рабочий поток мастера
            s = w.session
            seen["quiet"] = s._quiet
            seen["polling_paused"] = s.follower.polling_paused

    monkeypatch.setattr("ptztracker.ui.calibration_dialog.CalibrationDialog", FakeDialog)
    w = make(qtbot, tmp_path)
    s = w.session
    follower = s.follower
    w._run_calibration()
    assert seen == {"quiet": True, "polling_paused": True}
    assert not s._quiet and not follower.polling_paused
    assert s.follower is follower and follower._thread.is_alive() and follower._zthread.is_alive()
    assert w.preview._follower is follower
    w.close()


# --- агент: разрешение управлять снимается оператором -------------------------


def allow_agent(w):
    w.agent_panel.allow_check.setChecked(True)
    w.agent_panel.off_air_check.setChecked(True)
    assert w.agent_panel.control_allowed and w.agent_panel.not_on_air


def agent_revoked(w) -> bool:
    p = w.agent_panel
    return not (p.control_allowed or p.not_on_air or p.allow_check.isChecked() or p.off_air_check.isChecked())


def test_agent_switches_off_by_default_and_not_saved(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert agent_revoked(w)
    allow_agent(w)
    w.close()
    raw = (tmp_path / "c.json").read_text(encoding="utf-8")
    assert "agent" not in raw and "air" not in raw
    w2 = make(qtbot, tmp_path)
    assert agent_revoked(w2)
    w2.close()


def test_api_token_generated_once_and_saved(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    tok = w.cfg.api_token
    assert len(tok) >= 32
    w.close()
    assert ConfigStore(tmp_path / "c.json").load().api_token == tok
    w2 = make(qtbot, tmp_path)
    assert w2.cfg.api_token == tok
    w2.close()


def test_esc_revokes_agent_control(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    allow_agent(w)
    qtbot.keyPress(w, Qt.Key_Escape)
    assert agent_revoked(w)
    w.close()


def test_manual_joystick_revokes_agent_control(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    allow_agent(w)
    w.controls._on_joystick(0.5, 0.0)
    assert agent_revoked(w)
    w.controls.release_all()
    # стрелки и пресеты — то же ручное управление
    allow_agent(w)
    qtbot.keyPress(w, Qt.Key_Left)
    assert agent_revoked(w)
    qtbot.keyRelease(w, Qt.Key_Left)
    allow_agent(w)
    w.session.recall_preset(0)
    assert agent_revoked(w)
    # окно теряет фокус (оператор переключился на агента): стопы уходят, разрешение остаётся
    allow_agent(w)
    w.release_all_held()
    assert w.agent_panel.control_allowed
    w.close()


def test_camera_switch_revokes_agent_control(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    allow_agent(w)
    w.set_cell_camera(w.grid.selected, w._current_cfg().id)
    assert agent_revoked(w)
    w.close()


def test_divergence_and_emergency_stop_revoke_agent_control(qtbot, tmp_path):
    import threading

    w = make(qtbot, tmp_path)
    f = w.session.follower
    allow_agent(w)
    f.enabled = True
    f.disable("выключено оператором")  # обычное выключение разрешение не трогает
    qtbot.wait(50)
    assert w.agent_panel.control_allowed
    f.enabled = True
    t = threading.Thread(target=f.disable, args=("камера раскачивалась — она быстрее, чем ожидалось",))
    t.start()
    t.join()
    assert not w.agent_panel.control_allowed  # снимок меняется сразу, ещё в потоке слежения
    qtbot.waitUntil(lambda: agent_revoked(w), timeout=1000)
    allow_agent(w)
    f.enabled = True
    f.disable("аварийный стоп")
    qtbot.waitUntil(lambda: agent_revoked(w), timeout=1000)
    w.close()


def test_busy_api_port_keeps_window_working(qtbot, tmp_path):
    import socket

    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    port = busy.getsockname()[1]
    store = ConfigStore(tmp_path / "c.json")
    cfg = store.load()
    cfg.api_port = port
    store.save(cfg)
    try:
        w = make(qtbot, tmp_path)
        assert w._api is None
        assert f"порт {port} занят" in w.agent_panel.api_label.text()
        assert "занят" in w.statusBar().currentMessage()
        qtbot.waitUntil(lambda: w.preview.has_frame(), timeout=3000)
        w.close()
    finally:
        busy.close()


def test_operator_follow_off_revokes_agent_control(qtbot, tmp_path):
    """Оператор сам выключил слежение (пробел или кнопка) — забрал камеру: агент теряет разрешение."""
    w = make(qtbot, tmp_path)
    f = w.session.follower
    allow_agent(w)
    f.enabled = True
    qtbot.keyPress(w, Qt.Key_Space)
    assert not f.enabled and agent_revoked(w)
    allow_agent(w)
    f.enabled = True
    w.follow_panel.followRequested.emit(False)  # кнопка «Слежение»
    assert not f.enabled and agent_revoked(w)
    w.close()


def test_speed_scale_saved_at_most_once_a_minute(qtbot, tmp_path):
    """Поправку скорости от слежения окно пишет в настройки камеры (поток Qt), не чаще раза в минуту;
    новая калибровка её сбрасывает."""
    from ptztracker.control.camera_model import DEFAULT_TABLE_ID

    store = ConfigStore(tmp_path / "c.json")
    w = MainWindow(store)
    qtbot.addWidget(w)
    calib = w.session.follower.calib
    calib.pan_scale = 1.4
    w._update_status()
    # без калибровки — с ярлыком таблицы: поправка к прежней таблице не годится для нынешней
    assert store.load().cameras[0].speed_scale == {"pan": 1.4, "tilt": 1.0, "zoom": 1.0, "table": DEFAULT_TABLE_ID}
    calib.pan_scale = 1.6
    w._update_status()
    assert store.load().cameras[0].speed_scale["pan"] == 1.4  # минута не прошла
    w._speed_scale_saved[0] -= 61
    w._update_status()
    assert store.load().cameras[0].speed_scale["pan"] == 1.6
    w.close()
    assert store.load().cameras[0].speed_scale["pan"] == 1.6


def test_new_calibration_resets_speed_scale(qtbot, tmp_path, monkeypatch):
    """Мастер калибровки дал новую таблицу: прежняя поправка скорости к ней не относится — сбрасывается
    и в настройках, и в новом слежении."""
    from ptztracker.control.camera_model import DEFAULT_TABLE_ID, default_calibration

    class FakeDialog:
        def __init__(self, factory, on_saved, parent=None):
            self.on_saved = on_saved

        def windowTitle(self):  # noqa: N802
            return "Калибровка"

        def setWindowTitle(self, _t):  # noqa: N802
            pass

        def exec(self):
            self.on_saved(default_calibration().to_json())

    monkeypatch.setattr("ptztracker.ui.calibration_dialog.CalibrationDialog", FakeDialog)
    store = ConfigStore(tmp_path / "c.json")
    cfg = store.load()
    cfg.cameras[0].speed_scale = {"pan": 1.4, "tilt": 1.0, "zoom": 1.0, "table": DEFAULT_TABLE_ID}
    store.save(cfg)
    w = MainWindow(store)
    qtbot.addWidget(w)
    assert w.session.follower.calib.pan_scale == 1.4
    w._run_calibration()
    assert store.load().cameras[0].speed_scale is None
    assert w.session.follower.calib.pan_scale == 1.0
    w.close()


def test_camera_switch_resets_speed_scale_timer(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w._speed_scale_saved[0] = 123.0
    w.set_cell_camera(w.grid.selected, w._current_cfg().id)
    assert 0 not in w._speed_scale_saved  # «раз в минуту» — для камеры заново
    w.close()
