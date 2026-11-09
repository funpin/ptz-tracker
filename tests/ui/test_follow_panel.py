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
    assert abs(w.session.follower.controller.effort.gain - 1.2 - (1.7 - 1.2) * 0.4) < 1e-6
    w.close()
    cam = store.load().cameras[0]
    assert cam.composition == "wide" and abs(cam.effort - 0.2) < 1e-9


def raw_key(w, key: int, text: str, release: bool = False):
    """Клавиша русской раскладки: qtbot принимает только Qt.Key, а Qt отдаёт код буквы (0x0424 — «Ф»)."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    QApplication.sendEvent(w, QKeyEvent(QEvent.KeyRelease if release else QEvent.KeyPress, key, Qt.NoModifier, text))


def test_zxc_keys_select_composition(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.keyClick(w, Qt.Key_X)  # Z пояс, X рост, C общий (план шага Б, часть 3)
    assert w.follow_panel.comp_buttons["full"].isChecked()
    assert w.session.follower.controller.composition.key == "full"
    qtbot.keyClick(w, Qt.Key_C)
    assert w.session.follower.controller.composition.key == "wide"
    raw_key(w, 0x042F, "я")  # «Я» — та же клавиша Z в русской раскладке
    raw_key(w, 0x042F, "я", release=True)
    assert w.session.follower.controller.composition.key == "waist_center"
    w.close()


def test_number_keys_recall_presets(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    sim = w.grid.cells[w.grid.selected].session.sim
    qtbot.keyClick(w, Qt.Key_3)
    qtbot.waitUntil(lambda: "#R02" in sim.received, timeout=2000)  # клавиша 3 — пресет 3 (#R02)
    qtbot.keyClick(w, Qt.Key_9)
    qtbot.waitUntil(lambda: "#R08" in sim.received, timeout=2000)
    w.close()


def test_shift_number_stores_preset_after_confirm(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.Yes)
    w = make(qtbot, tmp_path)
    sim = w.grid.cells[w.grid.selected].session.sim
    qtbot.keyClick(w, Qt.Key_Exclam, Qt.ShiftModifier)  # Shift+1 на английской раскладке даёт «!»
    qtbot.waitUntil(lambda: "#M00" in sim.received, timeout=2000)
    assert "пресет 1" in asked[-1]
    w.close()


def test_wasd_pan_like_arrows_and_shift_is_faster(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.keyPress(w, Qt.Key_D)
    assert w._held == {Qt.Key_Right}
    qtbot.keyRelease(w, Qt.Key_D)
    raw_key(w, 0x0424, "ф")  # «Ф» — клавиша A в русской раскладке: влево
    assert w._held == {Qt.Key_Left}
    raw_key(w, 0x0424, "ф", release=True)
    assert not w._held
    assert w._key_pan(fast=True) > w._key_pan(fast=False)
    w.close()


def test_logical_key_maps_layouts():
    from ptztracker.ui.keys import digit_of, logical_key

    assert logical_key(0x0426) == Qt.Key_W and logical_key(0x0421) == Qt.Key_C  # Ц, С
    assert logical_key(0x042C) == Qt.Key_M  # «Ь» — клавиша M (быстрые настройки) в русской раскладке
    assert digit_of(Qt.Key_7, 0) == 7 and digit_of(Qt.Key_Ampersand, 0) == 7  # Shift+7 на английской
    assert digit_of(0x2116, 0) == 3  # «№» — Shift+3 на русской
    assert digit_of(Qt.Key_0, 0) is None and digit_of(Qt.Key_A, 0) is None
