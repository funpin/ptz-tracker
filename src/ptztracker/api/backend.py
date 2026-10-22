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

    # --- чтение (поток сервера) ------------------------------------------

    def status(self) -> dict:
        s = self.w.session  # одна ссылка: окно может сменить сессию в это время
        panel = self.w.agent_panel
        out: dict = {
            "now": time.monotonic(),
            "agent": {"control_allowed": panel.control_allowed, "not_on_air": panel.not_on_air},
            "camera": None,
        }
        if s is None:
            return out
        c = s.cfg
        f, a = s.follower, s.analyzer
        r = a.latest() if a else None
        st = s.frames.stats() if s.frames else None
        fo = f.output if f else None
        out.update({
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
        })
        return redact_meta(out)  # на всякий случай: адреса в тексте ошибок — без пароля

    def events(self, since: float, limit: int) -> dict:
        s = self.w.session
        rec = s.recorder if s else None
        recent = getattr(rec, "recent_events", None)
        return {"now": time.monotonic(), "events": recent(since, limit) if recent else []}

    def frame_jpeg(self) -> bytes | None:
        s = self.w.session
        fr = s.frames.latest() if s and s.frames else None
        if fr is None:
            return None
        ok, buf = cv2.imencode(".jpg", fr.image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        return buf.tobytes() if ok else None

    # --- действия (выполняются в потоке Qt) -------------------------------

    def save_moment(self) -> dict:
        fut = self.invoker.call(lambda: self.w.save_moment("agent"))
        if fut is None:
            raise ApiError(409, "Нечего сохранять: камера не выбрана.")
        try:
            return {"path": str(fut.result(timeout=MOMENT_WAIT_S))}
        except concurrent.futures.TimeoutError:
            return {"saving": True, "message": "Сохраняю запись…"}
        except Exception as e:
            raise ApiError(409, f"Запись не сохранена: {e}") from e

    def control(self, name: str, body: dict) -> dict:
        act = {
            "follow": self._follow_args,
            "target": self._target_args,
            "composition": self._composition_args,
            "effort": self._effort_args,
            "calibrate": lambda b: (self._calibrate, ()),
        }[name]
        fn, args = act(body)  # разбор тела — здесь, в потоке сервера
        # всё, что может двинуть камеру, — только с флажком «НЕ в эфире»; выключить слежение (стоп) можно всегда
        moves = not (name == "follow" and args == (False,))
        return self.invoker.call(lambda: self._gated(fn, args, need_off_air=moves))

    def _gated(self, fn, args, need_off_air: bool) -> dict:
        """Поток Qt: проверка разрешений оператора прямо перед действием."""
        from PySide6.QtWidgets import QApplication

        panel = self.w.agent_panel
        if not panel.control_allowed:
            raise ApiError(403, "Оператор не разрешил управление агенту (флажок в окне программы).")
        if need_off_air and not panel.not_on_air:
            raise ApiError(403, "Оператор не отметил «Камера НЕ в эфире — агент может двигать камеру»: "
                                "агент может только выключить слежение.")
        s = self.w.session
        if s is None or s.follower is None:
            raise ApiError(409, "Камера не выбрана или распознавание недоступно.")
        if s.follower.polling_paused:
            raise ApiError(409, "Идёт калибровка камеры.")
        if QApplication.activeModalWidget() is not None:
            raise ApiError(409, "В программе открыто окно (диалог) — дождитесь, пока оператор его закроет.")
        return fn(*args)

    def _follow_args(self, b: dict):
        on = b.get("on")
        if not isinstance(on, bool):
            raise ApiError(400, 'Нужно {"on": true} или {"on": false}.')
        return self._follow, (on,)

    def _follow(self, on: bool) -> dict:
        following = self.w.request_follow(on)  # тот же путь, что пробел и кнопка: все проверки те же
        s = self.w.session
        f = s.follower
        if on and following and not self.w.agent_panel.control_allowed:
            # раскачка или аварийный стоп сняли разрешение, пока включалось слежение (поток слежения)
            f.disable("разрешение агенту снято")
            raise ApiError(403, "Разрешение агенту снято, пока включалось слежение.")
        if on and not following:
            r = s.analyzer.latest() if s.analyzer else None
            if r is None or r.target_id is None:
                raise ApiError(409, "Слежение не включилось: сначала выберите цель (POST /target).")
            why = f" Последняя причина: {f.last_reason}." if f.last_reason else ""
            raise ApiError(409, f"Слежение не включилось.{why}")
        return {"following": f.enabled, "reason": f.last_reason}

    def _target_args(self, b: dict):
        x, y = b.get("x"), b.get("y")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (x, y)):
            raise ApiError(400, 'Нужно {"x": число, "y": число} — пиксели кадра анализа.')
        return self._target, (float(x), float(y))

    def _target(self, x: float, y: float) -> dict:
        a = self.w.session.analyzer
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

    def _composition(self, key: str) -> dict:
        self.w.apply_composition(key)
        return {"composition": key}

    def _effort_args(self, b: dict):
        v = b.get("value")
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0.0 <= v <= 1.0:
            raise ApiError(400, 'Нужно {"value": число от 0 (мягко) до 1 (резко)}.')
        return self._effort, (float(v),)

    def _effort(self, v: float) -> dict:
        self.w.apply_effort(v)
        return {"effort": v}

    def _calibrate(self) -> dict:
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
