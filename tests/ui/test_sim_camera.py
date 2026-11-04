"""«Симулятор (для тренировки)» в обычной сборке: меню ☰, диалог камеры, метка на видео."""

from ptztracker.config import ConfigStore
from ptztracker.ui.camera_dialog import CameraDialog
from ptztracker.ui.main_window import MainWindow


def make(qtbot, tmp_path):
    """Обычный запуск, без --sim: в списке только камера из настроек."""
    w = MainWindow(ConfigStore(tmp_path / "c.json"))
    qtbot.addWidget(w)
    w.show()
    return w


def sim_count(w) -> int:
    return sum(1 for c in w._cameras() if c.kind == "sim")


def test_menu_adds_and_opens_sim(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert sim_count(w) == 0
    w.sim_action.trigger()
    assert w._current_cfg().kind == "sim"
    assert w.session.cfg.kind == "sim"
    assert w.camera_combo.currentText() == "Тренировка (симулятор)"
    assert sim_count(w) == 1
    w.close()


def test_menu_sim_added_once_and_reselected(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.sim_action.trigger()
    w.camera_combo.setCurrentIndex(0)  # обратно на настоящую камеру
    assert w._current_cfg().kind == "panasonic"
    w.sim_action.trigger()
    assert sim_count(w) == 1
    assert w._current_cfg().kind == "sim"
    w.close()


def test_menu_sim_survives_restart_but_start_is_real_camera(qtbot, tmp_path):
    store = ConfigStore(tmp_path / "c.json")
    w = MainWindow(store)
    qtbot.addWidget(w)
    w.sim_action.trigger()
    w.close()
    w2 = MainWindow(store)
    qtbot.addWidget(w2)
    assert w2._current_cfg().kind == "panasonic"  # на эфир с симулятором не стартуем
    assert sim_count(w2) == 1
    w2.close()


def test_sim_camera_can_be_deleted(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    w = make(qtbot, tmp_path)
    w.sim_action.trigger()
    assert w.del_action.isEnabled() and w.edit_action.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    w._delete_camera()
    assert sim_count(w) == 0
    w.close()


def test_sim_badge_visible_for_sim_only(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert w.camera_combo.currentData() and w._current_cfg().kind == "panasonic"
    assert not w.sim_badge.isVisible()
    w.sim_action.trigger()
    assert w.sim_badge.isVisible()
    assert w.sim_badge.text() == "СИМУЛЯТОР"
    w.camera_combo.setCurrentIndex(0)
    assert not w.sim_badge.isVisible()
    w.close()


def test_sim_badge_with_flag_sim(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.show()
    assert w._current_cfg().kind == "sim"
    assert w.sim_badge.isVisible()
    assert w.camera_combo.currentText() == "Симулятор"  # слово уже в названии: «(симулятор)» не дублируем
    w.close()


def test_dialog_creates_sim_camera(qtbot):
    d = CameraDialog()
    qtbot.addWidget(d)
    d.show()
    assert d.kind_combo.currentData() == "panasonic"
    assert d.ip_edit.isVisible()
    d.kind_combo.setCurrentIndex(d.kind_combo.findData("sim"))
    for hidden in (d.ip_edit, d.user_edit, d.pass_edit, d.rtsp_edit, d.test_button):
        assert not hidden.isVisible()
    assert d.name_edit.text() == "Тренировка"  # пустое имя подставляется
    assert d.ok_button.isEnabled()  # IP не нужен
    c = d.result_config()
    assert (c.kind, c.ip, c.username, c.rtsp_url, c.name) == ("sim", "sim", None, None, "Тренировка")
    assert d.password() is None


def test_dialog_back_to_panasonic_needs_ip(qtbot):
    d = CameraDialog()
    qtbot.addWidget(d)
    d.show()
    d.kind_combo.setCurrentIndex(d.kind_combo.findData("sim"))
    d.kind_combo.setCurrentIndex(d.kind_combo.findData("panasonic"))
    assert d.ip_edit.isVisible()
    assert not d.ok_button.isEnabled()
    d.ip_edit.setText("10.0.0.14")
    assert not d.ok_button.isEnabled()  # подставленное «Тренировка» стёрлось: нужно своё имя
    d.name_edit.setText("Сцена")
    assert d.ok_button.isEnabled()
    assert d.result_config().kind == "panasonic"


def test_dialog_edit_sim_keeps_kind(qtbot):
    from ptztracker.config import CameraConfig

    d = CameraDialog(CameraConfig(id="s1", name="Зал", ip="sim", kind="sim"))
    qtbot.addWidget(d)
    d.show()
    assert d.kind_combo.currentData() == "sim"
    assert not d.kind_combo.isEnabled()  # тип у готовой камеры не меняют
    assert not d.ip_edit.isVisible()
    assert d.result_config().kind == "sim"


def test_add_camera_via_dialog_selects_sim(qtbot, tmp_path, monkeypatch):
    w = make(qtbot, tmp_path)
    monkeypatch.setattr(CameraDialog, "exec", lambda self: (self.kind_combo.setCurrentIndex(
        self.kind_combo.findData("sim")), True)[1])
    w._add_camera()
    assert w._current_cfg().kind == "sim"
    assert w.sim_badge.isVisible()
    w.close()


def _no_passwords(monkeypatch):
    from ptztracker.ui import main_window

    calls = []
    monkeypatch.setattr(main_window, "set_password", lambda *a, **k: calls.append(("set", a)))
    monkeypatch.setattr(main_window, "get_password", lambda *a, **k: calls.append(("get", a)))
    return calls


def test_sim_add_edit_delete_never_touch_passwords(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    calls = _no_passwords(monkeypatch)
    w = make(qtbot, tmp_path)
    monkeypatch.setattr(CameraDialog, "exec", lambda self: (self.kind_combo.setCurrentIndex(
        self.kind_combo.findData("sim")), True)[1])
    w._add_camera()
    assert w._current_cfg().kind == "sim"
    monkeypatch.setattr(CameraDialog, "exec", lambda self: (self.name_edit.setText("Зал"), True)[1])
    w._edit_camera()
    assert w._current_cfg().name == "Зал"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    w._delete_camera()
    assert sim_count(w) == 0
    assert calls == []
    w.close()


def test_edit_stored_sim_keeps_id_interval_and_atem(qtbot, tmp_path, monkeypatch):
    from ptztracker.config import CameraConfig

    _no_passwords(monkeypatch)
    store = ConfigStore(tmp_path / "c.json")
    cfg = store.load()
    cfg.cameras.append(CameraConfig(id="s1", name="Зал", ip="sim", kind="sim", min_interval=0.2, atem_input=3))
    store.save(cfg)
    w = MainWindow(store)
    qtbot.addWidget(w)
    w._fill_combo("s1")
    monkeypatch.setattr(CameraDialog, "exec", lambda self: (self.name_edit.setText("Зал 2"), True)[1])
    w._edit_camera()
    c = w._current_cfg()
    assert (c.id, c.name, c.min_interval, c.atem_input) == ("s1", "Зал 2", 0.2, 3)
    w.close()


def test_start_without_last_camera_prefers_real_over_stored_sim(qtbot, tmp_path):
    from ptztracker.config import CameraConfig

    store = ConfigStore(tmp_path / "c.json")
    cfg = store.load()
    sim = CameraConfig(id="s1", name="Тренировка", ip="sim", kind="sim")
    real = CameraConfig(id="r1", name="Сцена", ip="10.0.0.12")
    cfg.cameras = [sim, real]
    cfg.last_camera_id = None
    store.save(cfg)
    w = MainWindow(store)
    qtbot.addWidget(w)
    assert w._current_cfg().id == "r1"
    assert not w.sim_badge.isVisible()
    w._fill_combo("gone")  # устаревший id — то же самое
    assert w._current_cfg().id == "r1"
    w.close()


def test_stored_sim_keeps_composition_and_effort_but_no_speed_scale(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.sim_action.trigger()
    w._on_composition("waist_center")
    w._on_effort(0.7)
    w.close()
    cfg = ConfigStore(tmp_path / "c.json").load()
    sim = next(c for c in cfg.cameras if c.kind == "sim")
    assert (sim.composition, sim.effort) == ("waist_center", 0.7)
    assert sim.speed_scale is None


def test_dialog_sim_to_panasonic_clears_autofilled_name(qtbot):
    d = CameraDialog()
    qtbot.addWidget(d)
    d.kind_combo.setCurrentIndex(d.kind_combo.findData("sim"))
    assert d.name_edit.text() == "Тренировка"
    d.kind_combo.setCurrentIndex(d.kind_combo.findData("panasonic"))
    assert d.name_edit.text() == ""
    d.kind_combo.setCurrentIndex(d.kind_combo.findData("sim"))
    d.name_edit.setText("Зал")  # своё имя не трогаем
    d.kind_combo.setCurrentIndex(d.kind_combo.findData("panasonic"))
    assert d.name_edit.text() == "Зал"
