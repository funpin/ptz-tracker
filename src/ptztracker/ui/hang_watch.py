"""Сторож зависаний: окно не отвечает — в файл рядом с журналом пишется, чем занят каждый поток.

Зачем: на площадке программа зависла после подключения геймпада (Windows, 2026-11-13), а в журнале об этом
ничего не осталось. Поток Qt раз в секунду переводит будильник `faulthandler.dump_traceback_later` на HANG_S
вперёд. Будильник — поток на C: ему не нужен GIL, поэтому он срабатывает и тогда, когда поток Qt стоит, и
тогда, когда другой поток застрял в C, не отпустив GIL (так зависал SDL на Mac, DECISIONS). Обычный поток-сторож
на Python во втором случае стоял бы вместе со всеми.

В файле — только имена файлов, строк и функций: значений переменных, адресов камер и паролей в нём нет.
Сторож только наблюдает: не бросает исключений и ничего не останавливает.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import platformdirs
from PySide6.QtCore import QObject, QTimer

from ptztracker import __version__
from ptztracker.config import APP_NAME

log = logging.getLogger(__name__)

FILE_NAME = "ptztracker-hang.log"
HANG_S = 5.0  # окно молчит дольше — запись. Поток Qt не ждёт ни сеть, ни нейросеть: 5 с — уже не «занято»
BEAT_MS = 1000
MAX_BYTES = 1_000_000  # больше — прежний файл уходит в «.1» при запуске


def hang_log_path() -> Path:
    """Рядом с ptztracker.log: оператор присылает оба файла из одной папки."""
    return Path(platformdirs.user_log_dir(APP_NAME, appauthor=False)) / FILE_NAME


class HangWatch(QObject):
    def __init__(self, path: Path, parent: QObject | None = None, hang_s: float = HANG_S,
                 beat_ms: int = BEAT_MS) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self._hang_s, self._beat_ms = hang_s, beat_ms
        self._file = None
        self._last = 0.0
        self._full = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.beat)

    def start(self) -> bool:
        """Открыть файл и взвести будильник. Не вышло — строка в журнал и False: программа работает без сторожа."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                self.path.replace(self.path.with_name(self.path.name + ".1"))
            self._file = open(self.path, "a", encoding="utf-8")  # noqa: SIM115 — открыт, пока работает программа
            self._note(f"=== запуск {datetime.now():%Y-%m-%d %H:%M:%S}, версия {__version__} ===")
            self._last = time.monotonic()
            self._arm()
            self._timer.start(self._beat_ms)
            return True
        except Exception as e:  # noqa: BLE001 — сторож не должен мешать запуску
            log.warning("Сторож зависаний не включился (%s: %s)", type(e).__name__, e)
            self.stop()
            return False

    def _note(self, line: str) -> None:
        self._file.write(line + "\n")
        self._file.flush()  # faulthandler пишет прямо в файл, мимо буфера Python: наши строки — до него

    def _arm(self) -> None:
        faulthandler.dump_traceback_later(self._hang_s, repeat=False, file=self._file)

    def beat(self) -> None:
        """Такт потока Qt: окно живо — будильник переводится вперёд; после зависания — отметка в файле и журнале."""
        try:
            now = time.monotonic()
            gap, self._last = now - self._last, now
            if gap > self._hang_s:
                names = ", ".join(f"{t.ident:#x} = {t.name}" for t in threading.enumerate() if t.ident is not None)
                self._note(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}: окно ожило, не отвечало {gap:.1f} с. Потоки: {names}")
                log.warning("Окно не отвечало %.1f с. Чем были заняты потоки — в файле %s", gap, self.path.name)
                if os.fstat(self._file.fileno()).st_size > MAX_BYTES:  # замирает раз за разом — диск не исписываем
                    self._full = True
                    self._note("--- файл полон: до следующего запуска сторож молчит")
            if not self._full:
                self._arm()
        except Exception:  # noqa: BLE001
            log.debug("Сторож зависаний: такт не прошёл", exc_info=True)

    def stop(self) -> None:
        try:
            self._timer.stop()
            faulthandler.cancel_dump_traceback_later()
            if self._file is not None:
                self._file.close()
                self._file = None
        except Exception:  # noqa: BLE001
            log.debug("Сторож зависаний: остановка с ошибкой", exc_info=True)
