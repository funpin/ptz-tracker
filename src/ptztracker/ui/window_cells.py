"""Клетки сетки: какая камера где, выбор клетки, режимы вида, анализ."""

from __future__ import annotations

import dataclasses
import logging
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel, QMenu

from ptztracker.camera.session import CameraSession
from ptztracker.config import GRID_SLOTS, SPEED_GEARS, CameraConfig
from ptztracker.multicam.analysis import CellState, allocate_analysis
from ptztracker.multicam.grid import Cell
from ptztracker.multicam.velocity import MixerSource
from ptztracker.ui.camera_cell import CameraCell
from ptztracker.ui.camera_strip import StripItem
from ptztracker.ui.icons import icon
from ptztracker.ui.service_cell import KINDS, TITLES
from ptztracker.ui.theme import C
from ptztracker.ui.widgets import StatusPill, VideoFrame
from ptztracker.ui.window_common import SERVICE_CHOICES, SIDE_WIDTH, STRIP_THUMB_H

log = logging.getLogger(__name__)


class CellsMixin:
    """Часть MainWindow (ui/main_window.py): состояние окна — в его __init__."""

    @property
    def selected_cell(self) -> Cell | None:
        sel = self.grid.selected
        return self.grid.cells[sel] if sel is not None else None

    @property
    def _cell(self) -> CameraCell:
        return self.cells[self.grid.selected or 0]

    @property
    def session(self) -> CameraSession | None:
        c = self.selected_cell
        return c.session if c else None

    @property
    def preview(self):
        return self._cell.preview

    @property
    def video_frame(self) -> VideoFrame:
        return self._cell.video_frame

    @property
    def power_overlay(self):
        return self._cell.power_overlay

    @property
    def target_label(self) -> QLabel:
        return self._cell.target_label

    @property
    def reset_target_btn(self) -> StatusPill:
        return self._cell.reset_target_btn

    @property
    def skeleton_check(self) -> StatusPill:
        return self._cell.skeleton_check

    def _make_session(self, cfg: CameraConfig, analysis: bool) -> CameraSession:
        return CameraSession(cfg, scene_path=self.cfg.sim_scene_path, app_cfg=self.cfg, analysis=analysis)

    def _is_stored(self, cam: CameraConfig | None) -> bool:
        """Камера записана в настройки (её можно изменить и удалить); временный --sim — нет."""
        return cam is not None and any(c.id == cam.id for c in self.cfg.cameras)

    @staticmethod
    def _choice_label(c: CameraConfig) -> str:
        if c.kind == "sim":  # «Симулятор» в названии и так всё говорит: «(симулятор)» не дублируем
            return c.name if "симулятор" in c.name.lower() else f"{c.name} (симулятор)"
        return f"{c.name} · {c.ip}"

    @property
    def sim_badge(self) -> QLabel:
        """Плашка «СИМУЛЯТОР» выбранной клетки (совместимость: окно на одну камеру)."""
        return self.cells[self.grid.selected if self.grid.selected is not None else 0].sim_tag

    def _sync_sim_badges(self) -> None:
        self._update_cell_headers()

    def _build_cell_menu(self, slot: int, menu: QMenu, stream_menu: QMenu) -> None:
        """⋯ в шапке клетки: всё про эту камеру. Перед показом клетка выбирается — действия идут на неё."""
        menu.aboutToShow.connect(lambda: self.select_slot(slot))

        def ic(name: str):  # значки — как в меню ☰, серым
            return icon(name, C.TEXT_DIM, 16)

        menu.addAction(self.osd_action)
        self.osd_action.setIcon(ic("adjustments"))
        streams = menu.addMenu(ic("video"), "Видеопоток")
        for act in self.stream_actions.actions():
            streams.addAction(act)
        menu.addSeparator()
        menu.addAction(self.calib_action)
        self.calib_action.setIcon(ic("track"))
        menu.addAction(self.diag_action)
        self.diag_action.setIcon(ic("chip"))
        menu.addSeparator()
        menu.addAction(self.edit_action)
        self.edit_action.setIcon(ic("camera"))
        menu.addAction(ic("user-x"), "Убрать из клетки", lambda: self.set_cell_camera(slot, None))
        menu.addSeparator()
        menu.addAction(self.power_action)
        self.power_action.setIcon(ic("power"))

    def _open_osd_menu(self) -> None:
        if not self.camera_menu.is_open:
            self.release_all_held()
            self.camera_menu.pad_hints = False  # открыли клавишей или мышью — клавиши
            self.camera_menu.toggle()

    def _cell_power(self, slot: int) -> None:
        """⏻ в шапке клетки: как пункт меню, но для этой камеры (слежение выключится до вопроса)."""
        self.select_slot(slot)
        self._power_menu()

    def _cell_pads(self, slot: int) -> list[tuple]:
        out = []
        for n in self.pad_router.pads_on(slot):
            b = self.pads.get(n)
            out.append((n, b.battery_percent if b else None, bool(b and b.battery_charging), self._pad_gear(n)))
        return out

    def _pad_gear(self, n: int) -> tuple[int, int] | None:
        """(ступень, ступеней) для значка геймпада — только когда L1/R1 у него «предел скорости»."""
        b = self.pads.get(n)
        pid = (b.info or {}).get("id") if b else None
        if pid is None or self.pad_l1r1_mode(pid) != "limit":
            return None
        return self.pad_speed_gear(pid), SPEED_GEARS

    def _update_side_head(self) -> None:
        sel, c = self.grid.selected, self.selected_cell
        self.side_num.setVisible(c is not None)
        self.side_num.setText(str(sel + 1) if sel is not None else "")
        name = c.cfg.name if c else "Камера не выбрана"
        self.side_name.setText(self.side_name.fontMetrics().elidedText(name, Qt.ElideRight, SIDE_WIDTH - 110))
        tally = self._tally_per[sel][1] if sel is not None else None
        self.side_tally.setVisible(tally is not None)
        if tally:
            self.side_tally.set(tally)

    def _update_cell_headers(self) -> None:
        """Шапки клеток из снимков: эфир, слежение, симулятор, питание, разрешение и анализ в углу видео."""
        for i, c in enumerate(self.grid.cells):
            cell = self.cells[i]
            if c is None:
                cell.set_header(None, None, False, None)
                cell.set_info("")
                continue
            s = c.session
            _inp, tally, _known = self._tally_per[i]
            if s.power in ("standby", "switching"):
                state = "standby"
            elif s.analyzer is None:
                state = "no_analysis"
            else:
                state = {"on": "on", "lost": "lost"}.get(self._current_view(c).kind, "off")
            cell.set_header(tally, state, c.cfg.kind == "sim", "standby" if s.power == "standby" else "on")
            self.agent_panel.set_tally(i, tally)
            st = s.frames.stats()
            info = f"{st.width}×{st.height} · {st.fps:.0f}" if st.connected and st.width else ""
            r = s.analyzer.latest() if s.analyzer else None
            if info and r is not None and r.fps:
                info += f" · анализ {r.fps:.0f}"
            cell.set_info(info)
        self._update_side_head()

    def _current_cfg(self) -> CameraConfig | None:
        c = self.selected_cell
        return c.cfg if c else None

    def _states(self) -> list[CellState | None]:
        return [
            CellState(c.cfg.name, c.cfg.tracking, bool(c.session.follower and c.session.follower.enabled))
            if c else None
            for c in self.grid.cells
        ]

    def _refresh_cell_choices(self) -> None:
        """В списке клетки — камеры, не стоящие в других клетках (одна камера — одна клетка)."""
        for i, cell in enumerate(self.cells):
            items = [
                (c.id, self._choice_label(c))
                for c in self._cameras() if self.grid.slot_of(c.id) in (None, i)
            ]
            cur = self.grid.cells[i]
            # пустая клетка (не приветствие): ещё и что в ней показывать
            free = cur is None and any(c is not None for c in self.grid.cells)
            cell.set_choices(items, cur.cfg.id if cur else None, SERVICE_CHOICES if free else [])

    def _initial_grid(self) -> None:
        by_id = {c.id: c for c in self._cameras()}
        ids = ["sim1", "sim2", None, None] if self.use_sim else list(self.cfg.grid)
        if not self.use_sim:  # на эфир не стартуем с симулятором: он открывается только из меню ☰
            ids = [i if i in by_id and by_id[i].kind != "sim" else None for i in ids]
            real = [c for c in self.cfg.cameras if c.kind != "sim"]
            if not any(ids) and real:
                ids[0] = real[0].id  # как раньше: без выбора открывается первая настоящая камера
        ids = [i if i in by_id else None for i in ids]
        occupied = [i for i, cid in enumerate(ids) if cid]
        want = self.cfg.last_camera_id if not self.use_sim else None
        sel = next((i for i, cid in enumerate(ids) if cid and cid == want), occupied[0] if occupied else 0)
        self.grid.selected = sel
        states = [CellState(by_id[c].name, by_id[c].tracking, False) if c else None for c in ids]
        pref = [i for cid in self.cfg.analysis_ids for i, c in enumerate(ids) if c == cid]
        alloc = allocate_analysis(states, pref, self.cfg.max_analyzed)
        for slot, cid in enumerate(ids):
            if cid:
                self._open_cell(slot, by_id[cid], analysis=slot in alloc)
        self._loading = False
        self._pad_sync_slots()
        self._reallocate_analysis()
        self._select_effects()
        self._refresh_cell_choices()
        self._refresh_service_cells()
        self._update_selected_status()
        self._auto_view()

    def _auto_view(self) -> None:
        """Одна камера — сразу крупно: сетка с тремя служебными клетками ради неё не нужна."""
        if sum(c is not None for c in self.grid.cells) == 1:
            sel = next(i for i, c in enumerate(self.grid.cells) if c is not None)
            if self.grid.selected != sel:
                self.select_slot(sel)
            self.set_single_view(True)

    def set_single_view(self, on: bool) -> None:
        self.single_view = on
        self.view_one_btn.setChecked(on)
        self.view_grid_btn.setChecked(not on)
        self._apply_view()
        self._refresh_drawer()  # одна камера — строка её пресетов; сетка — строки по кнопкам камер

    def _toggle_view_at(self, slot: int) -> None:
        if self.grid.cells[slot] is not None and slot != self.grid.selected:
            self.select_slot(slot)
            self.set_single_view(True)
            return
        self.set_single_view(not self.single_view)

    def _strip_clicked(self, slot: int) -> None:
        if self.grid.cells[slot] is None:  # плитка служебного вида — крупно вместо камеры; управление не трогаем
            self._single_slot = slot
            self._apply_view()
            return
        self._single_slot = None
        if slot == self.grid.selected:
            self._apply_view()
        else:
            self.select_slot(slot)

    def _apply_view(self) -> None:
        """«1 камера»: выбранная клетка на всю сетку, остальные спрятаны (их сессии работают); «4» — как было."""
        if not hasattr(self, "strip"):
            return
        gl = self.grid_host.layout()
        if self._single_slot is not None and self.grid.cells[self._single_slot] is not None:
            self._single_slot = None  # в клетку поставили камеру
        sel = self._single_slot if self._single_slot is not None else self.grid.selected
        for i, c in enumerate(self.cells):
            gl.removeWidget(c)
            if self.single_view and i == sel:
                gl.addWidget(c, 0, 0, 2, 2)
            elif not self.single_view:
                gl.addWidget(c, i // 2, i % 2)
            c.setVisible(not self.single_view or i == sel)
        if self.single_view:
            self._strip_timer.start()
        else:
            self._strip_timer.stop()
        self.strip.setVisible(self.single_view)
        self._refresh_strip()
        self._fit_later()

    @staticmethod
    def _strip_image(fr) -> QImage:
        h, w = fr.image.shape[:2]
        return QImage(fr.image.data, w, h, fr.image.strides[0], QImage.Format_BGR888).scaledToHeight(
            STRIP_THUMB_H, Qt.SmoothTransformation)

    def _refresh_strip_frames(self) -> None:
        """Только кадры камер в полоске, ~12 раз в секунду; кадр не новый — карточку не трогаем. Уменьшение 640×360
        до 128×72 — доли миллисекунды: на слежение и камеру не влияет (кадры уже декодированы потоком видео)."""
        if not self.single_view:
            return
        for card in self.strip.items:
            if card.item.service:
                continue
            c = self.grid.cells[card.slot]
            fr = c.session.frames.latest() if c is not None else None
            if fr is None or fr.seq == card.seq:
                continue
            card.seq = fr.seq
            card.item = dataclasses.replace(card.item, image=self._strip_image(fr))
            card.update()

    def _refresh_strip(self) -> None:
        if not self.single_view:
            return
        big = self._single_slot if self._single_slot is not None else self.grid.selected
        items = []
        for i, c in enumerate(self.grid.cells):
            if c is None or i == big:
                continue
            fr = c.session.frames.latest()
            img = self._strip_image(fr) if fr is not None else None
            items.append(StripItem(i, c.cfg.name, self._tally_per[i][1], img,
                                   recording=c.session.video_state().active))
        rows = self._service["summary"]._rows
        for kind in KINDS:  # служебные виды из свободных клеток — плитками после камер
            slot = self._service_slot.get(kind)
            if slot is not None and slot != big:
                items.append(StripItem(slot, TITLES[kind], None, None, service=kind, rows=rows))
        self.strip.set_items(items)

    def select_slot(self, slot: int, keep_analysis: bool = False) -> None:
        """Выбрать клетку: клавиатура, мышь, геймпад и правая панель теперь относятся к ней.
        Анализ выбор клетки не трогает: он там, где его назначил оператор (плашка «БЕЗ АНАЛИЗА»)."""
        if not 0 <= slot < GRID_SLOTS or slot == self.grid.selected:
            return
        self.camera_menu.close_menu()  # до переноса: ручной фокус получает ноль
        self.exit_mouse_mode()  # режим мыши — у выбранной клетки
        self.release_all_held()  # геймпад ведёт свою камеру и выбором клетки не трогается
        old = self.selected_cell
        if old is not None:
            old.mixer.release("keyboard")
            old.mixer.release("mouse")
        self.grid.selected = slot
        self._single_slot = None
        self._select_effects()
        self._apply_view()
        self._reallocate_analysis()
        self._save_grid()
        self._update_selected_status()
        self._update_hint_context()  # геймпад другой камеры — сразу подсказка клавиатуры, не ждём таймера

    def _select_effects(self) -> None:
        """Всё, что зависит от выбранной клетки: рамка, правая панель, управление, оверлеи, огоньки геймпада."""
        sel = self.grid.selected
        for i, cell in enumerate(self.cells):
            cell.set_selected(i == sel)
        self._move_overlays(self._cell.video_frame)
        cam, cell = self._current_cfg(), self.selected_cell
        self._recalib_hint_for = None  # подсказка не возвращается после переключения туда-обратно
        # то, что про камеру уже известно, сразу: скрытые строки не мигают при каждом переключении
        known = tuple(sorted(f for f in cam.unsupported if isinstance(f, str))) if cam is not None else ()
        self._unsupported_shown = known
        self.controls.set_unsupported(known)
        real = cam is not None and cam.kind != "sim"
        self.edit_action.setEnabled(self._is_stored(cam))
        self.del_action.setEnabled(self._is_stored(cam))
        self._sync_sim_badges()
        want = cam.stream if cam else "auto"
        for act in self.stream_actions.actions():
            act.setChecked(act.data() == want)
            act.setEnabled(real)
        self.controls.bind(self._source(cell, "mouse") if cell else None)
        if cam is not None:
            self.follow_panel.set_composition(cam.composition)
            if cam.composition.startswith("waist"):
                self._last_waist = cam.composition
            self.follow_panel.set_effort(cam.effort)
            self.follow_panel.set_calibrated(cam.calibration is not None)
        self.follow_panel.set_state(False, None)
        self.follow_panel.status_label.hide()
        self._view_shown = None
        self._tally_shown = None
        self._sync_pad_player()
        self._refresh_drawer()

    def _move_overlays(self, frame: VideoFrame) -> None:
        if self._overlay_host is frame:
            return
        w = self.follow_panel.status_label
        hidden = w.isHidden()
        if self._overlay_host is not None:
            self._overlay_host.remove_overlay(w)
        frame.add_overlay(w, "bottom-left")
        w.setVisible(not hidden)
        self._overlay_host = frame
        if self._menu_pad is None:
            self._menu_to(frame)

    def _menu_to(self, frame: VideoFrame) -> None:
        if self._menu_host is frame:
            return
        m = self.camera_menu
        hidden = m.isHidden()
        if self._menu_host is not None:
            self._menu_host.remove_overlay(m)
        frame.add_overlay(m, m.placement())
        m.setVisible(not hidden)
        self._menu_host = frame

    def _menu_slot(self) -> int | None:
        """Клетка, на которую действует меню Options: камера геймпада, если меню открыли с него."""
        if self._menu_pad is not None:
            return self.pad_router.slot_of(self._menu_pad)
        return self.grid.selected

    def menu_session(self) -> CameraSession | None:
        """Сессия, на которую действует меню Options (читает API: агент не трогает эту камеру, пока меню открыто)."""
        return self._menu_session()

    def _menu_session(self) -> CameraSession | None:
        slot = self._menu_slot()
        c = self.grid.cells[slot] if slot is not None else None
        return c.session if c else None

    def _source(self, cell: Cell, name: str) -> MixerSource:
        """Источник смесителя клетки (один объект на клетку и имя: он помнит свои оси)."""
        slot = self.grid.cells.index(cell)
        key = (slot, name)
        got = self._srcs.get(key)
        if got is None or got[0] is not cell:
            got = (cell, cell.mixer.source(name))
            self._srcs[key] = got
        return got[1]

    def _on_cell_chosen(self, slot: int, camera_id) -> None:
        """Оператор выбрал камеру в списке клетки — или, в пустой клетке, что в ней показывать."""
        if isinstance(camera_id, str) and camera_id.startswith("svc:"):
            self._service_pref[slot] = camera_id[4:]
            self._cycle_started = time.monotonic()
            self._refresh_service_cells()
            self._refresh_cell_choices()
            return
        if slot != self.grid.selected:
            self.select_slot(slot)
        try:
            self.set_cell_camera(slot, camera_id)
        except ValueError as e:
            log.warning("%s", e)
            self._refresh_cell_choices()

    def set_cell_camera(self, slot: int, camera_id: str | None) -> None:
        """Открыть камеру в клетке (или закрыть клетку, если `camera_id` — None).

        Камера, уже стоящая в другой клетке, — ValueError, клетка остаётся как была."""
        if not 0 <= slot < GRID_SLOTS:
            raise ValueError(f"нет клетки {slot}")
        cam = None
        if camera_id is not None:
            cam = next((c for c in self._cameras() if c.id == camera_id), None)
            if cam is None:
                raise ValueError(f"нет камеры {camera_id}")
            other = self.grid.slot_of(camera_id)
            if other is not None and other != slot:
                raise ValueError(f"камера {camera_id} уже открыта в клетке {other + 1}")
        self._open_cell(slot, cam)

    def _open_cell(self, slot: int, cam: CameraConfig | None, analysis: bool | None = None) -> None:
        other = self.grid.slot_of(cam.id) if cam is not None else None
        if other is not None and other != slot:  # до разборки клетки: отказ оставляет её как была
            raise ValueError(f"камера {cam.id} уже открыта в клетке {other + 1}")
        selected = slot == self.grid.selected
        self._last_preset.pop(slot, None)  # пресеты и снимки — прежней камеры клетки, не новой
        self._thumb_watch.pop(slot, None)
        if self.camera_menu.is_open and self._menu_slot() == slot:
            self.camera_menu.close_menu()  # до остановки сессии: ручной фокус получает ноль
        if selected:
            self.release_all_held()
        d = self._cell_dialogs.pop(slot, None)
        if d is not None:
            d.reject()  # диалог относился к закрываемой камере
        self._speed_scale_saved.pop(slot, None)  # «раз в минуту» — для каждой камеры заново
        self._srcs = {k: v for k, v in self._srcs.items() if k[0] != slot}
        cell = self.cells[slot]
        cell.preview.set_source(None)
        cell.preview.set_analyzer(None)
        cell.preview.set_follow(None)
        self._tag_shown.pop(slot, None)
        self._view_shown = None if selected else self._view_shown
        if cam is None:
            self.grid.close(slot)
            cell.video_frame.tracking_tag.hide()
            cell.video_frame.set_tally(None)
        else:
            if analysis is None:
                states = self._states()
                states[slot] = CellState(cam.name, cam.tracking, False)
                analysis = slot in allocate_analysis(states, self._analysis_slot(), self.cfg.max_analyzed)
            try:
                opened = self.grid.open(slot, cam, analysis)
            except ValueError:
                raise
            except Exception as e:
                log.exception("Не удалось открыть камеру %s", cam.name)
                self.statusBar().showMessage(f"Не удалось открыть камеру {cam.name}: {e}", 8000)
                opened = None
            if opened is not None:
                s = opened.session
                opened.mixer.pt_scale = lambda s=s: self._manual_pt_scale(s)
                for p in self.pad_router.pads_on(slot):  # камеру в клетке сменили: новая поедет после центра стика
                    self._pad_hold_on_new(p, opened)
                cell.preview.set_source(s.frames)
                cell.preview.camera_name = cam.name
                if s.follower:
                    s.follower.on_change = self._follow_cb(s)  # поток слежения
        self._pad_sync_slots()  # закрытая клетка переводит её геймпады по правилу Create
        self.agent_panel.refresh()  # строки панели «Агент»; смена камеры сняла флажки прежней (её шлюз закрыт)
        if self._loading:
            return
        self._tally_shown = None
        if selected:
            self._select_effects()
        self._reallocate_analysis()
        self._sync_sim_badges()
        self._refresh_cell_choices()
        self._refresh_service_cells()
        self._save_grid()
        self._update_selected_status()

    def _save_grid(self) -> None:
        """Сетка и последняя выбранная камера — в настройки (в --sim ничего не пишем)."""
        if self.use_sim or self._loading:
            return
        self.cfg.grid = self.grid.ids()
        c = self.selected_cell
        if c is not None:
            self.cfg.last_camera_id = c.cfg.id
        self._save()

    def _analysis_slot(self) -> list[int]:
        """Клетки, которым оператор назначил анализ (важнее — раньше). Выбор клетки сюда не входит."""
        slots = {c.cfg.id: i for i, c in enumerate(self.grid.cells) if c is not None}
        return [slots[i] for i in self.cfg.analysis_ids if i in slots]

    def request_analysis(self, slot: int) -> None:
        """Оператор назначил анализ этой камере (клик по «БЕЗ АНАЛИЗА» или слежение на ней)."""
        c = self.grid.cells[slot] if 0 <= slot < len(self.grid.cells) else None
        if c is None:
            return
        self.cfg.analysis_ids = [c.cfg.id, *(i for i in self.cfg.analysis_ids if i != c.cfg.id)]
        self._save()
        self._reallocate_analysis()
        self._update_status()

    def _reallocate_analysis(self) -> None:
        """Нейросеть — слежущим, затем выбранной и остальным по порядку, пока не кончился лимит."""
        if self._loading:
            return
        alloc = allocate_analysis(self._states(), self._analysis_slot(), self.cfg.max_analyzed)
        for i, c in enumerate(self.grid.cells):
            if c is not None:
                c.session.set_analysis(i in alloc)
        for i, c in enumerate(self.grid.cells):  # анализатор и слежение могли смениться — перепривязать
            if c is None:
                continue
            self.cells[i].preview.set_analyzer(c.session.analyzer)
            self.cells[i].preview.set_follow(c.session.follower)
            if c.session.follower:
                c.session.follower.on_change = self._follow_cb(c.session)
        self._pad_limit_binding()

    def _pad_limit_binding(self) -> None:
        for i, c in enumerate(self.grid.cells):
            lw = c.session.limit_watch if c else None
            if lw is not None:
                bs = [self.pads[p] for p in self.pad_router.pads_on(i) if p in self.pads]
                lw.set_on_limit((lambda bs=bs: [b.notify_limit() for b in bs]) if bs else (lambda: None))

    def _on_max_analyzed(self, n: int) -> None:
        self.cfg.max_analyzed = int(n)
        self._save()
        self._reallocate_analysis()
        self._update_selected_status()
