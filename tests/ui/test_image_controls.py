"""Правая панель: без прокрутки при 1640×850; gain и баланс белого; кадр одной строкой."""

import sys
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
           f" содержимое {w.side_scroll.widget().sizeHint().height()},"
           f" мин. ширина содержимого {w.side_scroll.widget().minimumSizeHint().width()}"]
    for panel in (w.follow_panel, w.controls):
        lay = panel.layout()
        for i in range(lay.count()):
            it = lay.itemAt(i)
            wd = it.widget()
            name = type(wd).__name__ if wd else "layout"
            text = getattr(wd, "text", lambda: "")() if wd and hasattr(wd, "text") else ""
            out.append(f"{type(panel).__name__}[{i}] {name} {text[:20]!r} h={it.sizeHint().height()}"
                       f" мин. ширина {it.minimumSize().width()}")
            if not wd and it.layout():
                sub = it.layout()
                out.append("    " + ", ".join(
                    f"{type(sub.itemAt(k).widget()).__name__ if sub.itemAt(k).widget() else 'layout'}"
                    f"={sub.itemAt(k).minimumSize().width()}" for k in range(sub.count())))
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
        if sys.platform == "darwin":  # шрифт Windows выше: на Mac держим запас по высоте под него
            slack = w.side_scroll.viewport().height() - w.side_scroll.widget().sizeHint().height()
            assert slack >= 40, _heights(w)
        # ни одна строка не шире панели (иначе правый край обрезается — на Windows так было с заголовками)
        assert w.side_scroll.widget().minimumSizeHint().width() <= w.side_scroll.viewport().width(), _heights(w)
        for wd in (w.controls.gain_slider, w.controls.wb_combo, w.controls.far_btn,
                   w.controls.near_btn, w.controls.iris_slider):
            # целиком внутри видимой области панели; по ширине допускаем 1–2 px округления.
            # visibleRegion() тут не годится: на сервере сборки Windows (экран 800×800, окно больше экрана)
            # он бывает пустым у виджета в границах панели
            vp = w.side_scroll.viewport()
            top_left = wd.mapTo(vp, wd.rect().topLeft())
            r = wd.rect().translated(top_left)
            where = f"{type(wd).__name__} {getattr(wd, 'text', lambda: '')()!r} {r} в области {vp.rect()}\n{_heights(w)}"
            assert wd.isVisible() and vp.rect().adjusted(0, 0, 2, 0).contains(r), where
    finally:
        w.close()
        app.setStyleSheet(old_style)
        app.setPalette(old_pal)


def test_compositions_in_one_row(qtbot, tmp_path):
    w = make(qtbot, tmp_path)
    ys = {b.geometry().y() for b in w.follow_panel.comp_buttons.values()}
    assert len(ys) == 1 and len(w.follow_panel.comp_buttons) == 3
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


def test_dpad_from_auto_unticks_auto_boxes(qtbot, tmp_path):
    """Жалоба 2026-11-05: «ирис с крестовины не снимает авто». Камера авто снимала, но флажок «Авто»
    в панели и в меню камеры оставался: окно не читало режим ириса из ответов камеры."""
    from ptztracker.camera import protocol as P
    from ptztracker.gamepad.intents import GainStep, IrisStep

    w = make(qtbot, tmp_path)
    c, s = w.controls, w.session
    qtbot.waitUntil(lambda: s.image.iris is not None, timeout=3000)
    s.set_gain(P.GAIN_AUTO)
    qtbot.waitUntil(lambda: c.gain_auto.isChecked() and s.sim.gain_code == P.GAIN_AUTO, timeout=3000)
    assert c.iris_auto.isChecked() and s.sim.iris_auto
    pad = w.pad_connected()
    w._on_pad_intents([IrisStep(+1)], pad)
    qtbot.waitUntil(lambda: not s.sim.iris_auto, timeout=2000)
    qtbot.waitUntil(lambda: not c.iris_auto.isChecked() and c.iris_slider.isEnabled(), timeout=2000)
    assert abs(c.iris_slider.value() - round(100 * (s.image.iris - P.LENS_MIN) / (P.LENS_MAX - P.LENS_MIN))) <= 1
    n = len(s.sim.received)
    w._on_pad_intents([GainStep(+1)], pad)
    qtbot.waitUntil(lambda: s.sim.gain_code != P.GAIN_AUTO, timeout=2000)
    qtbot.waitUntil(lambda: not c.gain_auto.isChecked(), timeout=2000)
    assert "dB" in c.gain_value.text()
    # снятие «Авто» в панели не отправляет камере ничего лишнего (#D31 или второй OGU)
    qtbot.wait(600)
    sent = list(s.sim.received)[n:]
    assert "#D31" not in sent and sum(x.startswith("OGU:") and x != "OGU:" for x in sent) <= 1, sent
    w.close()


def test_stick_grows_on_tall_screen(qtbot, tmp_path):
    """MacBook Pro 16 во весь экран (~1728×1080): пустое место в правой колонке отдаётся стику."""
    w = make(qtbot, tmp_path)
    w.resize(1640, 850)
    qtbot.wait(300)
    assert w.controls.joystick.width() == 176
    w.resize(1728, 1080)
    qtbot.waitUntil(lambda: w.controls.joystick.width() >= 230, timeout=2000)
    assert w.side_scroll.verticalScrollBar().maximum() == 0
    w.resize(1640, 850)
    qtbot.waitUntil(lambda: w.controls.joystick.width() == 176, timeout=2000)
    w.close()


def test_windows_full_hd_maximized_with_presets(qtbot, tmp_path, monkeypatch):
    """Windows 1920×1080, масштаб 100%, окно развёрнуто (минус «Пуск» и заголовок ≈ 1920×1001):
    правая колонка без прокрутки с запасом, пресеты делят место с камерами, видео остаётся крупным."""
    w = make(qtbot, tmp_path)
    monkeypatch.setattr(w, "isMaximized", lambda: True)
    w.resize(1920, 1001)
    w.presets_btn.click()
    qtbot.wait(500)
    w.resize(1920, 1001)
    qtbot.wait(300)
    assert w.height() == 1001  # развёрнутое окно не растёт
    assert w.side_scroll.verticalScrollBar().maximum() == 0, _heights(w)
    assert w.side_scroll.viewport().height() - w.side_scroll.widget().sizeHint().height() >= 40, _heights(w)
    assert w.cells[0].video_frame.height() >= 300
    w.close()


def test_wb_picker_same_size_as_one_shot_button(qtbot, tmp_path):
    app = QApplication.instance()
    old_style, old_pal = app.styleSheet(), app.palette()
    apply_dark_theme(app)
    w = make(qtbot, tmp_path)
    try:
        w.resize(1640, 850)
        qtbot.wait(300)
        c = w.controls
        assert abs(c.wb_combo.height() - c.push_af.height()) <= 1
        assert c.wb_combo.width() <= 96  # чип по тексту, а не во всю ширину
    finally:
        w.close()
        app.setStyleSheet(old_style)
        app.setPalette(old_pal)


def test_slider_handles_not_clipped_in_window(qtbot, tmp_path):
    """В окне ползунок был 15 px — ручку 17 px срезало сверху и снизу (замечание пользователя 2026-11-05)."""
    app = QApplication.instance()
    old_style, old_pal = app.styleSheet(), app.palette()
    apply_dark_theme(app)
    w = make(qtbot, tmp_path)
    try:
        w.resize(1640, 850)
        qtbot.wait(300)
        c = w.controls
        for sl in (c.gain_slider, c.iris_slider, c.sens_slider, c.ctemp_slider, c.focus_slider,
                   w.follow_panel.size_slider):
            assert sl.height() >= 20, (type(sl).__name__, sl.height())
        assert w.side_scroll.verticalScrollBar().maximum() == 0, _heights(w)
    finally:
        w.close()
        app.setStyleSheet(old_style)
        app.setPalette(old_pal)


def test_sensitivity_label_not_cut_when_room(qtbot, tmp_path):
    """Подпись сжимается только при нехватке места (Windows); в окне 1640×850 на Mac — целиком."""
    from PySide6.QtWidgets import QLabel

    app = QApplication.instance()
    old_style, old_pal = app.styleSheet(), app.palette()
    apply_dark_theme(app)
    w = make(qtbot, tmp_path)
    try:
        w.resize(1640, 850)
        qtbot.wait(300)
        lab = next(x for x in w.controls.findChildren(QLabel) if x.text() == "Чувствительность")
        if sys.platform == "darwin":
            assert lab.width() >= lab.sizeHint().width()
    finally:
        w.close()
        app.setStyleSheet(old_style)
        app.setPalette(old_pal)
