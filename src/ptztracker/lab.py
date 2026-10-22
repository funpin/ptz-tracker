"""Команды без окна (--lab): запись, слежение и калибровка для агента на Mac.

Камера двигается только с флагом --not-on-air. Пароль берётся из системного хранилища (по камере из
настроек) или из переменной PTZ_PASSWORD — в аргументах и выводе его нет.
В конце всегда строка `RESULT {json}`, которую разбирает агент.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path

from ptztracker.camera.session import CameraSession
from ptztracker.config import (
    AppConfig,
    CameraConfig,
    ConfigStore,
    default_config_path,
    get_password,
    record_dir,
)
from ptztracker.recorder import NullRecorder
from ptztracker.video.rtsp import redact_url

MOVING = ("follow", "calibrate")
SELECT_WAIT_S = 5.0  # сколько ждать, пока распознавание найдёт человека в точке --select
SAVE_WAIT_S = 30.0
EXIT_BAD_ARGS, EXIT_NOT_ON_AIR, EXIT_NO_PERSON, EXIT_INTERRUPTED = 2, 3, 4, 130

NOT_ON_AIR_TEXT = (
    "Команда двигает камеру, поэтому нужен флаг --not-on-air: он подтверждает, что камера НЕ в эфире. "
    "Ничего не запущено, к камере я не подключался."
)


def _sleep(seconds: float) -> None:  # отдельная функция — тесты подменяют её (Ctrl+C, ускорение)
    time.sleep(seconds)


_secret: str | None = None  # пароль этого запуска: вырезается из любого вывода


def _scrub(text: str) -> str:
    text = redact_url(text)
    return text.replace(_secret, "***") if _secret else text


def _say(msg: str) -> None:
    print(_scrub(msg), flush=True)


def _result(**data) -> None:
    print(_scrub("RESULT " + json.dumps(data, ensure_ascii=False, default=str)), flush=True)


def _parse_select(text: str | None) -> tuple[float, float] | None:
    if text is None:
        return None
    x, y = (float(p) for p in text.split(","))
    return x, y


def _resolve_camera(ip: str, user: str | None) -> tuple[CameraConfig, AppConfig, str | None]:
    """Камера, настройки записи и пароль (None — без пароля или из системного хранилища сессии)."""
    if ip == "sim":
        return CameraConfig(id="sim", name="Симулятор", ip="sim", kind="sim"), AppConfig(), None
    app_cfg = ConfigStore(default_config_path()).load()
    cam = next((c for c in app_cfg.cameras if c.ip == ip and c.kind != "sim"), None)
    if cam is None:
        cam = CameraConfig(id=f"lab-{ip}", name=ip, ip=ip, username=user)
    password = get_password(cam.id) if cam.username and cam.id in {c.id for c in app_cfg.cameras} else None
    if password is None and cam.username:
        password = os.environ.get("PTZ_PASSWORD")
    return cam, app_cfg, password


def _person_at(session: CameraSession, xy: tuple[float, float]) -> bool:
    r = session.analyzer.latest() if session.analyzer else None
    return bool(r) and any(t.box[0] <= xy[0] <= t.box[2] and t.box[1] <= xy[1] <= t.box[3] for t in r.tracks)


def _select_person(session: CameraSession, xy: tuple[float, float]) -> int | None:
    """Ждёт, пока анализ увидит человека в точке, и щёлкает один раз (каждый щелчок попадает в запись)."""
    deadline = time.monotonic() + SELECT_WAIT_S
    while not _person_at(session, xy):
        if time.monotonic() >= deadline:
            return None
        _sleep(0.2)
    return session.analyzer.select_at(*xy)


def _wait(seconds: float) -> None:
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        _sleep(min(0.25, left))


def _save(session: CameraSession, reason: str) -> Path | None:
    try:
        return Path(session.save_moment(reason).result(timeout=SAVE_WAIT_S))
    except Exception as e:
        _say(f"Запись не сохранена: {e}")
        return None


def _emergency(session: CameraSession) -> None:
    try:
        session.emergency_stop()
        session.scheduler.wait_emergency(2.0)
    except Exception as e:
        _say(f"Аварийный стоп не удался: {e}")


def _bad(cmd: str, code: int, error: str) -> int:
    _result(command=cmd, error=error, exit_code=code)
    return code


def run_lab(args) -> int:
    cmd = args.lab
    if cmd in MOVING and not args.not_on_air:
        _say(NOT_ON_AIR_TEXT)
        return _bad(cmd, EXIT_NOT_ON_AIR, "not_on_air")
    if not args.ip:
        _say("Укажите IP камеры: --ip 10.0.0.12 (или --ip sim для симулятора).")
        return _bad(cmd, EXIT_BAD_ARGS, "bad_args")
    from ptztracker.control.composition import COMPOSITIONS
    from ptztracker.control.effort import EffortProfile

    try:
        select = _parse_select(args.select)
    except ValueError:
        _say("Не понял --select: нужно два числа через запятую, например 320,240.")
        return _bad(cmd, EXIT_BAD_ARGS, "bad_args")
    if args.composition is not None and args.composition not in COMPOSITIONS:
        _say(f"Нет такой композиции: {args.composition}. Есть: {', '.join(COMPOSITIONS)}.")
        return _bad(cmd, EXIT_BAD_ARGS, "bad_args")
    if args.effort is not None and not 0.0 <= args.effort <= 1.0:
        _say("--effort должен быть от 0 до 1.")
        return _bad(cmd, EXIT_BAD_ARGS, "bad_args")
    if args.seconds <= 0:
        _say("--seconds должно быть больше нуля.")
        return _bad(cmd, EXIT_BAD_ARGS, "bad_args")
    if cmd == "follow" and select is None:
        _say("Для слежения укажите, кого вести: --select x,y (пиксели кадра анализа).")
        return _bad(cmd, EXIT_BAD_ARGS, "bad_args")

    global _secret
    cam, app_cfg, password = _resolve_camera(args.ip, args.user)
    _secret = password or None
    app_cfg.record_enabled = True
    if args.out:
        app_cfg.record_dir = str(Path(args.out).expanduser())
    _say(f"Папка записей: {record_dir(app_cfg)}")

    session = CameraSession(cam, app_cfg=app_cfg, password=password)
    data: dict = {"command": cmd, "ip": args.ip}
    code = 0
    try:
        _say("Подключаюсь к камере…")
        session.start()
        if select is not None:
            _say(f"Выбираю человека в точке {select[0]:.0f},{select[1]:.0f}…")
            if _select_person(session, select) is None:
                _say(f"В точке {select[0]:.0f},{select[1]:.0f} человека не нашлось за {SELECT_WAIT_S:.0f} с.")
                data["error"] = "no_person"
                return _finish(session, data, EXIT_NO_PERSON)
        if cmd == "record":
            _say(f"Пишу {args.seconds:.0f} с, камера не двигается…")
            _wait(args.seconds)
        elif cmd == "follow":
            code = _follow(session, args, data, COMPOSITIONS, EffortProfile)
        else:
            code = _calibrate(session, args, cam, data)
    except KeyboardInterrupt:
        _say("Прервано (Ctrl+C): аварийный стоп.")
        _emergency(session)
        data["interrupted"] = True
        return _finish(session, data, EXIT_INTERRUPTED, stopped=True)
    except Exception as e:
        _say(f"Ошибка: {type(e).__name__}: {e}")
        data["error"] = str(e)
        return _finish(session, data, 1)
    return _finish(session, data, code)


def _finish(session: CameraSession, data: dict, code: int, stopped: bool = False) -> int:
    """Стоп, запись в zip (если есть что сохранять), остановка сессии, итоговая строка RESULT."""
    try:
        if not stopped and session.scheduler:
            _emergency(session)
        if "zip" not in data and not isinstance(session.recorder, NullRecorder):
            path = _save(session, "lab")
            if path:
                data["zip"] = str(path)
                _say(f"Запись сохранена: {path}")
    finally:
        session.stop()
    data["exit_code"] = code
    _result(**data)
    return code


def _follow(session, args, data, compositions, effort_profile) -> int:
    f = session.follower
    if args.composition is not None:
        f.set_composition(compositions[args.composition])
    if args.effort is not None:
        f.set_effort(effort_profile.at(args.effort))
    if not f.enable():
        _say("Слежение не включилось: нет цели.")
        data["error"] = "no_target"
        return EXIT_NO_PERSON
    _say(f"Слежу {args.seconds:.0f} с…")
    _wait(args.seconds)
    f.disable("конец --lab")
    _emergency(session)
    path = _save(session, "lab-follow")
    if path is None:
        return 1
    data["zip"] = str(path)
    _say(f"Запись сохранена: {path}")
    from ptztracker.recorder.reader import Episode
    from ptztracker.recorder.replay import replay_controller

    try:
        summary = replay_controller(Episode.open(path)).summary()
    except Exception as e:
        _say(f"Повтор записи не удался: {e}")
        return 1
    _say(summary.text())
    data["summary"] = dataclasses.asdict(summary)
    return 0


def _calibrate(session, args, cam, data) -> int:
    from ptztracker.control.calibration import CalibrationRunner

    if session.follower:
        session.follower.disable("калибровка")
    session.quiet_for_calibration()  # опросы положения и пульс сбивают команды мастера (камера отвечает «занята»)
    _say("Калибровка: камера будет двигаться 1–2 минуты и вернётся на место.")
    runner = CalibrationRunner(session.client.send, session.frames, _say, stream_kind=session.stream_kind())
    calib = runner.run().to_json()
    data["calibration"] = calib
    _say("Калибровка готова: " + json.dumps(calib, ensure_ascii=False))
    if args.save:
        store = ConfigStore(default_config_path())
        cfg = store.load()
        target = next((c for c in cfg.cameras if c.ip == cam.ip and c.kind != "sim"), None)
        if target is None:
            _say("Камеры нет в настройках программы — калибровку не сохраняю (она в RESULT).")
        else:
            target.calibration = calib
            store.save(cfg)
            data["saved"] = True
            _say(f"Калибровка записана в настройки камеры «{target.name}».")
    return 0
