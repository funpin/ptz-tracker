"""Геймпады в окне с несколькими камерами: маршрутизация через PadRouter (подставные вызовы, без устройства)."""

from PySide6.QtCore import Qt

from ptztracker.camera.speed import velocity_to_code
from ptztracker.config import ConfigStore
from ptztracker.multicam.pads import PadFeedback
from ptztracker.ui.main_window import MainWindow


class NoPad:
    """Устройства нет: мост молчит, геймпады в тестах подключаются вызовами окна."""

    def open(self):
        return False


def make(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True, gamepad_backend=NoPad())
    qtbot.addWidget(w)
    w.show()
    fb = []
    w.on_pad_feedback = fb.append
    return w, fb


def pts(w, slot):
    return [c for c in w.grid.cells[slot].session.sim.received if c.startswith("#PTS")]


def code(pan, tilt):
    return f"#PTS{velocity_to_code(pan):02d}{velocity_to_code(tilt):02d}"


def test_pad_drives_its_camera_only(qtbot, tmp_path):
    w, _ = make(qtbot, tmp_path)
    assert w.pad_connected() == 1
    w.pad_velocity(1, 0.5, 0.0, 0.0)
    qtbot.waitUntil(lambda: bool(pts(w, 0)) and pts(w, 0)[-1] == code(0.5, 0.0), timeout=2000)
    qtbot.wait(300)
    assert not pts(w, 1)
    w.pad_velocity(1, 0.0, 0.0, 0.0)
    w.close()


def test_two_pads_sum_on_one_camera(qtbot, tmp_path):
    w, _ = make(qtbot, tmp_path)
    w.set_cell_camera(1, None)  # одна камера: оба геймпада на ней
    assert w.pad_connected() == 1 and w.pad_connected() == 2
    assert w.pad_router.pads_on(0) == [1, 2]
    w.pad_velocity(1, 0.5, 0.0, 0.0)
    w.pad_velocity(2, 0.0, 0.5, 0.0)
    qtbot.waitUntil(lambda: bool(pts(w, 0)) and pts(w, 0)[-1] == code(0.5, 0.5), timeout=2000)
    w.pad_velocity(1, 0.0, 0.0, 0.0)  # отпустил один — второй продолжает
    qtbot.waitUntil(lambda: pts(w, 0)[-1] == code(0.0, 0.5), timeout=2000)
    w.pad_velocity(2, 0.0, 0.0, 0.0)
    w.close()


def test_pad_stop_only_its_camera(qtbot, tmp_path):
    w, _ = make(qtbot, tmp_path)
    w.pad_connected()  # геймпад 1 — на камере 0
    w.select_slot(1)
    qtbot.keyPress(w, Qt.Key_Right)  # клавиатура едет камерой 1
    w.pad_velocity(1, 0.0, 0.5, 0.0)
    qtbot.waitUntil(lambda: bool(pts(w, 0)) and bool(pts(w, 1)), timeout=2000)
    w.pad_stop(1)
    qtbot.waitUntil(lambda: pts(w, 0)[-1] == "#PTS5050", timeout=2000)
    qtbot.wait(300)
    assert pts(w, 1)[-1] != "#PTS5050"
    qtbot.keyRelease(w, Qt.Key_Right)
    w.close()


def test_create_moves_and_stops_old(qtbot, tmp_path):
    w, _ = make(qtbot, tmp_path)
    w.pad_connected()
    w.pad_velocity(1, 0.5, 0.0, 0.0)
    qtbot.waitUntil(lambda: bool(pts(w, 0)) and pts(w, 0)[-1] == code(0.5, 0.0), timeout=2000)
    w.pad_create(1)
    assert w.pad_router.slot_of(1) == 1
    qtbot.waitUntil(lambda: pts(w, 0)[-1] == "#PTS5050", timeout=2000)
    w.pad_velocity(1, 0.5, 0.0, 0.0)  # стик всё ещё держат
    qtbot.wait(400)
    assert not pts(w, 1)
    w.pad_velocity(1, 0.0, 0.0, 0.0)
    w.pad_velocity(1, 0.5, 0.0, 0.0)
    qtbot.waitUntil(lambda: bool(pts(w, 1)) and pts(w, 1)[-1] == code(0.5, 0.0), timeout=2000)
    w.pad_velocity(1, 0.0, 0.0, 0.0)
    w.close()


def test_closing_cell_moves_pad(qtbot, tmp_path):
    w, _ = make(qtbot, tmp_path)
    w.pad_connected()
    w.pad_velocity(1, 0.5, 0.0, 0.0)
    qtbot.waitUntil(lambda: bool(pts(w, 0)), timeout=2000)
    old_sim = w.grid.cells[0].session.sim
    w.set_cell_camera(0, None)
    assert w.pad_router.slot_of(1) == 1
    qtbot.waitUntil(lambda: any(c == "#PTS5050" for c in old_sim.received), timeout=2000)
    w.pad_velocity(1, 0.5, 0.0, 0.0)
    qtbot.wait(400)
    assert not pts(w, 1)  # новая камера не поехала, пока стик не вернётся в центр
    w.pad_velocity(1, 0.0, 0.0, 0.0)
    w.pad_velocity(1, 0.5, 0.0, 0.0)
    qtbot.waitUntil(lambda: bool(pts(w, 1)), timeout=2000)
    w.pad_velocity(1, 0.0, 0.0, 0.0)
    w.close()


def test_changing_camera_in_cell_holds_pad(qtbot, tmp_path):
    w, _ = make(qtbot, tmp_path)
    w.pad_connected()
    w.pad_velocity(1, 0.5, 0.0, 0.0)
    qtbot.waitUntil(lambda: bool(pts(w, 0)), timeout=2000)
    w.set_cell_camera(0, "sim1")  # та же камера открыта заново
    qtbot.wait(100)
    w.pad_velocity(1, 0.5, 0.0, 0.0)
    qtbot.wait(400)
    assert not pts(w, 0)
    w.pad_velocity(1, 0.0, 0.0, 0.0)
    w.close()


def test_create_onto_taken_rumbles(qtbot, tmp_path):
    w, fb = make(qtbot, tmp_path)
    w.pad_connected()
    w.pad_connected()  # оба геймпада заняли обе камеры
    assert [w.pad_router.slot_of(1), w.pad_router.slot_of(2)] == [0, 1]
    w.pad_create(1)
    assert PadFeedback(1, "rumble_taken") in fb
    assert w.pad_router.pads_on(1) == [1, 2]
    w.close()


def test_dots_follow_slot(qtbot, tmp_path):
    w, fb = make(qtbot, tmp_path)
    w.pad_connected()
    assert PadFeedback(1, "dots", 1) in fb
    w.pad_create(1)
    assert fb[-1] == PadFeedback(1, "dots", 2)
    w.set_cell_camera(1, None)  # камеру убрали — на оставшуюся
    assert fb[-1] == PadFeedback(1, "dots", 1)
    w.close()


def test_badges_count(qtbot, tmp_path):
    w, _ = make(qtbot, tmp_path)
    w.pad_connected()
    w.pad_connected()
    assert [not b.isHidden() for b in w.cells[0].pad_badges][:2] == [True, False]
    w.pad_create(2)  # второй перешёл на занятую первым камеру 0
    assert [not b.isHidden() for b in w.cells[0].pad_badges][:3] == [True, True, False]
    assert w.cells[1].pad_badges[0].isHidden()
    assert w._service_pads(0) == [1, 2]
    w.pad_disconnected(2)
    assert [not b.isHidden() for b in w.cells[0].pad_badges][:2] == [True, False]
    w.close()
