from PySide6.QtCore import Qt

from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow


def make(qtbot, tmp_path, sim=True):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=sim)
    qtbot.addWidget(w)
    w.resize(1400, 860)
    w.show()
    if sim:
        qtbot.waitUntil(
            lambda: w.session.analyzer.latest() is not None and len(w.session.analyzer.latest().tracks) > 0, timeout=4000
        )
    return w


def select(w):
    tr = w.session.analyzer.latest().tracks[0]
    w.session.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2)


def test_follow_button_requires_target(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.follow_panel.follow_btn.click()
    assert not w.session.follower.enabled
    assert "кликните" in w.follow_panel.status_label.text().lower()
    assert not w.follow_panel.follow_btn.isChecked()
    w.close()


def test_space_toggles_follow_after_target(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    select(w)
    qtbot.keyClick(w, Qt.Key_Space)
    qtbot.waitUntil(lambda: w.session.follower.enabled and w.follow_panel.follow_btn.isChecked(), timeout=2000)
    qtbot.keyClick(w, Qt.Key_Space)
    qtbot.waitUntil(lambda: not w.session.follower.enabled and not w.follow_panel.follow_btn.isChecked(), timeout=2000)
    w.close()


def test_joystick_turns_follow_off_and_shows_reason(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    select(w)
    w.follow_panel.follow_btn.click()
    qtbot.waitUntil(lambda: w.session.follower.enabled, timeout=2000)
    w.controls.joystick.moved.emit(0.5, 0.0)
    assert not w.session.follower.enabled
    qtbot.waitUntil(lambda: "ручное управление" in w.follow_panel.status_label.text(), timeout=2000)
    assert not w.follow_panel.follow_btn.isChecked()
    w.controls.joystick.moved.emit(0.0, 0.0)
    w.close()


def test_composition_and_effort_saved(qtbot, tmp_path):
    store = ConfigStore(tmp_path / "c.json")
    w = MainWindow(store)
    qtbot.addWidget(w)
    w.follow_panel.comp_buttons["wide"].click()
    w.follow_panel.effort_slider.setValue(20)
    w.follow_panel.effort_slider.sliderReleased.emit()
    assert w.session.follower.controller.composition.key == "wide"
    assert abs(w.session.follower.controller.effort.gain - 0.9 - (1.3 - 0.9) * 0.4) < 1e-6
    w.close()
    cam = store.load().cameras[0]
    assert cam.composition == "wide" and abs(cam.effort - 0.2) < 1e-9


def test_number_keys_select_composition(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.keyClick(w, Qt.Key_2)  # 1 пояс, 2 рост, 3 общий
    assert w.follow_panel.comp_buttons["full"].isChecked()
    assert w.session.follower.controller.composition.key == "full"
    qtbot.keyClick(w, Qt.Key_5)  # 4 и 5 больше ничего не делают
    assert w.session.follower.controller.composition.key == "full"
    w.close()


def test_zones_painted_when_following(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    select(w)
    w.follow_panel.follow_btn.click()
    qtbot.waitUntil(lambda: w.session.follower.output is not None, timeout=3000)
    assert w.preview.follow_zones() is not None
    assert not w.preview.grab().isNull()
    w.close()


def test_uncalibrated_warning(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    assert "не откалибрована" in w.follow_panel.calib_label.text()
    w.close()


def test_window_fits_small_screen(qtbot, tmp_path):
    # у пользователя окно ~1640×850: боковая панель не должна растягивать окно по высоте
    w = make(qtbot, tmp_path)
    assert w.minimumSizeHint().height() <= 760
    w.close()


def test_effort_applied_on_any_change(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.follow_panel.effort_slider.setValue(100)  # колесо/клик по полосе — тоже применяется
    assert w.session.follower.controller.effort.gain == 2.0
    w.close()


def test_recalibration_note_shown_only_for_calibrated_camera(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    fp = w.follow_panel
    fp.set_calibrated(False)
    fp.set_calibration_note("Калибровка сделана на другой модели")
    assert "не откалибрована" in fp.calib_label.text()  # без калибровки — прежнее сообщение
    fp.set_calibrated(True)
    assert fp.calib_label.isHidden()  # новая калибровка — прежняя подсказка не нужна
    fp.set_calibration_note("Калибровка сделана на другой модели")
    assert fp.calib_label.text() == "Калибровка сделана на другой модели" and not fp.calib_label.isHidden()
    fp.set_calibration_note(None)
    assert fp.calib_label.isHidden()
    w.close()


def test_zone_drag_moves_point_keeps_following_and_is_per_size(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    select(w)
    w.follow_panel.follow_btn.click()
    qtbot.waitUntil(lambda: w.session.follower.enabled, timeout=2000)
    w.preview.zoneChanged.emit(0.38, 0.3, 1.2)
    w.preview.zoneCommitted.emit()
    ctrl = w.session.follower.controller
    assert ctrl.composition.anchor == (0.38, 0.3)
    assert w.session.follower.enabled  # перенос зоны — не ручное управление
    assert w.preview.zone_scale_of() == 1.2
    w.follow_panel.comp_buttons["wide"].click()  # у «Общего» своя зона — по умолчанию
    assert ctrl.composition.anchor == (0.5, 0.4)
    w.follow_panel.comp_buttons["waist_center"].click()
    assert ctrl.composition.anchor == (0.38, 0.3)
    w.preview.zoneReset.emit()
    assert ctrl.composition.anchor == (0.5, 0.25)
    w.close()
