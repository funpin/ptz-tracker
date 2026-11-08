"""Геймпады: подключение, команды, настройки, огоньки и вибрация."""

from __future__ import annotations

import logging

from ptztracker.camera.session import CameraSession
from ptztracker.config import DEFAULT_GEAR, DEFAULT_TILT_SPEED, SPEED_GEARS, TILT_SPEED_MAX, TILT_SPEED_MIN
from ptztracker.control.composition import COMPOSITIONS
from ptztracker.control.manual_curve import DEFAULT_SENS, PAD_GEAR_DEG_S, clamp_sens, gear_of
from ptztracker.gamepad.feedback import (
    RUMBLE_EDGE,
    RUMBLE_NOWHERE,
    RUMBLE_PRESET_STORED,
    RUMBLE_STEP,
    RUMBLE_TAKEN,
    PadStatus,
)
from ptztracker.gamepad.intents import (
    Composition,
    Disconnected,
    GainStep,
    Help,
    IrisStep,
    MenuAccept,
    MenuClose,
    MenuNav,
    MenuOpen,
    Neighbor,
    NextCamera,
    PanTilt,
    Preset,
    PushFocus,
    SaveMoment,
    SpeedStep,
    Stop,
    ToggleFollow,
    Zoom,
)
from ptztracker.multicam.grid import Cell
from ptztracker.multicam.pads import PadFeedback, PadMove
from ptztracker.tally import atem_input_of
from ptztracker.ui.gamepad_bridge import GamepadBridge
from ptztracker.ui.gamepads_dialog import GamepadsDialog, PadRow, time_left_text
from ptztracker.ui.hotkeys_dialog import fmt_remaining
from ptztracker.ui.widgets import battery_level
from ptztracker.ui.window_common import _PAD_SIZES, _is_action

log = logging.getLogger(__name__)


class PadsMixin:
    """Часть MainWindow (ui/main_window.py): состояние окна — в его __init__."""

    def pad_rows(self) -> list[PadRow]:
        """Геймпады для окна «Геймпады…»: все, что программа видела (config), и подключённые сейчас."""
        live: dict[str, GamepadBridge] = {}
        for b in self._bridges:
            pid = (b.info or {}).get("id") if b.connected else None
            if pid:
                live.setdefault(pid, b)
        rows = []
        for pid in dict.fromkeys([*self.cfg.pad_names, *live]):
            b = live.get(pid)
            name = self.cfg.pad_names.get(pid) or ((b.info or {}).get("name") if b else None) or "Геймпад"
            cell = self._pad_slot_number(self._pad_nums.get(b)) if b is not None else None
            batt = dict(battery=b.battery_percent, charging=bool(b.battery_charging),
                        battery_time=time_left_text(b.battery_remaining_s())) if b is not None else {}
            tally = self._tally_per[cell - 1][1] if cell is not None and cell <= len(self._tally_per) else None
            rows.append(PadRow(pid, name, self.pad_sensitivity({"id": pid}), b is not None, cell, tally=tally,
                               mode=self.pad_l1r1_mode(pid), gear=self.pad_speed_gear(pid),
                               main=pid == self.cfg.main_pad, tilt_sens=self.pad_tilt_sensitivity(pid),
                               tilt_speed=self.pad_tilt_speed(pid), **batt))
        return rows

    def show_gamepads(self) -> None:
        if self.gamepads_dialog is None:
            self.gamepads_dialog = GamepadsDialog(
                self.pad_rows(), self.cfg.slow_on_tele, self.set_pad_sensitivity,
                self.controls.slow_tele_check.setChecked, parent=self, on_mode=self.set_pad_l1r1_mode,
                tele_strength=self.cfg.tele_strength, on_tele_strength=self._on_tele_strength,
                on_main=self.set_main_pad, on_tilt_sens=self.set_pad_tilt_sensitivity,
                on_tilt_speed=self.set_pad_tilt_speed)
        else:
            self.gamepads_dialog.set_rows(self.pad_rows())
            self.gamepads_dialog.set_slow_on_tele(self.cfg.slow_on_tele)
        self.gamepads_dialog.show()
        self.gamepads_dialog.raise_()

    def _refresh_gamepads_dialog(self) -> None:
        d = self.gamepads_dialog
        if d is not None and d.isVisible():
            d.set_rows(self.pad_rows())

    def pad_sensitivity(self, info: dict | None) -> int:
        """Чувствительность геймпада по его опознавателю (окно «Геймпады…»); незнакомый — по умолчанию."""
        pid = (info or {}).get("id")
        return clamp_sens(self.cfg.pad_sensitivity.get(pid, DEFAULT_SENS)) if pid else DEFAULT_SENS

    def set_pad_sensitivity(self, pad_id: str, sens: int) -> None:
        """Из окна «Геймпады…»: сохранить и сразу применить к подключённым геймпадам с этим опознавателем."""
        self.cfg.pad_sensitivity[pad_id] = clamp_sens(sens)
        self._save()
        for b in self._bridges:
            if (b.info or {}).get("id") == pad_id:
                b.set_sensitivity(self.cfg.pad_sensitivity[pad_id])
                b.set_tilt_sensitivity(self.pad_tilt_sensitivity(pad_id))  # вверх/вниз не задан — следует за ним

    def pad_tilt_sensitivity(self, pad_id: str | None) -> int:
        """Кривая вверх/вниз геймпада; не задана — как влево/вправо."""
        if not pad_id:
            return DEFAULT_SENS
        own = self.cfg.pad_tilt_sensitivity.get(pad_id)
        return clamp_sens(own) if own is not None else self.pad_sensitivity({"id": pad_id})

    def pad_tilt_speed(self, pad_id: str | None) -> int:
        """Скорость вверх/вниз, % от влево/вправо; не задана — DEFAULT_TILT_SPEED."""
        return self.cfg.pad_tilt_speed.get(pad_id, DEFAULT_TILT_SPEED) if pad_id else DEFAULT_TILT_SPEED

    def set_pad_tilt_sensitivity(self, pad_id: str, sens: int) -> None:
        """Из окна «Геймпады…»: кривая вверх/вниз, сохранить и сразу применить."""
        self.cfg.pad_tilt_sensitivity[pad_id] = clamp_sens(sens)
        self._save()
        for b in self._bridges:
            if (b.info or {}).get("id") == pad_id:
                b.set_tilt_sensitivity(self.cfg.pad_tilt_sensitivity[pad_id])

    def set_pad_tilt_speed(self, pad_id: str, pct: int) -> None:
        """Из окна «Геймпады…»: скорость вверх/вниз в % от влево/вправо, сохранить и сразу применить."""
        self.cfg.pad_tilt_speed[pad_id] = max(TILT_SPEED_MIN, min(TILT_SPEED_MAX, int(pct)))
        self._save()
        for b in self._bridges:
            if (b.info or {}).get("id") == pad_id:
                b.set_tilt_speed(self.cfg.pad_tilt_speed[pad_id])
        self._apply_held()  # стик держат — новая скорость сразу

    def pad_l1r1_mode(self, pad_id: str | None) -> str:
        """Что меняют L1/R1 у геймпада: "limit" — предел скорости (по умолчанию), "sens" — чувствительность."""
        return self.cfg.pad_l1r1_mode.get(pad_id, "limit") if pad_id else "limit"

    def pad_speed_gear(self, pad_id: str | None) -> int:
        """Ступень скорости 1…SPEED_GEARS; незнакомый геймпад — DEFAULT_GEAR."""
        return self.cfg.pad_gear.get(pad_id, DEFAULT_GEAR) if pad_id else DEFAULT_GEAR

    def set_pad_l1r1_mode(self, pad_id: str, mode: str) -> None:
        """Из окна «Геймпады…»: режим L1/R1; предел скорости действует только в режиме "limit"."""
        self.cfg.pad_l1r1_mode[pad_id] = "sens" if mode == "sens" else "limit"
        self._save()
        for b in self._bridges:
            if (b.info or {}).get("id") == pad_id:
                self._apply_pad_speed(b)
        self._apply_held()  # стик держат — новый предел сразу

    def _apply_pad_speed(self, b: GamepadBridge) -> None:
        pid = (b.info or {}).get("id")
        if pid is None:
            return  # без опознавателя ступень живёт только в мосте, до отключения
        gear = self.pad_speed_gear(pid) if self.pad_l1r1_mode(pid) == "limit" else DEFAULT_GEAR
        b.set_speed_limit(PAD_GEAR_DEG_S[gear - 1])

    def _pad_speed_step(self, b: GamepadBridge, d: int) -> None:
        """L1/R1: ступень предела скорости или чувствительность ±10; за краем — длинный толчок."""
        pid = (b.info or {}).get("id")
        if self.pad_l1r1_mode(pid) == "sens":
            cur = b.sensitivity
            new = clamp_sens(cur + 10 * d)
            if new == cur:
                b.rumble(RUMBLE_EDGE)
                return
            if pid:
                self.set_pad_sensitivity(pid, new)
            else:
                b.set_sensitivity(new)
            b.rumble(RUMBLE_STEP)
        else:
            cur = self.pad_speed_gear(pid) if pid else gear_of(b.speed_limit)
            new = max(1, min(SPEED_GEARS, cur + d))
            if new == cur:
                b.rumble(RUMBLE_EDGE)
                return
            if pid:
                self.cfg.pad_gear[pid] = new
                self._save()
                for x in self._bridges:  # одинаковые геймпады без серийного номера делят ступень
                    if (x.info or {}).get("id") == pid:
                        self._apply_pad_speed(x)
            else:
                b.set_speed_limit(PAD_GEAR_DEG_S[new - 1])
            b.rumble(RUMBLE_STEP)
        self._refresh_gamepads_dialog()

    def _remember_pad(self, b: GamepadBridge, info: dict) -> None:
        pid, name = info.get("id"), info.get("name") or "Геймпад"
        if pid:
            b.set_sensitivity(self.pad_sensitivity(info))
            b.set_tilt_sensitivity(self.pad_tilt_sensitivity(pid))
            b.set_tilt_speed(self.pad_tilt_speed(pid))
            self._apply_pad_speed(b)
            if self.cfg.pad_names.get(pid) != name:
                self.cfg.pad_names[pid] = name
                self._save()

    def _pad_cell(self, pad: int | None) -> Cell | None:
        """Клетка, которой управляет геймпад n (по умолчанию — первый физический). Единственное место выбора камеры для него."""
        return self._cell_of_pad(pad)

    @property
    def _bridge_pad(self) -> int | None:
        return self._first_pad()

    def _first_pad(self) -> int | None:
        """Номер геймпада первого моста (self.pad); None — он не подключён."""
        return self._pad_nums.get(self.pad)

    def _cell_of_pad(self, pad: int | None) -> Cell | None:
        slot = self.pad_router.slot_of(pad) if pad is not None else None
        return self.grid.cells[slot] if slot is not None else None

    def _pad_session(self, pad: int | None) -> CameraSession | None:
        c = self._pad_cell(pad)
        return c.session if c else None

    def pad_connected(self, bridge: bool | GamepadBridge = False) -> int:
        """Геймпад подключился: берёт первую свободную камеру. Возвращает его номер 1–4.

        `bridge` — мост физического геймпада (True — первый, self.pad): его вибрация и огоньки идут через него."""
        b = self.pad if bridge is True else (bridge or None)
        pad, moves, fb = self.pad_router.connect()
        if b is not None:
            self.pads[pad] = b  # до обработки движений: _pad_moved/_pad_feedback отличают физический геймпад
            self._pad_nums[b] = pad
        # подключение — не переход: автомат моста сам заперт, если стик отклонён при подключении; второй замок
        # отсюда (поток Qt, позже опроса) запирал бы и стик, отклонённый уже после подключения
        self._pad_apply(moves, fb, connecting=pad)
        self._refresh_service_cells()
        return pad

    def pad_disconnected(self, pad: int) -> None:
        """Геймпад отключился: его камере — нули от его источника."""
        self._pad_apply(self.pad_router.disconnect(pad), [])
        self._pad_vel.pop(pad, None)
        b = self.pads.pop(pad, None)
        if b is not None:
            self._pad_nums.pop(b, None)
        if self._menu_pad == pad:
            self._menu_pad = None
        self._refresh_service_cells()

    def pad_velocity(self, pad: int, pan: float, tilt: float, zoom: float, focus: float = 0.0) -> None:
        """Скорость геймпада — в смеситель его камеры. Нет камеры — игнорируется."""
        self._pad_vel[pad] = (float(pan), float(tilt), float(zoom))
        c = self._cell_of_pad(pad)
        if c is not None:
            c.mixer.set(f"pad{pad}", pan, tilt, zoom, focus)

    def pad_stop(self, pad: int) -> None:
        """Стоп с тачпада: только камера этого геймпада (все её источники), слежение и разрешения агенту."""
        b = self.pads.get(pad)
        if b is not None:
            b.reset_motion()  # автомат заперт до центра; смесителю — нули, чтобы он не защёлкнул источник
            self.pad_velocity(pad, 0.0, 0.0, 0.0)
        c = self._cell_of_pad(pad)
        if c is None:
            return
        self.grid.stop_cell(c)
        if c.session.limit_watch:
            c.session.limit_watch.set_demand(0.0, 0.0, 0.0)
        if self.camera_menu.is_open and self._menu_session() is c.session:
            self.camera_menu.close_menu()  # сначала стоп, потом закрытие
        self.agent_panel.refresh()

    def pad_create(self, pad: int) -> None:
        """Create: геймпад на следующую камеру по кругу. Главный геймпад в виде «1 камера» переключает
        и камеру в программе: видео, пресеты снизу и правую колонку."""
        moves, fb = self.pad_router.create(pad)
        self._pad_apply(moves, fb)
        slot = self.pad_router.slot_of(pad)
        pid = self._pad_id(pad)
        if moves and self.single_view and pid and pid == self.cfg.main_pad and slot is not None:
            self.select_slot(slot, keep_analysis=True)
        self._refresh_service_cells()

    def _pad_id(self, pad: int) -> str | None:
        b = self.pads.get(pad)
        return (b.info or {}).get("id") if b is not None else None

    def set_main_pad(self, pad_id: str | None) -> None:
        """Окно «Геймпады»: главный геймпад (один) или никакого."""
        self.cfg.main_pad = pad_id or ""
        self._save()

    def pad_session(self, pad: int) -> CameraSession | None:
        c = self._cell_of_pad(pad)
        return c.session if c else None

    def _pad_sync_slots(self) -> None:
        """Открытые клетки изменились: маршрутизатор переводит геймпады закрытых."""
        moves, fb = self.pad_router.set_slots([c is not None for c in self.grid.cells])
        self._pad_apply(moves, fb)

    def _pad_apply(self, moves: list[PadMove], fb: list[PadFeedback], connecting: int | None = None) -> None:
        for m in moves:
            self._pad_moved(m, connecting)
        for f in fb:
            self._pad_feedback(f)
        for i, cell in enumerate(self.cells):
            cell.set_pads(self._cell_pads(i))
        if moves:
            self._sync_pad_player()

    def _pad_hold_on_new(self, pad: int, cell: Cell) -> None:
        """Геймпад пришёл к камере со стиком не в центре: она поедет, только когда стик вернётся в центр."""
        b = self.pads.get(pad)
        if b is not None:  # физический: запирает автомат моста (нули в смеситель дойдут сами)
            b.reset_motion()
            b.cancel_holds()
        elif any(self._pad_vel.get(pad, ())):
            cell.mixer.latch(f"pad{pad}")

    def _pad_moved(self, m: PadMove, connecting: int | None = None) -> None:
        old = self.grid.cells[m.old_slot] if m.old_slot is not None else None
        new = self.grid.cells[m.new_slot] if m.new_slot is not None else None
        b = self.pads.get(m.pad)
        if old is not None:
            old.mixer.release(f"pad{m.pad}")  # прежней камере — нули от этого геймпада; остальные источники едут
            if old.session.limit_watch and b is not None:
                old.session.limit_watch.set_demand(0.0, 0.0, 0.0)
        if self.camera_menu.is_open and self._menu_pad == m.pad:
            self.camera_menu.close_menu()
        if new is not None:
            if m.pad != connecting:
                self._pad_hold_on_new(m.pad, new)
        elif b is not None:
            b.reset_motion()

    def _pad_feedback(self, f: PadFeedback) -> None:
        b = self.pads.get(f.pad)
        if b is not None:
            if f.kind == "dots":
                b.set_player(f.value or None)
            elif f.kind == "rumble_taken":
                b.rumble(RUMBLE_TAKEN)
            elif f.kind == "rumble_nowhere":
                b.rumble(RUMBLE_NOWHERE)
        if self.on_pad_feedback is not None:
            self.on_pad_feedback(f)

    def _on_pad_connection(self, info, bridge: GamepadBridge | None = None) -> None:
        b = bridge if bridge is not None else self.pad
        if info is not None:
            self._remember_pad(b, info)
            if b not in self._pad_nums:
                try:
                    self.pad_connected(bridge=b)
                except ValueError as e:
                    log.warning("%s", e)
        else:
            self._pad_disconnect_bridge(b)
        self._sync_pad_player()
        self._update_pad_chip()
        self._refresh_gamepads_dialog()

    def _pad_disconnect_bridge(self, b: GamepadBridge) -> None:
        n = self._pad_nums.get(b)
        if n is not None:
            self.pad_disconnected(n)

    def _on_pad_intents(self, intents: list, pad: int | None = -1, bridge: GamepadBridge | None = None) -> None:
        """Намерения геймпада n (поток Qt; -1 — первый; None — мост без номера): те же пути, что клавиатура и кнопки окна.

        `bridge` — мост-отправитель. Мост без номера (5-й геймпад, хвост после отключения) ничего не делает
        и никогда не действует от имени другого геймпада."""
        if pad == -1:
            pad = self._first_pad()
        if pad is None:
            if bridge is not None and any(isinstance(it, Disconnected) for it in intents):
                bridge.zero_velocity()
            return
        b = bridge if bridge is not None else (self.pads.get(pad) or self.pad)
        if any(_is_action(it) for it in intents):
            self._note_input("pad")
        moved = False
        for it in intents:
            if isinstance(it, (PanTilt, Zoom)):
                moved = True
            elif isinstance(it, Disconnected):
                # отключили: камера стоит — ничего не шлём; едет — нули. Слежение не трогаем
                if self._menu_pad in (None, pad):
                    self.camera_menu.stop_focus()
                b.zero_velocity()
                self._pad_disconnect_bridge(b)  # его камере уходят нули; номер освобождается
                moved = False
            elif isinstance(it, Stop):
                if pad is not None:
                    self.pad_stop(pad)  # только камера этого геймпада; Esc останавливает все
                moved = False
            elif isinstance(it, ToggleFollow):
                if self._pad_on_selected(pad):
                    self.toggle_follow()
            elif isinstance(it, Help):  # кнопка PS: справка — открыть или закрыть (любой геймпад)
                if self.hotkeys_dialog is not None and self.hotkeys_dialog.isVisible():
                    self.hotkeys_dialog.close()
                else:
                    self.show_hotkeys()
            elif isinstance(it, Composition):
                if self._pad_on_selected(pad):
                    self._pad_composition(it.d, pad)
            elif isinstance(it, SpeedStep):
                self._pad_speed_step(b, it.d)  # настройка самого геймпада: выбор клетки не нужен
                moved = moved or any(b.velocity()[:2])  # стик держат — новый предел сразу
            elif isinstance(it, NextCamera):
                if pad is not None:
                    self.pad_create(pad)
            elif isinstance(it, SaveMoment):
                self.save_moment("геймпад")  # запись — всего окна, не камеры
                b.rumble(RUMBLE_PRESET_STORED)
            elif isinstance(it, MenuOpen):
                self._pad_menu_open(pad)
            elif isinstance(it, (MenuNav, MenuAccept, MenuClose)):
                if self._menu_pad not in (None, pad):
                    continue  # меню открыто другим геймпадом
                if isinstance(it, MenuNav):
                    self.camera_menu.nav(it.dx, it.dy)
                elif isinstance(it, MenuAccept):
                    self.camera_menu.accept()
                else:
                    self.camera_menu.close_menu()
            else:
                self._pad_camera_intent(it, pad)
        if moved:
            self._apply_held()
            ps = self._pad_session(pad)
            if ps is not None and ps.limit_watch:
                ps.limit_watch.set_demand(*b.velocity())

    def _pad_rumble(self, pad: int | None, pattern) -> None:
        b = self.pads.get(pad) if pad is not None else None
        (b or self.pad).rumble(pattern)

    def _pad_camera_intent(self, it, pad: int | None) -> None:
        s = self._pad_session(pad)
        if s is None:
            return
        if isinstance(it, Preset):
            slot = self.grid.cells.index(self._pad_cell(pad))
            if it.store:
                s.store_preset(it.n)
                self._capture_thumb_soon(slot, it.n + 1)
                self._pad_rumble(pad, RUMBLE_PRESET_STORED)
            else:
                s.recall_preset(it.n)
                self._after_recall(slot, it.n + 1)
        elif isinstance(it, IrisStep):
            if not s.iris_step(it.d):
                self._pad_rumble(pad, RUMBLE_NOWHERE)
        elif isinstance(it, GainStep):
            step = getattr(s, "gain_step", None)
            if step is None or not step(it.d):
                self._pad_rumble(pad, RUMBLE_NOWHERE)
        elif isinstance(it, Neighbor):
            a = s.analyzer
            if a is None or a.select_neighbor(it.d) is None:
                self._pad_rumble(pad, RUMBLE_NOWHERE)
            else:
                self._update_status()
        elif isinstance(it, PushFocus):
            s.push_focus()

    def _pad_on_selected(self, pad: int | None) -> bool:
        """Слежение и крупность живут в правой панели выбранной камеры: с геймпада — только когда он на ней."""
        c = self._pad_cell(pad)
        ok = c is not None and c is self.selected_cell
        if not ok and c is not None:
            self._pad_rumble(pad, RUMBLE_NOWHERE)
        return ok

    def _pad_menu_open(self, pad: int | None) -> None:
        """Меню Options с геймпада: на его камере, поверх её видео."""
        c = self._pad_cell(pad)
        if c is None or self.camera_menu.is_open:
            return
        self._menu_pad = pad
        self.camera_menu.pad_hints = True
        self._menu_to(self.cells[self.grid.cells.index(c)].video_frame)
        self.camera_menu.open()

    def _pad_stop_motion(self) -> None:
        """Esc и закрытие окна: все геймпады — нули и автомат заперт до центра, опрос упора стоит.
        Смесители камер обнуляет grid.emergency_stop_all / close_all."""
        for b in self._bridges:
            b.reset_motion()
        for c in self.grid.cells:
            if c is not None and c.session.limit_watch:
                c.session.limit_watch.set_demand(0.0, 0.0, 0.0)

    def _pad_composition(self, d: int, pad: int | None) -> None:
        """Только крупность: пояс ↔ рост ↔ общий. Свайп вправо (−1) — шире, к «Общему»; влево (+1) — ближе;
        за краем — «некуда».
        В пояс из роста — на последнюю сторону пояса (слева/центр/справа)."""
        cam = self._current_cfg()
        if cam is None:
            return
        cur = cam.composition if cam.composition in COMPOSITIONS else "waist_center"
        i = _PAD_SIZES.index("waist" if cur.startswith("waist") else cur)
        j = i - d
        if not 0 <= j < len(_PAD_SIZES):
            self._pad_rumble(pad, RUMBLE_NOWHERE)
            return
        self._on_composition(self._last_waist if _PAD_SIZES[j] == "waist" else _PAD_SIZES[j])

    def _chip_bridge(self) -> GamepadBridge | None:
        """Чип показывает первый подключённый геймпад (остальные — в служебной клетке)."""
        return next((b for b in self._bridges if b.connected), None)

    def _update_pad_chip(self) -> None:
        """Сверху — значок каждого подключённого геймпада: номер и заряд; без геймпадов скрыто."""
        live = sorted((n, b) for n, b in self.pads.items() if b.connected)
        for i, badge in enumerate(self.top_pad_badges):
            if i >= len(live):
                badge.hide()
                continue
            n, b = live[i]
            pct, chg = b.battery_percent, bool(b.battery_charging)
            badge.set(n, pct, chg)  # без цифр: SDL даёт заряд грубо; ступень L1/R1 — только в шапке клетки
            slot = self._pad_slot_number(n)
            tip = f"{(b.info or {}).get('name') or 'Геймпад'} {n}"
            tip += f" — камера в клетке {slot} (огоньки на геймпаде)" if slot else " — без камеры"
            if chg:  # SDL при зарядке уровня не даёт
                tip += "\nЗаряжается"
            elif (lvl := battery_level(pct)) is not None:
                rem = b.battery_remaining_s()
                tip += f"\nЗаряд: {lvl[0]}" + ("" if rem is None else f", хватит примерно на {fmt_remaining(rem)}")
            badge.setToolTip(tip + "\nКлик — окно «Геймпады…»")
            badge.show()
        self.top_pads.setVisible(bool(live))
        self._fit_top_bar()

    def _sync_pad_player(self) -> None:
        """Огоньки игрока = номер клетки камеры геймпада; без камеры — погасить."""
        for b in self._bridges:
            b.set_player(self._pad_slot_number(self._pad_nums.get(b)))
        self._pad_limit_binding()
        self._update_pad_chip()

    def _pad_slot_number(self, pad: int | None) -> int | None:
        c = self._pad_cell(pad)
        return self.grid.cells.index(c) + 1 if c is not None else None

    def _sync_pad(self) -> None:
        """Цвет полосы из снимков (сеть не ждём); мост шлёт его, только если цвет изменился."""
        for b in self._bridges:
            if not b.available:
                continue
            n = self._pad_nums.get(b)
            s = self._pad_session(n)
            inp = atem_input_of(s.cfg) if s is not None else 0
            state = self.atem.tally(inp) if self.atem is not None and inp else None
            f = s.follower if s is not None else None
            b.set_status(PadStatus(
                on_program=state == "pgm",
                on_preview=state == "pvw",
                link_ok=bool(s and s.online),
                standby=getattr(s, "power", None) in ("standby", "switching"),
                menu_open=self.camera_menu.is_open and self._menu_session() is s and s is not None,
                following=bool(f and f.enabled),
            ))
