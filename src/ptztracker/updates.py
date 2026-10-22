"""Проверка, не вышла ли новая версия программы (только уведомление, без автообновления).

Без Qt. Одна проверка идёт в фоновом потоке, любая ошибка молча даёт «обновлений нет».
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

RELEASES_URL = "https://api.github.com/repos/funpin/ptz-tracker/releases/latest"
RELEASE_PAGE = "https://github.com/funpin/ptz-tracker/releases/tag/v{version}"
TIMEOUT_S = 5.0
CHECK_INTERVAL_S = 24 * 3600  # не чаще раза в сутки

_VERSION = re.compile(r"^\s*v?(\d+(?:\.\d+)*)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class UpdateInfo:
    version: str  # без префикса «v»
    html_url: str


def parse_version(s) -> tuple[int, ...] | None:
    """«v0.3.0» → (0, 3, 0). Мусор и пре-релизы («0.3.0-rc1») → None."""
    if not isinstance(s, str):
        return None
    m = _VERSION.match(s)
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def is_newer(latest, current) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    pad = lambda t: t + (0,) * (n - len(t))  # noqa: E731
    return pad(a) > pad(b)


def _http_fetch(url: str, current: str) -> dict:
    import httpx

    r = httpx.get(
        url,
        timeout=TIMEOUT_S,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"PTZTracker/{current}"},
        follow_redirects=True,
    )
    r.raise_for_status()
    return r.json()


class UpdateChecker:
    """Одна проверка в фоновом потоке; результат читается из любого потока."""

    def __init__(self, current: str, fetch: Callable[[str], dict] | None = None, url: str = RELEASES_URL):
        self.current = current
        self._url = url
        self._fetch = fetch or (lambda u: _http_fetch(u, current))
        self._result: UpdateInfo | None = None
        self._done = threading.Event()
        self._ok = False
        self._started = False

    @property
    def result(self) -> UpdateInfo | None:
        return self._result

    @property
    def done(self) -> bool:
        """Проверка завершена (успешно или нет)."""
        return self._done.is_set()

    @property
    def ok(self) -> bool:
        """Ответ GitHub получен (даже если новой версии нет): время проверки можно запомнить."""
        return self._ok

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._run, name="update-check", daemon=True).start()

    def _run(self) -> None:
        try:
            data = self._fetch(self._url)
            self._ok = isinstance(data, dict)
            if isinstance(data, dict) and not data.get("draft") and not data.get("prerelease"):
                tag, url = data.get("tag_name"), data.get("html_url")
                if isinstance(url, str) and url.startswith("https://") and is_newer(tag, self.current):
                    self._result = UpdateInfo(str(tag).strip().lstrip("vV"), url)
        except Exception as e:  # офлайн, лимит GitHub, плохой JSON — оператору это не нужно
            log.debug("Проверка обновлений не удалась: %s", e)
        finally:
            self._done.set()
