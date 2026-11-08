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


# --- несколько физических геймпадов через общий хаб (подставной, без SDL) ---

import threading  # noqa: E402

from ptztracker.gamepad.device import RawPad  # noqa: E402


class FakeBackend:
    def __init__(self):
        self.pad = None  # None — не подключён
        self.calls = []
        self._lock = threading.Lock()

    def open(self):
        return True

    def poll(self):
        return self.pad

    def info(self):
        return {"name": "DualSense", "type": "ps5", "has_led": True, "has_rumble": True, "power": None}

    def set_led(self, rgb):
        pass

    def set_player_index(self, n):
        with self._lock:
            self.calls.append(("player", n))

    def rumble(self, low, high, ms):
        pass

    def battery(self):
        return (None, None)

    def close(self):
        pass


class FakeHub:
    def __init__(self):
        self.backends = [FakeBackend() for _ in range(4)]
        self.closed = False
        self.open_threads = []

    def open(self):
        import threading
        self.open_threads.append(threading.current_thread().name)
        return True

    def backend(self, i):
        return self.backends[i]

    def pump(self):
        pass

    def close(self):
        self.closed = True


def make_hub(qtbot, tmp_path):
    hub = FakeHub()
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True, gamepad_hub=hub)
    qtbot.addWidget(w)
    w.show()
    fb = []
    w.on_pad_feedback = fb.append
    return w, hub, fb


def test_two_pads_get_numbers_1_2(qtbot, tmp_path):
    w, hub, _ = make_hub(qtbot, tmp_path)
    hub.backends[0].pad = RawPad()
    hub.backends[1].pad = RawPad()
    qtbot.waitUntil(lambda: len(w.pads) == 2, timeout=3000)
    assert sorted(w.pads) == [1, 2]
    assert w.pads[1] is w.pad and w.pads[2] is not w.pad
    assert [w.pad_router.slot_of(1), w.pad_router.slot_of(2)] == [0, 1]
    w.close()
    assert hub.closed


def test_second_pad_drives_second_cell(qtbot, tmp_path):
    w, hub, _ = make_hub(qtbot, tmp_path)
    hub.backends[0].pad = RawPad()
    hub.backends[1].pad = RawPad()
    qtbot.waitUntil(lambda: len(w.pads) == 2, timeout=3000)
    qtbot.waitUntil(lambda: w.grid.cells[1].session.online, timeout=6000)  # иначе камера ответит er2
    qtbot.wait(300)  # автомат нового геймпада заперт, пока стик не замечен в центре
    hub.backends[1].pad = RawPad(axes={"leftx": 1.0})
    qtbot.waitUntil(lambda: bool(pts(w, 1)) and pts(w, 1)[-1] != "#PTS5050", timeout=6000)
    assert not pts(w, 0)
    hub.backends[1].pad = RawPad()
    w.close()


def test_unplug_one_keeps_other(qtbot, tmp_path):
    w, hub, _ = make_hub(qtbot, tmp_path)
    hub.backends[0].pad = RawPad()
    hub.backends[1].pad = RawPad()
    qtbot.waitUntil(lambda: len(w.pads) == 2, timeout=3000)
    qtbot.wait(300)  # автомат нового геймпада заперт, пока стик не замечен в центре
    hub.backends[0].pad = None
    qtbot.waitUntil(lambda: sorted(w.pads) == [2], timeout=3000)
    assert w.pad_router.slot_of(2) == 1 and w.pads[2].connected
    hub.backends[1].pad = RawPad(axes={"leftx": 1.0})
    qtbot.waitUntil(lambda: bool(pts(w, 1)) and pts(w, 1)[-1] != "#PTS5050", timeout=6000)
    hub.backends[1].pad = RawPad()
    w.close()


def test_dots_per_pad(qtbot, tmp_path):
    w, hub, fb = make_hub(qtbot, tmp_path)
    hub.backends[0].pad = RawPad()
    hub.backends[1].pad = RawPad()
    qtbot.waitUntil(lambda: len(w.pads) == 2, timeout=3000)
    assert PadFeedback(1, "dots", 1) in fb and PadFeedback(2, "dots", 2) in fb
    qtbot.waitUntil(lambda: ("player", 1) in hub.backends[0].calls and ("player", 2) in hub.backends[1].calls,
                    timeout=3000)
    w.close()


def test_intents_from_unnumbered_bridge_never_reach_pad_1(qtbot, tmp_path):
    """Мост без номера (5-й геймпад или хвост после отключения) не действует от имени геймпада 1."""
    from ptztracker.gamepad.intents import Disconnected, PanTilt

    w, hub, _ = make_hub(qtbot, tmp_path)
    hub.backends[0].pad = RawPad()
    qtbot.waitUntil(lambda: len(w.pads) == 1, timeout=3000)
    stray = w._bridges[1]
    assert stray not in w._pad_nums
    stray._vel = (1.0, 0.0, 0.0)
    w._on_pad_intents([PanTilt(1.0, 0.0)], None, stray)
    w._on_pad_intents([Disconnected()], None, stray)
    qtbot.wait(300)
    assert sorted(w.pads) == [1]  # геймпад 1 не отключён
    assert not [c for c in pts(w, 0) if c != "#PTS5050"]  # его камера не поехала
    w.close()


def test_unplugging_pad_closes_its_menu(qtbot, tmp_path):
    w, _ = make(qtbot, tmp_path)
    pad = w.pad_connected()
    w.pad_create(pad)
    w._menu_pad = pad
    w.camera_menu.open()
    w.pad_disconnected(pad)
    assert not w.camera_menu.is_open  # меню геймпада не остаётся висеть над чужой камерой
    w.close()


def test_window_starts_sdl_off_the_qt_thread(qtbot, tmp_path):
    """На Mac запуск SDL в потоке Qt ронял программу через несколько минут с DualSense."""
    w, hub, _ = make_hub(qtbot, tmp_path)
    qtbot.waitUntil(lambda: bool(hub.open_threads), timeout=2000)
    assert hub.open_threads == ["gamepads"]


def test_speed_gear_shared_by_pads_with_same_id(qtbot, tmp_path):
    """Одинаковые геймпады без серийного номера делят настройку: ступень меняется у обоих сразу."""
    w, hub, _ = make_hub(qtbot, tmp_path)
    for fb in hub.backends[:2]:
        fb.info = lambda: {"name": "DualSense", "type": "ps5", "id": "GUID:DualSense"}
        fb.pad = RawPad()
    qtbot.waitUntil(lambda: len(w.pads) == 2, timeout=3000)
    w._pad_speed_step(w.pads[1], -1)
    assert w.pads[1].speed_limit == w.pads[2].speed_limit == 21
    w.close()


def test_ps_button_toggles_help(qtbot, tmp_path):
    from ptztracker.gamepad.intents import Help

    w, _ = make(qtbot, tmp_path)
    assert w.pad_connected() == 1
    w._on_pad_intents([Help()], 1)
    qtbot.waitUntil(lambda: w.hotkeys_dialog is not None and w.hotkeys_dialog.isVisible(), timeout=2000)
    w._on_pad_intents([Help()], 1)  # второе нажатие PS — закрыть
    qtbot.waitUntil(lambda: not w.hotkeys_dialog.isVisible(), timeout=2000)
    w.close()


def test_main_pad_create_switches_selected_camera_in_single_view(qtbot, tmp_path):
    w, _ = make(qtbot, tmp_path)
    w.pad_connected()
    w._pad_id = lambda n: "MAIN" if n == 1 else None
    w.set_single_view(True)
    w.select_slot(0)
    w.pad_create(1)  # не главный — выбор в программе не меняется
    assert w.grid.selected == 0
    w.pad_create(1)  # вернулся на клетку 1
    w.set_main_pad("MAIN")
    w.pad_create(1)
    assert w.pad_router.slot_of(1) == 1 and w.grid.selected == 1  # камера, пресеты и правая колонка — его
    w.set_single_view(False)
    w.pad_create(1)  # в сетке 2×2 выбор не трогаем
    assert w.grid.selected == 1
    assert w.cfg.main_pad == "MAIN"
    w.close()


def test_main_pad_switch_keeps_analysis_and_moves_preset_row(qtbot, tmp_path):
    w, _ = make(qtbot, tmp_path)
    w.cfg.max_analyzed = 1
    w.pad_connected()
    w._pad_id = lambda n: "MAIN"
    w.set_main_pad("MAIN")
    w.set_single_view(True)
    w.select_slot(0)  # оператор выбрал камеру 1 — анализ на ней
    w._reallocate_analysis()
    assert w.grid.cells[0].session.analyzer is not None and w.grid.cells[1].session.analyzer is None
    w.pad_create(1)  # главный геймпад — на камеру 2
    assert w.grid.selected == 1
    assert w.grid.cells[0].session.analyzer is not None and w.grid.cells[1].session.analyzer is None  # анализ остался
    assert w._preset_rows() == [1]  # пресеты снизу — камеры 2
    w.select_slot(0)  # выбор мышью — снова оператор
    assert w._preset_rows() == [0]
    w.close()


def test_selection_never_moves_analysis_click_on_chip_does(qtbot, tmp_path):
    w, _ = make(qtbot, tmp_path)
    w.cfg.max_analyzed = 1
    w._reallocate_analysis()
    has = lambda i: w.grid.cells[i].session.analyzer is not None  # noqa: E731
    assert has(0) and not has(1)
    w.select_slot(1)  # выбор камеры — анализ остаётся
    assert has(0) and not has(1)
    w.cells[1].analysisRequested.emit(1)  # клик по «БЕЗ АНАЛИЗА» у камеры 2
    assert has(1) and not has(0)
    w.select_slot(0)
    assert has(1) and not has(0)
    assert w.cfg.analysis_ids == [w.grid.cells[1].cfg.id]  # запомнено и после перезапуска
    w.close()


def test_hint_follows_last_used_device(qtbot, tmp_path):
    """Пользователь 2026-11-06: подсказка — для того, чем управляли последним, а не по привязке геймпада."""
    from PySide6.QtCore import QPoint

    from ptztracker.gamepad.intents import PanTilt

    w, _ = make(qtbot, tmp_path)
    w.pad_connected()  # геймпад 1 — на камере 1
    w.select_slot(0)
    assert w.last_input == "kb" and not w.follow_panel._ctx["pad"]  # после запуска — клавиатура и мышь
    w._on_pad_intents([PanTilt(0.0, 0.0)], 1)  # стик в центре — не действие
    assert w.last_input == "kb"
    w._on_pad_intents([PanTilt(0.6, 0.0)], 1)
    assert w.last_input == "pad" and w.follow_panel._ctx["pad"]
    w.select_slot(1)  # геймпад этой камерой не управляет
    assert not w.follow_panel._ctx["pad"]
    w.select_slot(0)
    assert w.follow_panel._ctx["pad"]
    qtbot.keyPress(w, Qt.Key_Left)
    qtbot.keyRelease(w, Qt.Key_Left)
    assert w.last_input == "kb" and not w.follow_panel._ctx["pad"]
    w._on_pad_intents([PanTilt(0.6, 0.0)], 1)
    w._on_pad_intents([PanTilt(0.0, 0.0)], 1)
    qtbot.mouseClick(w.cells[0].preview, Qt.LeftButton, pos=QPoint(5, 5))
    assert w.last_input == "kb"
    w.close()


def test_pad_stop_reaches_camera_even_if_preparation_fails(qtbot, tmp_path):
    w, _ = make(qtbot, tmp_path)
    w.pad_connected()
    cell = w.grid.cells[0]

    def boom(*a, **k):
        raise RuntimeError("boom")

    cell.gate.revoke = boom
    cell.mixer.clear_all = boom
    w.pad_stop(1)
    qtbot.waitUntil(lambda: bool(pts(w, 0)) and pts(w, 0)[-1] == "#PTS5050", timeout=2000)
    w.close()


def test_close_window_stops_cameras_even_if_preparation_fails(qtbot, tmp_path):
    w, _ = make(qtbot, tmp_path)
    sim = w.grid.cells[0].session.sim

    def boom(*a, **k):
        raise RuntimeError("boom")

    w._pad_stop_motion = boom
    w.close()
    assert all(c is None for c in w.grid.cells)
    assert "#PTS5050" in sim.received
