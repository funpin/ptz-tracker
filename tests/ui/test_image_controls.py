"""Правая панель: без прокрутки при 1640×850; gain и баланс белого; кадр одной строкой."""

import time

from PySide6.QtWidgets import QApplication

from ptztracker.camera.session import ImageState
from ptztracker.config import ConfigStore
from ptztracker.ui.main_window import MainWindow
from ptztracker.ui.theme import apply_dark_theme


def make(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path / "c.json"), use_sim=True)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.session.image.gain is not None, timeout=4000)
    return w


def _heights(w) -> str:
    """Высоты строк правой панели — чтобы по логу CI понять, что не поместилось."""
    out = [f"окно {w.width()}x{w.height()}, область {w.side_scroll.viewport().height()},"
           f" содержимое {w.side_scroll.widget().sizeHint().height()}"]
    for panel in (w.follow_panel, w.controls):
        lay = panel.layout()
        for i in range(lay.count()):
            it = lay.itemAt(i)
            wd = it.widget()
            name = type(wd).__name__ if wd else "layout"
            text = getattr(wd, "text", lambda: "")() if wd and hasattr(wd, "text") else ""
            out.append(f"{type(panel).__name__}[{i}] {name} {text[:20]!r} h={it.sizeHint().height()}")
    return "\n".join(out)


def recv(w, prefix):
    return [c for c in w.session.sim.received if c.startswith(prefix)]


def test_side_panel_fits_without_scroll(qtbot, tmp_path):
    app = QApplication.instance()
    old_style, old_pal = app.styleSheet(), app.palette()
    apply_dark_theme(app)
    w = make(qtbot, tmp_path)
    try:
        w.resize(1640, 850)
        qtbot.waitExposed(w)
        qtbot.wait(1200)
        assert w.side_scroll.verticalScrollBar().maximum() == 0, _heights(w)  # на Windows видно, что выросло
        for wd in (w.controls.gain_slider, w.controls.wb_combo, w.controls.preset_buttons[-1],
                   w.controls.near_btn, w.controls.iris_slider):
            # по высоте не обрезано (панель не прокручивается); по ширине допускаем 1–2 px округления
            r = wd.visibleRegion().boundingRect()
            assert wd.isVisible() and r.height() == wd.height() and r.width() >= wd.width() - 2
    finally:
        w.close()
        app.setStyleSheet(old_style)
        app.setPalette(old_pal)


def test_compositions_in_one_row(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    ys = {b.geometry().y() for b in w.follow_panel.comp_buttons.values()}
    assert len(ys) == 1
    assert "Пояс · слева" in w.follow_panel.comp_buttons["waist_left"].toolTip()
    w.close()


def test_gain_slider_sends_valid_code(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    c = w.controls
    c.gain_auto.setChecked(False)
    c.gain_slider.setValue(1)
    qtbot.waitUntil(lambda: "OGU:0B" in w.session.sim.received, timeout=1500)
    assert "3 dB" in c.gain_value.text()
    w.close()


def test_gain_auto(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.controls.gain_auto.setChecked(True)
    qtbot.waitUntil(lambda: "OGU:80" in w.session.sim.received, timeout=1500)
    assert not w.controls.gain_slider.isEnabled()
    w.close()


def test_color_temp_only_in_var(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    c = w.controls
    c.wb_combo.setCurrentIndex(c.wb_combo.findData(5))
    qtbot.waitUntil(lambda: "OAW:5" in w.session.sim.received, timeout=1500)
    assert not c.ctemp_slider.isEnabled()
    c.wb_combo.setCurrentIndex(c.wb_combo.findData(9))
    assert c.ctemp_slider.isEnabled()
    c.ctemp_slider.setValue(0x20)
    qtbot.waitUntil(lambda: "OSD:B1:020" in w.session.sim.received, timeout=1500)
    assert "K" in c.ctemp_value.text()
    w.close()


def test_sync_image_does_not_send(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    time.sleep(0.3)
    before = len(recv(w, "OGU:")) + len(recv(w, "OAW:")) + len(recv(w, "OSD:"))
    w._status_timer.stop()  # иначе таймер сразу покажет настоящие значения камеры
    w.controls.sync_image(ImageState(gain=0x14, wb=9, color_temp=0x30))
    assert w.controls.gain_slider.value() == 4 and not w.controls.gain_auto.isChecked()
    assert w.controls.wb_combo.currentData() == 9 and w.controls.ctemp_slider.value() == 0x30
    qtbot.wait(400)
    assert len(recv(w, "OGU:")) + len(recv(w, "OAW:")) + len(recv(w, "OSD:")) == before
    w.close()


def test_status_tick_syncs_image_from_camera(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    w.session.sim.gain_code = 0x1A  # изменили с пульта
    w.session.refresh_image()
    qtbot.waitUntil(lambda: w.controls.gain_slider.value() == 6, timeout=3000)
    w.close()


def test_image_controls_do_not_disable_follow(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    qtbot.waitUntil(lambda: bool(w.session.analyzer.latest() and w.session.analyzer.latest().tracks), timeout=8000)
    tr = w.session.analyzer.latest().tracks[0]
    w.session.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2)
    w.track_btn.click()
    qtbot.waitUntil(lambda: w.session.follower.enabled, timeout=2000)
    w.controls.gain_auto.setChecked(False)
    w.controls.gain_slider.setValue(2)
    qtbot.wait(300)
    assert w.session.follower.enabled
    w.close()
