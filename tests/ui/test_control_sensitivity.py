"""Чувствительность ручного управления и «медленнее на крупном плане» в окне (симулятор, без устройства)."""

import pathlib

import pytest
from PySide6.QtCore import Qt

from ptztracker.camera.speed import velocity_to_code
from ptztracker.config import ConfigStore
from ptztracker.control.manual_curve import shape, tele_scale
from ptztracker.ui.main_window import KEY_PAN_SPEED, MainWindow


class NoPad:
    def open(self):
        return False


def make(qtbot, tmp_path, **cfg):
    store = ConfigStore(tmp_path / "c.json")
    c = store.load()
    for k, v in cfg.items():
        setattr(c, k, v)
    store.save(c)
    w = MainWindow(store, use_sim=True, gamepad_backend=NoPad())
    qtbot.addWidget(w)
    w.show()
    return w


def pts(w, slot=0):
    return [c for c in w.grid.cells[slot].session.sim.received if c.startswith("#PTS")]


def code(pan, tilt=0.0):
    return f"#PTS{velocity_to_code(pan):02d}{velocity_to_code(tilt):02d}"


@pytest.mark.parametrize("sens", [100, 50, 0])
def test_arrow_keys_follow_curve(qtbot, tmp_path, sens):
    w = make(qtbot, tmp_path, stick_sensitivity=sens)
    qtbot.keyPress(w, Qt.Key_Right)
    want = code(shape(KEY_PAN_SPEED, sens))
    qtbot.waitUntil(lambda: bool(pts(w)) and pts(w)[-1] == want, timeout=2000)
    qtbot.keyRelease(w, Qt.Key_Right)
    qtbot.waitUntil(lambda: pts(w)[-1] == "#PTS5050", timeout=2000)
    w.close()


def test_screen_stick_follows_curve_and_slider_changes_it(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.controls.joystick.moved.emit(0.5, 0.0)
    qtbot.waitUntil(lambda: bool(pts(w)) and pts(w)[-1] == code(0.25), timeout=2000)  # по умолчанию — степень 2
    w.controls.joystick.moved.emit(0.0, 0.0)
    w.controls.sens_slider.setValue(100)
    assert w.cfg.stick_sensitivity == 100
    w.controls.joystick.moved.emit(0.5, 0.0)
    qtbot.waitUntil(lambda: pts(w)[-1] == code(0.5), timeout=2000)  # «Резко» — как в 1.0.1
    w.controls.joystick.moved.emit(0.0, 0.0)
    assert ConfigStore(tmp_path / "c.json").load().stick_sensitivity == 100
    w.close()


def _to_tele(qtbot, w, slot=0):
    s = w.grid.cells[slot].session
    s.sim.zoom = 1.0
    qtbot.waitUntil(lambda: (s.zoom_x or 0) > 19, timeout=4000)  # окно само спрашивает #GZ


def test_slow_on_tele_for_manual_only(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.pad_connected()
    w.pad_velocity(1, 1.0, 0.0, 0.0)
    qtbot.waitUntil(lambda: bool(pts(w)) and pts(w)[-1] == code(1.0), timeout=2000)  # широкий угол — как было
    w.pad_velocity(1, 0.0, 0.0, 0.0)
    _to_tele(qtbot, w)
    w.pad_velocity(1, 1.0, 0.0, 0.0)
    want = code(tele_scale(w.grid.cells[0].session.zoom_x))
    assert want < code(1.0)
    qtbot.waitUntil(lambda: pts(w)[-1] == want, timeout=2000)
    w.pad_velocity(1, 0.0, 0.0, 0.0)
    w.controls.slow_tele_check.setChecked(False)
    assert w.cfg.slow_on_tele is False
    w.pad_velocity(1, 1.0, 0.0, 0.0)
    qtbot.waitUntil(lambda: pts(w)[-1] == code(1.0), timeout=2000)
    w.pad_velocity(1, 0.0, 0.0, 0.0)
    w.close()


def test_held_stick_slows_while_zooming_in(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.pad_connected()
    w.pad_velocity(1, 1.0, 0.0, 0.0)
    qtbot.waitUntil(lambda: bool(pts(w)) and pts(w)[-1] == code(1.0), timeout=2000)
    _to_tele(qtbot, w)
    want = code(tele_scale(w.grid.cells[0].session.zoom_x))
    qtbot.waitUntil(lambda: pts(w)[-1] == want, timeout=2000)  # стик держат: такт окна пересчитывает
    w.pad_velocity(1, 0.0, 0.0, 0.0)
    w.close()


def test_tracking_does_not_see_curve_or_tele_slowdown():
    """Регулятор слежения шлёт #PTS мимо смесителя: ни кривая, ни множитель теле его не касаются."""
    root = pathlib.Path(__file__).parents[2] / "src" / "ptztracker" / "control"
    for name in ("follower.py", "framing.py", "camera_model.py", "speed_adapt.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "manual_curve" not in text and "pt_scale" not in text, name
