import logging
import logging.handlers
import sys
import threading
from pathlib import Path

import platformdirs

from ptztracker.config import APP_NAME


def setup_logging(level: int = logging.INFO) -> Path:
    """Лог в файл с ротацией (5 × 1 МБ) и в консоль. Возвращает путь к файлу лога."""
    log_dir = Path(platformdirs.user_log_dir(APP_NAME, appauthor=False))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ptztracker.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    return log_file


def install_error_hooks() -> None:
    """Необработанные ошибки — в журнал. У собранной программы на Windows нет консоли, а Qt печатает ошибку
    обработчика кнопки только в stderr: кнопка молча не срабатывала, в журнале не оставалось ничего (2026-11-20)."""
    err = logging.getLogger("ptztracker.errors")
    prev_sys, prev_thread = sys.excepthook, threading.excepthook

    def on_error(etype, value, tb) -> None:
        try:
            err.error("Необработанная ошибка: %s: %s", etype.__name__, value, exc_info=(etype, value, tb))
        except Exception:
            pass
        if sys.stderr is not None:
            prev_sys(etype, value, tb)

    def on_thread_error(args) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        try:
            err.error("Необработанная ошибка в потоке %s: %s: %s", name, args.exc_type.__name__, args.exc_value,
                      exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        except Exception:
            pass
        if sys.stderr is not None:
            prev_thread(args)

    sys.excepthook = on_error
    threading.excepthook = on_thread_error
