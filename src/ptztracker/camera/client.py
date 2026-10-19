"""HTTP-клиент камеры Panasonic. Каждый запрос — новое соединение (Keep-Alive камера не поддерживает)."""

from __future__ import annotations

import logging

import httpx

from ptztracker.camera.protocol import build_url, check_response

log = logging.getLogger(__name__)


class CameraUnreachable(Exception):
    """Камера не отвечает: нет сети, неверный IP, выключена."""


class AuthRequired(Exception):
    """Камера требует логин/пароль (или они неверны)."""


class CameraClient:
    def __init__(
        self,
        host: str,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.host = host
        auth = httpx.DigestAuth(username, password or "") if username else None
        self._http = httpx.Client(
            timeout=httpx.Timeout(timeout),
            auth=auth,
            headers={"Connection": "close"},
            transport=transport,
            limits=httpx.Limits(max_keepalive_connections=0),
        )

    def send(self, cmd: str) -> str:
        url = build_url(self.host, cmd)
        try:
            r = self._http.get(url)
        except httpx.HTTPError as e:
            raise CameraUnreachable(f"{self.host}: {type(e).__name__}: {e}") from e
        if r.status_code == 401:
            raise AuthRequired(f"{self.host}: требуется авторизация")
        if r.status_code != 200:
            raise CameraUnreachable(f"{self.host}: HTTP {r.status_code}")
        return check_response(r.text)

    def close(self) -> None:
        self._http.close()
