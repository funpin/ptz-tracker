"""Что показывает окно: таймер состояния, служебные клетки, эфир ATEM, обновления, подгонка размеров."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QMenu, QMessageBox, QToolButton, QWidget

from ptztracker import __version__
from ptztracker.camera.session import REFUSED_SHOW_S, CameraSession
from ptztracker.config import GRID_SLOTS, CameraConfig
from ptztracker.control.camera_model import DEFAULT_TABLE_ID
from ptztracker.multicam.timeline import StateTimeline
from ptztracker.tally import atem_input_of
from ptztracker.tally.atem import AtemTally
from ptztracker.ui.atem_dialog import AtemDialog
from ptztracker.ui.camera_cell import CameraCell
from ptztracker.ui.hotkeys_dialog import HotkeysDialog
from ptztracker.ui.manual_controls import STICK_MIN
from ptztracker.ui.service_cell import KINDS, TITLES, ServiceRow
from ptztracker.ui.state import analysis_pill, atem_pill, power_view, recalibration_hint, video_pill
from ptztracker.ui.theme import FS_CAPTION, SP, C
from ptztracker.ui.widgets import StatusPill
from ptztracker.ui.window_common import (
    CYCLE_KINDS,
    CYCLE_S,
    QWIDGETSIZE_MAX,
    SIDE_FIT_RESERVE,
    SPEED_SCALE_SAVE_DIFF,
    SPEED_SCALE_SAVE_S,
)
from ptztracker.updates import CHECK_INTERVAL_S, RELEASE_PAGE, UpdateInfo, is_newer
from ptztracker.video.rtsp import STREAM_CHOICES, strip_credentials

log = logging.getLogger(__name__)


MESSAGE_MIN_W = 200  # столько нижней строки оставить сообщениям


class StatusMixin:
    """Часть MainWindow (ui/main_window.py): состояние окна — в его __init__."""

    def _start_update_check(self) -> None:
        """Одна проверка в фоне; не чаще раза в сутки и только если не отключена в меню."""
        if self._checker is not None or self._checker_factory is None or not self.cfg.check_updates:
            return
        if is_newer(self.cfg.latest_version, __version__):  # найдено прошлой проверкой — показать сразу, без сети
            self._show_update(UpdateInfo(self.cfg.latest_version, RELEASE_PAGE.format(version=self.cfg.latest_version)))
        if time.time() - self.cfg.last_update_check < CHECK_INTERVAL_S:
            return
        self._checker = self._checker_factory(__version__)
        self._checker.start()

    def _poll_update_check(self) -> None:
        """Из таймера состояния: читаем снимок, сеть не ждём."""
        c = self._checker
        if c is None or not c.done:
            return
        self._checker = None
        info = c.result
        if c.ok:
            self.cfg.last_update_check = time.time()
            self.cfg.latest_version = info.version if info is not None else ""
            self._save()
        if info is not None:
            self._show_update(info)
        elif c.ok:
            self.update_action.setVisible(False)
            self.main_menu_btn.set("", "off", "Меню")

    def _show_update(self, info: UpdateInfo) -> None:
        self._update_info = info
        self.update_action.setText(f"Доступна версия {info.version} — открыть страницу")
        self.update_action.setVisible(True)
        self.main_menu_btn.set("", "accent", f"Меню · доступна версия {info.version}")

    def _open_release_page(self) -> None:
        if self._update_info is not None:
            QDesktopServices.openUrl(QUrl(self._update_info.html_url))

    def _on_check_updates_toggled(self, on: bool) -> None:
        self.cfg.check_updates = on
        self._save()

    def save_config(self) -> None:
        self._save()

    def _restart_atem(self) -> None:
        """Остановить старый клиент ATEM и создать новый из cfg.atem_ip (пусто — Tally выключен)."""
        if self.atem:
            self.atem.stop()
            self.atem = None
        if self.cfg.atem_ip:
            self.atem = AtemTally(self.cfg.atem_ip, port=self._atem_port)
            self.atem.start()
        self._tally_shown = None
        self._sync_tally()

    def _atem_menu_status(self) -> tuple[str, str]:
        """Справа от «ATEM…» в ☰: не настроен / на связи · адрес / нет связи."""
        if not self.cfg.atem_ip:
            return ("не настроен", "off")
        snap = self.atem.snapshot() if self.atem else None
        return (f"на связи · {self.cfg.atem_ip}", "ok") if snap and snap.online else ("нет связи", "fault")

    def _atem_cameras(self) -> list[tuple[int, str, int, str | None]]:
        """Для окна ATEM: клетка, имя камеры, её вход, эфир сейчас."""
        return [(i + 1, c.cfg.name, atem_input_of(c.cfg), self._tally_per[i][1])
                for i, c in enumerate(self.grid.cells) if c is not None]

    def _pads_menu_status(self) -> tuple[str, str]:
        n = sum(1 for b in self.pads.values() if b.connected)
        return (str(n), "neutral") if n else ("нет", "off")

    def _agent_menu_status(self) -> tuple[str, str]:
        return ("разрешён", "ok") if self.agent_badge.isVisibleTo(self) else ("выключен", "off")

    def _edit_atem(self) -> None:
        d = AtemDialog(self.cfg.atem_ip, self, status=self._atem_menu_status, cameras=self._atem_cameras)
        if not d.exec():
            return
        self.cfg.atem_ip = d.ip()
        self._save()
        self._restart_atem()

    def _sync_tally(self) -> None:
        """Tally из снимка клиента (сеть не ждём): рамка каждой клетки и чип выбранной, обновляются только при изменении."""
        configured = self.atem is not None
        snap = self.atem.snapshot() if self.atem else None
        online = bool(snap and snap.online)
        per = []
        for c in self.grid.cells:
            inp = atem_input_of(c.cfg) if c else 0
            per.append((inp, snap.state(inp) if snap and inp else None, bool(snap and inp and snap.known(inp))))
        key = (configured, online, tuple(per), self.grid.selected)
        if key == self._tally_shown:
            return
        self._tally_shown = key
        self._tally_per = [(inp, state if online else None, known) for inp, state, known in per]
        self._update_cell_headers()
        for cell, (_inp, state, _known) in zip(self.cells, per):
            cell.video_frame.set_tally(state if online else None)
        inp, state, known = per[self.grid.selected or 0]
        pill = atem_pill(configured, online, inp, state, known=known)
        if pill is None:
            self.atem_pill.hide()
        else:
            self.atem_pill.set(*pill)
            self.atem_pill.show()
        self._fit_top_bar()

    @staticmethod
    def _caption(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: {FS_CAPTION}px;")
        return lab

    def _on_stream_changed(self, key: str) -> None:
        cam = self._current_cfg()
        if not cam or cam.kind == "sim":
            return
        cam.stream = key
        self._save()
        self._open_cell(self.grid.selected, cam)

    def service_kind(self, slot: int) -> str | None:
        """Какая служебная клетка стоит в пустой клетке `slot` (без выбора оператора: первая пустая — сводка,
        вторая — положение камер, третья — графики). Без камер — только приветствие."""
        return next((k for k, s in self._service_slot.items() if s == slot), None)

    def _service_plan(self) -> dict[str, int | None]:
        """Где какая служебная клетка: сначала выбор оператора в клетке (и «По очереди»), остальное — по порядку.
        Один вид — в одной клетке."""
        empty = [i for i, c in enumerate(self.grid.cells) if c is None]
        for slot in [s for s in self._service_pref if s not in empty]:
            del self._service_pref[slot]  # в клетку поставили камеру — её выбор забыт
        plan: dict[int, str] = {}
        if len(empty) < GRID_SLOTS:
            for slot in empty:
                p = self._service_pref.get(slot)
                if p == "cycle":
                    p = next((k for k in (self._cycle_kind, *CYCLE_KINDS) if k not in plan.values()), None)
                if p in KINDS and p not in plan.values():
                    plan[slot] = p
            for slot in empty:
                if slot not in plan:
                    k = next((k for k in KINDS if k not in plan.values()), None)
                    if k is not None:
                        plan[slot] = k
        return {k: next((s for s, kk in plan.items() if kk == k), None) for k in KINDS}

    def _cycle_tick(self, force: bool = False) -> None:
        """«По очереди»: раз в CYCLE_S сменить вид; пока мышь над клеткой — пауза (не уезжает из-под глаз)."""
        slot = next((s for s, p in self._service_pref.items() if p == "cycle"), None)
        for i, cell in enumerate(self.cells):
            if i != slot:
                cell.set_cycle(None)
        if slot is None:
            return
        now = time.monotonic()
        if self.cells[slot].underMouse():
            self._cycle_started = now
        elif force or now - self._cycle_started >= CYCLE_S:
            i = CYCLE_KINDS.index(self._cycle_kind) if self._cycle_kind in CYCLE_KINDS else -1
            self._cycle_kind = CYCLE_KINDS[(i + 1) % len(CYCLE_KINDS)]
            self._cycle_started = now
            self._refresh_service_cells()
        self.cells[slot].set_cycle(min(1.0, (now - self._cycle_started) / CYCLE_S))

    def _service_pads(self, slot: int) -> list[int]:
        return self.pad_router.pads_on(slot)

    def _refresh_service_cells(self) -> None:
        empty = [i for i, c in enumerate(self.grid.cells) if c is None]
        no_cams = len(empty) == GRID_SLOTS
        want = self._service_plan()
        if self._welcome_on and not no_cams:
            self.cells[0].set_service(None)
            self._welcome_on = False
        moved = [k for k in self._service if self._service_slot[k] != want[k]]
        for kind in moved:  # два прохода: снять все переезжающие, потом поставить — иначе соседний снимет новый
            if self._service_slot[kind] is not None:
                self.cells[self._service_slot[kind]].set_service(None)
        for kind in moved:
            if want[kind] is not None:
                self.cells[want[kind]].set_service(self._service[kind])
            self._service_slot[kind] = want[kind]
        if no_cams and not self._welcome_on:
            self.cells[0].set_service(self.welcome)
            self._welcome_on = True
        self._fit_later()
        where = {slot: TITLES[k] for k, slot in want.items() if slot is not None}
        for i, cell in enumerate(self.cells):
            cell.set_empty_title(where.get(i, "нет камеры"))
        if not any(w is not None for w in want.values()):
            return
        rows = []
        for i, c in enumerate(self.grid.cells):
            if c is None:
                continue
            s = c.session
            r = s.analyzer.latest() if s.analyzer else None
            st = s.frames.stats()
            if s.power in ("standby", "switching"):
                track = "standby"
            elif s.analyzer is None:
                track = "no_analysis"
            else:
                track = {"on": "on", "lost": "lost"}.get(self._current_view(c).kind, "off")
            pads = self._cell_pads(i)
            tl = self._timelines.setdefault(c.cfg.id, StateTimeline())
            now = time.monotonic()
            tl.record(now, track, self._tally_per[i][1] == "pgm")
            rows.append(ServiceRow(
                i, c.cfg.name, s.ptz, s.ptz_history(), r.fps if r else None, s.online, [n for n, *_ in pads],
                tally=self._tally_per[i][1], track=track, battery={n: (p, ch) for n, p, ch, *_ in pads},
                calibrated=c.cfg.calibration is not None, video_fps=st.fps if st.connected else None,
                preset_pans=[t.pan for t in self._thumbs_of(c.cfg.id).values() if t.pan is not None],
                timeline=tuple(tl.segments(now)), air=tuple(tl.air(now)), clock=tl.clock(now),
                air_total=tl.air_total(now), losses=tl.losses, span_s=tl.span(now)))
        for kind, widget in self._service.items():
            if want[kind] is not None:
                widget.update_view(rows, self.grid.selected)

    def _poll_positions(self) -> None:
        """Положение камер нужно только видимым клеткам position/graphs; poll_position лишь ставит запрос в очередь."""
        if self._service_slot["position"] is None and self._service_slot["graphs"] is None:
            return
        for c in self.grid.cells:
            if c is not None:
                c.session.poll_position()

    def _warn_refused(self) -> None:
        """Камера выбранной клетки отклоняет ирис / gain / баланс белого (ER3, режим Full Auto) — сказать в строке
        состояния, раз в REFUSED_SHOW_S, а не молча откатывать ползунки."""
        s = self.session
        f = getattr(s, "refused_recently", None)
        if not callable(f) or not f():
            return
        now = time.monotonic()
        if now - getattr(self, "_refused_said", -1e9) < REFUSED_SHOW_S:
            return
        self._refused_said = now
        self.statusBar().showMessage(
            f"Камера «{s.cfg.name}» не принимает ирис, gain, баланс белого и затвор: она в режиме Full Auto. "
            "Выключите Full Auto: быстрые настройки (M) → Меню Panasonic на камере → Scene.",
            int(REFUSED_SHOW_S * 1000) + 1000)

    def _update_status(self) -> None:
        self.load_meter.set_snapshot(self.system_load.snapshot())
        self._refresh_video()
        self._warn_refused()
        self._poll_update_check()
        self._refresh_manual_speed()
        self._refresh_service_cells()
        self._refresh_strip()  # снимки и эфир в полоске «1 камера» — раз в тик статуса
        self._cycle_tick()
        self._poll_positions()
        for i, c in enumerate(self.grid.cells):
            cell = self.cells[i]
            if c is None:
                cell.power_overlay.set_view("on", "")
                self._update_target_label(cell, None)
                continue
            s = c.session
            cell.power_overlay.set_view(power_view(s.power, s.online), s.cfg.name, s.supports("power"))
            if self.camera_menu.is_open and self._menu_host is cell.video_frame:
                self.camera_menu.raise_()  # заставка «камера выключена» поднимается каждый тик — меню остаётся сверху
            self._remember_rtsp_url(s)
            self._remember_speed_scale(s, i)
            self._remember_camera_info(s)
            self._update_target_label(cell, s)
        self._update_hint_context()
        self._update_cell_headers()
        for i, cell in enumerate(self.cells):
            cell.set_pads(self._cell_pads(i))  # заряд меняется сам по себе
        self._watch_thumbs()
        self._refresh_drawer()
        self._update_selected_status()

    def _update_selected_status(self) -> None:
        """Значки верхней строки и правая панель — для выбранной клетки."""
        s = self.session
        cam = self._current_cfg()
        can_power = s is not None and s.supports("power")  # ER1 на питание: кнопка остаётся, но серая
        self.power_action.setText("Включить камеру" if s and s.power == "standby"
                                  else "Перевести камеру в режим ожидания…")
        self.power_action.setEnabled(can_power)
        self.power_action.setToolTip("" if can_power or s is None else "Камера не принимает команду питания от программы")
        if not s:
            self.video_pill.set(*video_pill(None, ""))
            self.analysis_pill.set("", "off", "Анализ: камера не выбрана")
            self._sync_trouble_pills()
            self.blackbox_btn.set("Журнал", "off", "Журнал для разбора: камера не выбрана")
            self._fit_top_bar()
            return
        self._update_recalibration_hint(s, cam)
        stream = STREAM_CHOICES.get(cam.stream if cam else "auto", "Авто")
        st = s.frames.stats()
        text, kind, tip = video_pill(st, stream)
        if not s.online:
            kind, tip = "fault", f"Нет связи с камерой {s.cfg.name} ({s.cfg.ip})\n{tip}"
        self.video_pill.set(text, kind, tip)
        mins = s.app_cfg.record_buffer_s // 60
        self.blackbox_btn.set(
            "Журнал", "neutral" if s.recorder.active else "off",
            f"Чёрный ящик: хранит логи последних {mins} мин (не видео). F9 — сохранить момент"
            if s.recorder.active else "Чёрный ящик выключен",
        )
        self._update_analysis_status(s)
        self.controls.sync_image(s.image)
        unsupported = s.camera_info().unsupported
        if unsupported != self._unsupported_shown:
            self._unsupported_shown = unsupported
            self.controls.set_unsupported(unsupported)
        self._fit_top_bar()

    def _update_analysis_status(self, s: CameraSession) -> None:
        self._set_analysis_status(s)
        self._sync_trouble_pills()

    def _set_analysis_status(self, s: CameraSession) -> None:
        a = s.analyzer
        r = a.latest() if a else None
        if a is None:
            why = ("Без анализа: слежение на этой камере выключено в настройках" if not s.cfg.tracking
                   else "Без анализа: место анализа занято другой камерой")
            self.analysis_pill.set("", "off", why)
        else:
            self.analysis_pill.set(*analysis_pill(a.error, r.fps if r else None, r.infer_ms if r else None,
                                                  r.device if r else None))

    def _update_hint_context(self) -> None:
        """Подсказка внизу видео выбранной клетки: есть ли цель, ведёт ли камеру геймпад, мышь над зоной."""
        sel = self.grid.selected
        c = self.grid.cells[sel] if sel is not None else None
        a = c.session.analyzer if c else None
        r = a.latest() if a else None
        tid = r.target_id if r is not None else None
        pad = self.last_input == "pad" and bool(sel is not None and self.pad_router.pads_on(sel))
        hover = sel is not None and self.cells[sel].preview.zone_hovered()
        self.follow_panel.set_hint_context(tid is not None, tid, pad, hover)

    @staticmethod
    def _update_target_label(cell: CameraCell, s: CameraSession | None) -> None:
        a = s.analyzer if s else None
        r = a.latest() if a else None
        if r is None or r.target_id is None:
            cell.target_label.setText("Цель не выбрана")  # что делать — в подсказке снизу
            cell.target_label.setStyleSheet("")
        elif r.target_state == "tracking":
            cell.target_label.setText(f"Цель: №{r.target_id} — веду")
            cell.target_label.setStyleSheet(f"color: {C.ON}; font-weight: bold;")
        else:
            cell.target_label.setText(f"Цель: №{r.target_id} — потеряна")
            cell.target_label.setStyleSheet(f"color: {C.LOST}; font-weight: bold;")

    def _fit_top_bar(self) -> None:
        """Узкое окно: ATEM теряет текст; камера и TRACK — никогда."""
        if not hasattr(self, "_top"):
            return  # ещё строится окно
        pills = (self.atem_pill,)
        for p in pills:
            p.set_compact(False)
        avail = self.width() - 2 * SP[1]
        for p in pills:
            if self._top.sizeHint().width() <= avail:
                break
            p.set_compact(True)
        self._fit_status_bar()

    def _fit_status_bar(self) -> None:
        """Узкое окно: первой уходит нагрузка, сообщениям нужно хотя бы немного места."""
        sb = self.statusBar()
        others = [w for w in sb.findChildren(QWidget, options=Qt.FindDirectChildrenOnly)
                  if not w.isHidden() and w not in (self.load_meter, sb.message_label)]
        need = sum(w.sizeHint().width() + SP[2] for w in others) + self.load_meter.sizeHint().width()
        self.load_meter.setVisible(need + MESSAGE_MIN_W <= self.width())

    def _sync_trouble_pills(self) -> None:
        """Разрешение и к/с уже есть в углу видео: внизу эти значки — только когда что-то не так."""
        for pill in (self.video_pill, self.analysis_pill):
            pill.setVisible(pill.kind() in ("fault", "warn"))

    def _menu_button(self, icon_name: str, tooltip: str) -> StatusPill:
        b = StatusPill(icon_name, "", "neutral")
        b.set("", "neutral", tooltip)
        b.setPopupMode(QToolButton.InstantPopup)
        b.setMenu(QMenu(self))
        return b

    def _power_menu(self) -> None:
        s = self.session
        if s is None:
            return
        if s.power == "standby":
            self._power_on()
            return
        # как любое ручное действие: слежение и агент выключаются до вопроса и до #O0
        self.agent_panel.revoke()
        self.release_all_held()
        if f := self._follower():
            f.disable("выключено оператором")
        self._sync_follow()
        q = (f"Перевести камеру {s.cfg.name} в режим ожидания?\n"
             "Видео пропадёт; включение займёт около 8 секунд.")
        if QMessageBox.question(self, "Режим ожидания", q) == QMessageBox.Yes:
            s.set_power(False)

    def _power_on(self, slot: int | None = None) -> None:
        c = self.grid.cells[self.grid.selected if slot is None else slot]
        if c:
            c.session.set_power(True)
            self._update_status()

    def _show_agent_window(self) -> None:
        self.agent_window.resize(560, max(320, self.agent_window.sizeHint().height()))  # по карточкам, без пустоты
        self.agent_window.show()
        self.agent_window.raise_()

    def _fit_later(self) -> None:
        """Подогнать правую колонку и ряды сетки на следующем витке событий. Таймер — ребёнок окна: QTimer.singleShot
        с методом окна держал бы окно в памяти и после закрытия (так тесты копили по ~300 МБ на окно)."""
        t = getattr(self, "_fit_timer", None)
        if t is None:
            t = self._fit_timer = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(0)
            t.timeout.connect(self._fit_side)
            t.timeout.connect(self._fit_rows)
        t.start()

    def _fit_side(self) -> None:
        """Свободную высоту правой колонки (высокий экран) — стику: он растёт, пока колонка без прокрутки."""
        joy = self.controls.joystick.width()
        free = self.side_scroll.viewport().height() - self.side_scroll.widget().minimumSizeHint().height()
        spare = free + (joy - STICK_MIN) - SIDE_FIT_RESERVE  # свободно при самом маленьком стике, сверх запаса
        self.controls.set_stick_size(STICK_MIN + max(0, int(spare * 0.6)))

    def _fit_rows(self) -> None:
        """Внизу нет камер — верхний ряд ровно по кадру 16:9 (картинку ограничивает ширина клетки, лишняя высота
        дала бы только чёрные полосы), остальное — служебным клеткам. С камерой внизу ряды снова поровну."""
        gl = self.grid_host.layout()
        top = self.cells[0]
        service_below = self.grid.cells[2] is None and self.grid.cells[3] is None
        if not service_below or self.grid.cells[0] is None or self.single_view:
            gl.setRowStretch(0, 1)
            for c in self.cells[:2]:
                c.setMaximumHeight(QWIDGETSIZE_MAX)
            return
        vf = top.video_frame
        b = vf.BORDER
        want = top.height() - vf.height() + round((vf.width() - 2 * b) * 9 / 16) + 2 * b
        # потолок, а не минимум: окно можно ужать, а при нехватке места (пресеты во весь экран) ряд просто ниже
        gl.setRowStretch(0, 2)
        for c in self.cells[:2]:
            c.setMaximumHeight(max(top.minimumSizeHint().height(), want))

    def _remember_rtsp_url(self, s: CameraSession) -> None:
        """Сохранить подобранный адрес видео (без пароля), чтобы в следующий раз открывался сразу."""
        found = s.found_rtsp_url
        if not found:
            return
        s.found_rtsp_url = None
        url = strip_credentials(found)
        cam = next((c for c in self.cfg.cameras if c.id == s.cfg.id), None)
        if cam and cam.rtsp_url != url:
            cam.rtsp_url = url
            self._save()
            self.statusBar().showMessage(f"Найден адрес видео: {url}", 8000)

    def _remember_camera_info(self, s: CameraSession) -> None:
        """Модель, прошивку и найденные «не умею» — в настройки камеры (сессия настройки не пишет).
        Известную модель не затираем пустым ответом; симулятору сведения не нужны."""
        cam = next((c for c in self.cfg.cameras if c.id == s.cfg.id), None)
        if cam is None or cam.kind == "sim":
            return
        info = s.camera_info()
        model = info.model or cam.model
        # прошивка: новая модель — прошивка из ответа (она могла быть None), та же — известную не теряем
        firmware = info.firmware or (cam.firmware if model == cam.model else None)
        unsupported = list(info.unsupported)
        if (model, firmware, set(unsupported)) == (cam.model, cam.firmware, set(cam.unsupported)):
            return
        cam.model, cam.firmware, cam.unsupported = model, firmware, unsupported
        self._save()
        log.info("Сведения о камере %s сохранены: %s %s, не умеет: %s", cam.name, model, firmware, unsupported)

    def _update_recalibration_hint(self, s: CameraSession, cam: CameraConfig | None) -> None:
        """Мягкая подсказка под кнопками кадра, если калибровка сделана на другой камере или в прежней версии.
        Не блокирует ничего; для каждой камеры показывается один раз за запуск (до смены камеры)."""
        hint = None
        info = s.camera_info()
        if cam is not None and cam.kind != "sim" and (info.model is not None or s.online):
            hint = recalibration_hint(cam.calibration, info.model, info.firmware)
        if hint and (cam.id == self._recalib_hint_for or cam.id not in self._recalib_hinted):
            self._recalib_hinted.add(cam.id)
            self._recalib_hint_for = cam.id
        else:
            hint = None
        self.follow_panel.set_calibration_note(hint)

    def _remember_speed_scale(self, s: CameraSession, slot: int) -> None:
        """Поправку скорости, найденную слежением, — в настройки камеры (не чаще раза в SPEED_SCALE_SAVE_S).
        Поток слежения настройки не пишет: только читает поправку отсюда, из потока Qt."""
        f = s.follower
        if f is None or f.polling_paused or time.monotonic() - self._speed_scale_saved.get(slot, float('-inf')) < SPEED_SCALE_SAVE_S:
            return  # идёт калибровка: её итог сбросит поправку
        cam = next((c for c in self.cfg.cameras if c.id == s.cfg.id), None)
        if cam is None or cam.kind == "sim":  # симулятору поправка скорости не нужна, в настройки её не пишем
            return
        scale: dict = {k: round(v, 3) for k, v in f.speed_scale.items()}
        old = cam.speed_scale if isinstance(cam.speed_scale, dict) else {}
        if cam.calibration is None:
            scale["table"] = DEFAULT_TABLE_ID  # поправка к таблице без калибровки — с её ярлыком
        if old.get("table") == scale.get("table") and all(
            abs(v - float(old.get(k, 1.0))) < SPEED_SCALE_SAVE_DIFF for k, v in scale.items() if k != "table"
        ):
            return
        cam.speed_scale = scale
        self._speed_scale_saved[slot] = time.monotonic()
        self._save()
        log.info("Поправка скорости камеры %s сохранена: %s", cam.name, scale)

    def show_hotkeys(self) -> None:
        if self.hotkeys_dialog is None:
            self.hotkeys_dialog = HotkeysDialog(parent=self)
        self.hotkeys_dialog.show()
        self.hotkeys_dialog.raise_()
