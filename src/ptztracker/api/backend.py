"""API главного окна: что сервер читает из окна и какие действия выполняет в нём.

Чтение (`status`, `events`, `frame_jpeg`) идёт в потоке сервера и берёт только снимки состояния.
Действия (`save_moment`, `control`) выполняются в потоке Qt через `QtInvoker`: там же, где
пробел, Esc и клики оператора, поэтому проверки разрешений и действия не разъезжаются.
"""

from __future__ import annotations

import concurrent.futures
import logging
import math
import time
from typing import TYPE_CHECKING

import cv2

from ptztracker.api.server import HOST, ApiError, ApiServer, QtInvoker
from ptztracker.config import ensure_api_token
from ptztracker.control.composition import COMPOSITIONS
from ptztracker.recorder.events import redact_meta

if TYPE_CHECKING:
    from ptztracker.ui.main_window import MainWindow

log = logging.getLogger(__name__)

JPEG_QUALITY = 85
MOMENT_WAIT_S = 10.0  # сколько ждать, пока zip допишется; дольше — отвечаем «сохраняю»


class WindowBackend:
    def __init__(self, window: MainWindow, invoker: QtInvoker):
        self.w = window
        self.invoker = invoker
        # слежение, которое включил агент: шлюз камеры → объект Follower; нет записи — включал оператор или выключено
        self._agent_followers: dict = {}
        window.on_operator_follow = self._on_operator_follow
        window.agent_panel.offAirCleared.connect(self._on_off_air_cleared)

    # --- выбор камеры (только чтение) -------------------------------------

    def _slot(self, camera: str | None) -> int | None:
        """Клетка (с нуля) по параметру `camera`: id камеры или номер клетки «1»…«4»; без параметра — выбранная.
        Сначала ищется id, потом номер клетки: камера с id «2» в клетке 1 найдётся по «2» раньше клетки 2.
        Такой камеры нет — 404, и ничего не отправлено."""
        grid = self.w.grid
        if camera is None:
            return grid.selected
        cells = list(grid.cells)
        for i, c in enumerate(cells):
            if c is not None and c.cfg.id == camera:
                return i
        if camera.isascii() and camera.isdecimal() and 1 <= int(camera) <= len(cells) and cells[int(camera) - 1] is not None:
            return int(camera) - 1
        raise ApiError(404, f"Нет такой камеры в сетке: {camera}")

    def _cell(self, camera: str | None):
        slot = self._slot(camera)
        return self.w.grid.cells[slot] if slot is not None else None

    # --- чтение (поток сервера) ------------------------------------------

    @staticmethod
    def _agent_flags(cell) -> dict:
        g = cell.gate if cell else None
        return {"control_allowed": bool(g and g.control_allowed), "not_on_air": bool(g and g.not_on_air)}

    def status(self, camera: str | None = None) -> dict:
        cell = self._cell(camera)  # одна ссылка: окно может сменить клетку в это время
        out: dict = {"now": time.monotonic(), "agent": self._agent_flags(cell), "camera": None}
        if cell is not None:
            out.update(self._cell_fields(cell))
        out["cameras"] = [
            {"slot": i + 1, **self._cell_fields(c), "agent": self._agent_flags(c)}
            for i, c in enumerate(list(self.w.grid.cells)) if c is not None
        ]
        return redact_meta(out)  # на всякий случай: адреса в тексте ошибок — без пароля

    @staticmethod
    def _cell_fields(cell) -> dict:
        s = cell.session
        c = s.cfg
        f, a = s.follower, s.analyzer
        r = a.latest() if a else None
        st = s.frames.stats() if s.frames else None
        fo = f.output if f else None
        return {
            # без логина, пароля и адресов видео: только то, что нужно агенту
            "camera": {"id": c.id, "name": c.name, "ip": c.ip, "kind": c.kind, "stream": c.stream,
                       "calibrated": c.calibration is not None},
            "online": s.online,
            "video": None if st is None else {"connected": st.connected, "width": st.width, "height": st.height,
                                              "fps": round(st.fps, 1), "error": st.error},
            "analysis": None if a is None else {
                "error": a.error,
                "fps": round(r.fps, 1) if r else None,
                "infer_ms": round(r.infer_ms, 1) if r else None,
                "detector": r.detector if r else None,
                "device": r.device if r else None,
                "frame_size": list(r.frame_size) if r else None,
            },
            "following": bool(f and f.enabled),
            "reason": f.last_reason if f else None,
            "calibrating": bool(f and f.polling_paused),
            "target": {"id": r.target_id if r else None, "state": r.target_state if r else "none"},
            "output": None if fo is None else {
                "pan": float(fo.pan), "tilt": float(fo.tilt), "zoom": float(fo.zoom), "state": fo.state,
                "error": [float(v) for v in fo.error],
                "size_error": None if fo.size_error is None else float(fo.size_error),
            },
            "codes": [int(v) for v in f.codes] if f else None,
            "composition": c.composition,
            "effort": c.effort,
            "recording": bool(s.recorder.active),
        }

    def events(self, since: float, limit: int, camera: str | None = None) -> dict:
        cell = self._cell(camera)
        rec = cell.session.recorder if cell else None
        recent = getattr(rec, "recent_events", None)
        return {"now": time.monotonic(), "events": recent(since, limit) if recent else []}

    def frame_jpeg(self, camera: str | None = None) -> bytes | None:
        cell = self._cell(camera)
        s = cell.session if cell else None
        fr = s.frames.latest() if s and s.frames else None
        if fr is None:
            return None
        ok, buf = cv2.imencode(".jpg", fr.image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        return buf.tobytes() if ok else None

    # --- действия (выполняются в потоке Qt) -------------------------------

    def save_moment(self, camera: str | None = None) -> dict:
        """Как F9: сохраняет все открытые камеры; в ответе — запись указанной (или выбранной)."""
        slot = self._slot(camera) if camera is not None else None  # нет такой камеры — 404 до сохранения
        fut = self.invoker.call(lambda: self.w.save_moment("agent", slot))
        if fut is None:
            raise ApiError(409, "Нечего сохранять: камера не выбрана.")
        try:
            return {"path": str(fut.result(timeout=MOMENT_WAIT_S))}
        except concurrent.futures.TimeoutError:
            return {"saving": True, "message": "Сохраняю запись…"}
        except Exception as e:
            raise ApiError(409, f"Запись не сохранена: {e}") from e

    def control(self, name: str, body: dict, camera: str | None = None) -> dict:
        act = {
            "follow": self._follow_args,
            "target": self._target_args,
            "composition": self._composition_args,
            "effort": self._effort_args,
            "calibrate": lambda b: (self._calibrate, ()),
        }[name]
        slot = self._slot(camera)  # нет такой камеры — 404 до разбора тела и до любой команды
        cell = self.w.grid.cells[slot] if slot is not None else None
        fn, args = act(body)  # разбор тела — здесь, в потоке сервера
        # всё, что может двинуть камеру, — только с флажком «НЕ в эфире»; выключить слежение (стоп) можно всегда
        moves = not (name == "follow" and args == (False,))
        return self.invoker.call(lambda: self._gated(cell, fn, args, need_off_air=moves))

    def _gated(self, cell, fn, args, need_off_air: bool) -> dict:
        """Поток Qt: проверка разрешений оператора ЭТОЙ камеры прямо перед действием."""
        from PySide6.QtWidgets import QApplication

        if cell is None:
            raise ApiError(409, "Камера не выбрана или распознавание недоступно.")
        gate = cell.gate
        if not gate.control_allowed:
            raise ApiError(403, "Оператор не разрешил управление агенту (флажок в окне программы).")
        if need_off_air and not gate.not_on_air:
            raise ApiError(403, "Оператор не отметил «Камера НЕ в эфире»: "
                                "агент может только выключить слежение.")
        if cell not in self.w.grid.cells:
            raise ApiError(409, "Камеру закрыли или заменили, пока шёл запрос.")
        if cell.session.follower is None:
            raise ApiError(409, "Камера не выбрана или распознавание недоступно.")
        if cell.session.follower.polling_paused:
            raise ApiError(409, "Идёт калибровка камеры.")
        if QApplication.activeModalWidget() is not None:
            raise ApiError(409, "В программе открыто окно (диалог) — дождитесь, пока оператор его закроет.")
        menu = getattr(self.w, "camera_menu", None)
        if menu is not None and menu.is_open and cell is self.w.selected_cell:
            raise ApiError(409, "В программе открыто меню камеры — дождитесь, пока оператор его закроет.")
        return fn(cell, *args)

    def _follow_args(self, b: dict):
        on = b.get("on")
        if not isinstance(on, bool):
            raise ApiError(400, 'Нужно {"on": true} или {"on": false}.')
        return self._follow, (on,)

    def _follow(self, cell, on: bool) -> dict:
        slot = self.w.grid.cells.index(cell)
        selected = cell is self.w.selected_cell
        following = self.w.request_follow(on) if selected else self.w.request_follow(on, slot)  # тот же путь, что пробел и кнопка: все проверки те же
        s = cell.session
        f = s.follower
        if on and following and not cell.gate.control_allowed:
            # раскачка или аварийный стоп сняли разрешение, пока включалось слежение (поток слежения)
            f.disable("разрешение агенту снято")
            raise ApiError(403, "Разрешение агенту снято, пока включалось слежение.")
        if on and not following:
            r = s.analyzer.latest() if s.analyzer else None
            if r is None or r.target_id is None:
                raise ApiError(409, "Слежение не включилось: сначала выберите цель (POST /target).")
            why = f" Последняя причина: {f.last_reason}." if f.last_reason else ""
            raise ApiError(409, f"Слежение не включилось.{why}")
        if on:
            self._agent_followers[cell.gate] = f
        return {"following": f.enabled, "reason": f.last_reason}

    # --- оператор забирает слежение (поток Qt) ------------------------------

    def _on_operator_follow(self, _on: bool) -> None:
        c = self.w.selected_cell
        if c is not None:
            self._agent_followers.pop(c.gate, None)  # пробел или кнопка оператора: слежение теперь его

    def _on_off_air_cleared(self, gate) -> None:
        """Снят «Камера НЕ в эфире» у камеры: слежение, включённое агентом на ней, выключается сразу."""
        f = self._agent_followers.pop(gate, None)
        cell = next((c for c in self.w.grid.cells if c is not None and c.gate is gate), None)
        if f is not None and cell is not None and cell.session.follower is f and f.enabled:
            f.disable("камера в эфире — агент не ведёт")

    def _target_args(self, b: dict):
        x, y = b.get("x"), b.get("y")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (x, y)):
            raise ApiError(400, 'Нужно {"x": число, "y": число} — пиксели кадра анализа.')
        return self._target, (float(x), float(y))

    def _target(self, cell, x: float, y: float) -> dict:
        a = cell.session.analyzer
        tid = a.select_at(x, y)
        self.w.refresh_status()
        if tid is None:
            raise ApiError(404, "В этой точке никого нет.")
        return {"target_id": tid}

    def _composition_args(self, b: dict):
        key = b.get("key")
        if key not in COMPOSITIONS:
            raise ApiError(400, f"Нужно {{\"key\": ...}}, одно из: {', '.join(COMPOSITIONS)}.")
        return self._composition, (key,)

    def _composition(self, cell, key: str) -> dict:
        self.w.apply_composition(key, self.w.grid.cells.index(cell))
        return {"composition": key}

    def _effort_args(self, b: dict):
        v = b.get("value")
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0.0 <= v <= 1.0:
            raise ApiError(400, 'Нужно {"value": число от 0 (мягко) до 1 (резко)}.')
        return self._effort, (float(v),)

    def _effort(self, cell, v: float) -> dict:
        self.w.apply_effort(v, self.w.grid.cells.index(cell))
        return {"effort": v}

    def _calibrate(self, cell) -> dict:
        # Мастер калибровки — модальное окно (exec), и камеру в нём двигает оператор своим флажком
        # «НЕ в эфире» и кнопкой «Начать». Запуск exec() из API вложил бы цикл событий в обработку
        # запроса, поэтому API не открывает мастер, а просит оператора.
        raise ApiError(501, "Запустите калибровку в окне: Сервис → Калибровка камеры.")


def start_window_api(window: MainWindow) -> ApiServer | None:
    """Запустить API окна. Порт занят или API выключен — окно работает дальше без него."""
    cfg, panel = window.cfg, window.agent_panel
    if not cfg.api_enabled:
        panel.set_api_status("API для агента выключен в настройках.")
        return None
    if ensure_api_token(cfg):
        window.save_config()
    srv = ApiServer(WindowBackend(window, QtInvoker(window)), cfg.api_token, cfg.api_port)
    try:
        srv.start()
    except OSError as e:
        msg = f"API для агента не запущен: порт {cfg.api_port} занят"
        log.warning("%s (%s)", msg, e)
        panel.set_api_status(msg, error=True)
        window.statusBar().showMessage(msg, 15000)
        return None
    panel.set_api_status(f"API: http://{HOST}:{srv.port} · токен — api_token в config.json")
    return srv
