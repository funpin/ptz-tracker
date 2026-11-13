"""Клавиатура и мышь: режим «камера как курсор», удержание клавиш, аварийный стоп."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from ptztracker.camera.session import CameraSession
from ptztracker.control.manual_curve import clamp_sens, shape, tele_scale
from ptztracker.ui.window_common import (
    _ZOOM_IN_KEYS,
    _ZOOM_OUT_KEYS,
    KEY_PAN_FAST,
    KEY_PAN_SPEED,
    KEY_ZOOM_SPEED,
    ZOOM_POLL_S,
)

log = logging.getLogger(__name__)


class InputMixin:
    """Часть MainWindow (ui/main_window.py): состояние окна — в его __init__."""

    def _is_mine(self, obj) -> bool:
        return obj is self or (isinstance(obj, QWidget) and self.isAncestorOf(obj))

    def _note_input(self, device: str) -> None:
        """Чем управляли последним — подсказка внизу видео показывает кнопки этого устройства."""
        if device != self.last_input:
            self.last_input = device
            self._update_hint_context()

    def _mouse_src(self):
        c = self.grid.cells[self.mouse_slot] if self.mouse_slot is not None else None
        return self._source(c, "mousemode") if c is not None else None

    def enter_mouse_mode(self) -> None:
        sel = self.grid.selected
        if sel is None or self.grid.cells[sel] is None or self.mouse_slot is not None:
            return
        self.camera_menu.close_menu()
        self.mouse_slot, self.mouse_wheel = sel, "zoom"
        self.cells[sel].preview.set_mouse_mode(True)
        # плашка режима — на месте обычной подсказки внизу (сверху слева уже плашка цели)
        self.follow_panel.status_label.set_muted(True)
        self.cells[sel].info_muted = True
        self.cells[sel].info_label.hide()
        self.cells[sel].video_frame.add_overlay(self.mouse_hint, "bottom-left")
        self._show_mouse_hint()
        self.mouse_hint.show()

    def exit_mouse_mode(self) -> None:
        """Tab, Esc, другая клетка, ЛКМ: камера сразу останавливается."""
        slot = self.mouse_slot
        if slot is None:
            return
        src = self._mouse_src()
        self.mouse_slot = None
        self._wheel_timer.stop()
        if src is not None:
            src.set_pan_tilt_velocity(0.0, 0.0)
            src.set_zoom_velocity(0.0)
        self.cells[slot].preview.set_mouse_mode(False)
        self.cells[slot].video_frame.remove_overlay(self.mouse_hint)
        self.mouse_hint.hide()
        self.follow_panel.status_label.set_muted(False)
        self.cells[slot].info_muted = False

    def _show_mouse_hint(self) -> None:
        zoom = self.mouse_wheel == "zoom"
        self.mouse_hint.set_hint("Мышь", [("mouse", "W", "зум" if zoom else "ирис"),
                                          ("mouse", "R", "колесо → ирис" if zoom else "колесо → зум"),
                                          ("mouse", "L", "взять человека"), ("key", "Tab", "выйти")])

    def _on_mouse_vector(self, slot: int, x: float, y: float) -> None:
        src = self._mouse_src() if slot == self.mouse_slot else None
        if src is not None:  # как стик: та же кривая чувствительности; ручное действие выключает слежение
            sens = self.cfg.stick_sensitivity
            src.set_pan_tilt_velocity(shape(x, sens), shape(y, sens))

    def _on_mouse_wheel(self, slot: int, d: int) -> None:
        src = self._mouse_src() if slot == self.mouse_slot else None
        if src is None:
            return
        if self.mouse_wheel == "zoom":
            src.set_zoom_velocity(self.MOUSE_WHEEL_ZOOM * d)
            self._wheel_timer.start(self.MOUSE_WHEEL_MS)
        else:
            c = self.controls
            if c.iris_auto.isChecked():
                c.iris_auto.setChecked(False)  # колесо — значит вручную, как крестовина геймпада
            c.iris_slider.setValue(c.iris_slider.value() + self.MOUSE_IRIS_STEP * d)

    def _on_mouse_right(self, slot: int) -> None:
        if slot == self.mouse_slot:
            self.mouse_wheel = "iris" if self.mouse_wheel == "zoom" else "zoom"
            self._show_mouse_hint()

    def _on_mouse_left(self, slot: int) -> None:
        """ЛКМ: взять в слежение человека ближе всех к центру и включить слежение; режим мыши — выключить
        (иначе первое же движение руки выключило бы слежение)."""
        c = self.grid.cells[slot] if slot == self.mouse_slot else None
        a = c.session.analyzer if c is not None else None
        t = a.central_track() if a is not None else None  # при области сцены — среди людей на сцене
        self.exit_mouse_mode()
        if t is None:
            return
        if a.select_at((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2) is not None:
            self._on_follow_requested(True)

    def _on_app_state(self, state) -> None:
        """Окно потеряло фокус — камера режима мыши стоит, сам режим остаётся до Tab или Esc."""
        src = self._mouse_src()
        if src is not None and state != Qt.ApplicationActive:
            src.set_pan_tilt_velocity(0.0, 0.0)
            src.set_zoom_velocity(0.0)

    def emergency_stop(self) -> None:
        """Общий аварийный стоп (Esc и кнопка «Стоп»): снять разрешение агенту, отпустить клавиши,
        выключить слежение и остановить все камеры."""
        # каждый подготовительный шаг — отдельно: сбой одного не отменяет стоп
        steps = (
            ("отмена настройки области сцены", lambda: self.finish_stage_edit("cancel")),
            ("выход из режима мыши", self.exit_mouse_mode),  # Esc выходит из режима мыши и останавливает камеры
            ("снятие разрешений агента", self.agent_panel.revoke_all),
            ("обнуление геймпадов", self._pad_stop_motion),  # снова поедет после возврата стика в центр
            ("отпускание клавиш", self.release_all_held),
        )
        for what, step in steps:
            try:
                step()
            except Exception:
                log.exception("Аварийный стоп: сбой шага «%s»", what)
        self.grid.emergency_stop_all()
        try:
            self.camera_menu.close_menu()  # сначала стоп, потом закрытие
        except Exception:
            log.exception("Аварийный стоп: не закрылось меню камеры")

    def _menu_key(self, key: int, press: bool) -> None:
        m = self.camera_menu
        if key in (Qt.Key_Left, Qt.Key_Right):
            m.nav((key == Qt.Key_Right) - (key == Qt.Key_Left), 0) if press else m.nav(0, 0)
        elif press and key in (Qt.Key_Up, Qt.Key_Down):
            m.nav(0, (key == Qt.Key_Down) - (key == Qt.Key_Up))
        elif press:
            m.accept()

    def _menu_is_open(self) -> bool:
        """Читает поток опроса геймпада (мост создаётся раньше меню)."""
        m = getattr(self, "camera_menu", None)
        return bool(m and m.is_open)

    def _on_menu_changed(self, _open: bool) -> None:
        for i, c in enumerate(self.cells):  # меню закрывает плашки своей клетки, а не лежит поверх них
            if i != self.mouse_slot:  # в режиме мыши цифрами потока распоряжается он
                c.set_covered(_open and c.video_frame is self._menu_host)
        if self.mouse_slot is None:
            self.follow_panel.status_label.set_muted(_open and self._overlay_host is self._menu_host)
        if not _open and self._menu_pad is not None:
            self._menu_pad = None
            sel = self.selected_cell
            if sel is not None:
                self._menu_to(self.cells[self.grid.selected].video_frame)
        for b in self._bridges:
            b.cancel_holds()
        self._sync_pad()

    def _apply_held(self) -> None:
        """Клавиатура — в смеситель выбранной клетки, геймпад — в смеситель его клетки; сумма источников ±1 по оси."""
        h = self._held
        pan = self._key_pan(self._fast)
        kv = (pan * ((Qt.Key_Right in h) - (Qt.Key_Left in h)),
              pan * ((Qt.Key_Up in h) - (Qt.Key_Down in h)),
              KEY_ZOOM_SPEED * (bool(h & _ZOOM_IN_KEYS) - bool(h & _ZOOM_OUT_KEYS)))
        kc = self.selected_cell
        if kc is not None:
            self._send_velocity(self._source(kc, "keyboard"), kv)
        for n, b in list(self.pads.items()):
            self.pad_velocity(n, *b.velocity())

    def _key_pan(self, fast: bool) -> float:
        """Скорость поворота стрелками / WASD — через ту же кривую, что мышь; с Shift — полный наклон."""
        return shape(KEY_PAN_FAST if fast else KEY_PAN_SPEED, self.cfg.stick_sensitivity)

    def _manual_pt_scale(self, s: CameraSession) -> float:
        """Множитель ручного поворота камеры: «медленнее на крупном плане» по её зуму; слежение его не видит."""
        return tele_scale(s.zoom_x, self.cfg.tele_strength) if self.cfg.slow_on_tele else 1.0

    def _on_stick_sensitivity(self, sens: int) -> None:
        self.cfg.stick_sensitivity = clamp_sens(sens)
        self._save()
        if self._held:
            self._apply_held()

    def _on_tele_strength(self, v: int) -> None:
        """Окно «Геймпады»: насколько медленнее на крупном плане (мышь, стрелки и все геймпады)."""
        self.cfg.tele_strength = int(v)
        self._save()
        self._refresh_manual_speed()
        if self._held:
            self._apply_held()

    def _on_slow_tele(self, on: bool) -> None:
        self.cfg.slow_on_tele = bool(on)
        self._save()
        self._refresh_manual_speed()
        if self.gamepads_dialog is not None:
            self.gamepads_dialog.set_slow_on_tele(on)

    def _refresh_manual_speed(self) -> None:
        """Такт окна: зум камер для «медленнее на крупном плане» и пересчёт скорости, пока стик держат."""
        for c in self.grid.cells:
            if c is None:
                continue
            if self.cfg.slow_on_tele:
                c.session.poll_zoom(ZOOM_POLL_S)
            c.mixer.refresh()

    @staticmethod
    def _send_velocity(s, v: tuple[float, float, float]) -> None:
        s.set_pan_tilt_velocity(v[0], v[1])
        s.set_zoom_velocity(v[2])

    def release_all_held(self) -> None:
        """Отпустить клавиши и мышь; скорость, которую держит геймпад, остаётся."""
        if self._held:
            self._held.clear()
            self._apply_held()
        self.controls.release_all()
