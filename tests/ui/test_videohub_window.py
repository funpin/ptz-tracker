"""Окно и Videohub: смена выбранной камеры подаёт её вход на выход хаба; хаб не мешает камерам."""

import copy
import dataclasses
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "videohub"))
from fake_hub import FakeHub  # noqa: E402

from ptztracker.config import ConfigStore  # noqa: E402
from ptztracker.ui import main_window as mw  # noqa: E402
from ptztracker.ui.main_window import MainWindow  # noqa: E402

OUT = 5


class NoPad:
    def open(self):
        return False


@pytest.fixture
def hub():
    h = FakeHub()
    yield h
    h.stop()


def make(qtbot, tmp_path, monkeypatch, port, *, inputs=(3, 4, 0, 0), follow=True, output=OUT, ip="127.0.0.1"):
    def cams():
        return [dataclasses.replace(copy.deepcopy(mw.SIM_CAMERA), id=f"sim{i}", name=f"Симулятор {i}",
                                    videohub_input=inputs[i - 1]) for i in range(1, 5)]

    monkeypatch.setattr(mw, "sim_cameras", cams)
    store = ConfigStore(tmp_path / "c.json")
    cfg = store.load()
    cfg.videohub_ip, cfg.videohub_output, cfg.videohub_follow = ip, output, follow
    store.save(cfg)
    w = MainWindow(store, use_sim=True, videohub_port=port, gamepad_backend=NoPad())
    qtbot.addWidget(w)
    w.show()
    return w


def online(w):
    return w.hub is not None and w.hub.snapshot().online


def test_cell_click_routes_selected_camera(qtbot, tmp_path, monkeypatch, hub):
    w = make(qtbot, tmp_path, monkeypatch, hub.port)
    try:
        qtbot.waitUntil(lambda: online(w), timeout=3000)
        w.select_slot(1)
        qtbot.waitUntil(lambda: hub.input_on(OUT) == 4, timeout=2000)
        w.cells[0].selectRequested.emit(0)  # щелчок по клетке 1
        assert w.grid.selected == 0
        qtbot.waitUntil(lambda: hub.input_on(OUT) == 3, timeout=2000)
        qtbot.waitUntil(lambda: "Симулятор 1" in w.hub_pill.full_text, timeout=2000)
        assert w.hub_pill.kind() == "ok"
    finally:
        w.close()
    assert w.hub is None


def test_main_pad_create_routes(qtbot, tmp_path, monkeypatch, hub):
    w = make(qtbot, tmp_path, monkeypatch, hub.port)
    try:
        qtbot.waitUntil(lambda: online(w), timeout=3000)
        w.pad_connected()
        w._pad_id = lambda n: "MAIN" if n == 1 else None
        w.set_main_pad("MAIN")
        w.set_single_view(True)
        w.select_slot(0)
        w.pad_create(1)
        assert w.grid.selected == 1
        qtbot.waitUntil(lambda: hub.input_on(OUT) == 4, timeout=2000)
    finally:
        w.close()


def test_no_route_on_startup(qtbot, tmp_path, monkeypatch, hub):
    w = make(qtbot, tmp_path, monkeypatch, hub.port)
    try:
        qtbot.waitUntil(lambda: online(w), timeout=3000)
        qtbot.wait(300)
        assert hub.routes_received == []
        w.select_slot(0)  # та же камера — ничего
        qtbot.wait(200)
        assert hub.routes_received == []
    finally:
        w.close()


def test_camera_without_input_and_follow_off(qtbot, tmp_path, monkeypatch, hub):
    w = make(qtbot, tmp_path, monkeypatch, hub.port, inputs=(3, 0, 0, 0))
    try:
        qtbot.waitUntil(lambda: online(w), timeout=3000)
        w.select_slot(1)
        qtbot.wait(300)
        assert hub.routes_received == []
        w.cfg.videohub_follow = False
        w.select_slot(0)
        qtbot.wait(300)
        assert hub.routes_received == []
    finally:
        w.close()


def test_locked_output_skipped(qtbot, tmp_path, monkeypatch, hub):
    hub.set_lock(OUT, "L")
    w = make(qtbot, tmp_path, monkeypatch, hub.port)
    try:
        qtbot.waitUntil(lambda: online(w), timeout=3000)
        w.select_slot(1)
        qtbot.wait(300)
        assert hub.routes_received == []
        qtbot.waitUntil(lambda: w.hub_pill.kind() == "warn", timeout=2000)
        assert "занят" in w.hub_pill.full_text
    finally:
        w.close()


def test_unreachable_hub_does_not_block(qtbot, tmp_path, monkeypatch):
    w = make(qtbot, tmp_path, monkeypatch, 9990, ip="10.255.255.1")
    try:
        t0 = time.perf_counter()
        w.select_slot(1)
        w.select_slot(0)
        assert time.perf_counter() - t0 < 0.05 * 2 + 0.2  # выбор клетки сам по себе — десятки мс
        w.emergency_stop()
        cam = w.grid.cells[0].session.sim
        qtbot.waitUntil(lambda: any(c == "#PTS5050" for c in cam.received), timeout=2000)
        qtbot.waitUntil(lambda: w.hub_pill.kind() == "fault", timeout=2000)
        assert w.hub_pill.full_text == "Хаб: нет связи"
        t0 = time.perf_counter()
    finally:
        w.close()
    assert time.perf_counter() - t0 < 2.0


def test_hub_off_no_pill(qtbot, tmp_path, monkeypatch):
    w = make(qtbot, tmp_path, monkeypatch, 9990, ip="")
    try:
        assert w.hub is None
        w.select_slot(1)
        assert w.hub_pill.isHidden()
        assert w._videohub_menu_status() == ("не настроен", "off")
    finally:
        w.close()


def test_dialog_save_applies_and_restarts(qtbot, tmp_path, monkeypatch, hub):
    from ptztracker.ui import window_videohub as wv

    w = make(qtbot, tmp_path, monkeypatch, hub.port, ip="")

    def fake_exec(d):
        d.ip_edit.setText("127.0.0.1")
        d.output_combo.setCurrentIndex(d.output_combo.findData(7))
        d.input_combos[1].setCurrentIndex(d.input_combos[1].findData(9))
        return 1

    monkeypatch.setattr(wv.VideohubDialog, "exec", fake_exec)
    try:
        w._edit_videohub()
        assert (w.cfg.videohub_ip, w.cfg.videohub_output) == ("127.0.0.1", 7)
        assert w.grid.cells[1].cfg.videohub_input == 9
        qtbot.waitUntil(lambda: online(w), timeout=3000)
        qtbot.waitUntil(lambda: not w.hub_pill.isHidden(), timeout=2000)
        w.select_slot(1)
        qtbot.waitUntil(lambda: hub.input_on(7) == 9, timeout=2000)
        assert w._videohub_menu_status() == ("на связи · 127.0.0.1", "ok")
    finally:
        w.close()
