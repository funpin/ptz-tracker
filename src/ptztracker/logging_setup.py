import logging
import logging.handlers
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
