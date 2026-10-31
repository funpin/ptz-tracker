"""Pacer (диагностика и мастер калибровки): повтор команд при er2 «камера занята»."""

import time

import pytest

from ptztracker.camera.protocol import CameraError
from ptztracker.diagnostics.probe import Pacer


class _Client:
    """Клиент, который отвечает ошибкой первые n вызовов, потом — «OK»."""

    def __init__(self, n_errors: int, code: str = "er2"):
        self.n_errors, self.code, self.calls = n_errors, code, 0

    def send(self, cmd: str) -> str:
        self.calls += 1
        if self.calls <= self.n_errors:
            raise CameraError(self.code, f"{self.code.upper()}:{cmd}")
        return "OK"


def test_er2_is_retried_until_success():
    client = _Client(3)
    assert Pacer(client).send("#PTS5050") == "OK"
    assert client.calls == 4


def test_er2_forever_fails_within_time_limit():
    client = _Client(10**6)
    t0 = time.monotonic()
    with pytest.raises(CameraError) as ei:
        Pacer(client).send("#PTS5050")
    assert ei.value.code == "er2"
    assert 1.8 <= time.monotonic() - t0 <= 3.0  # ~2 с повторов, не зависание


@pytest.mark.parametrize("code", ["er1", "er3"])
def test_other_errors_are_not_retried(code):
    client = _Client(10**6, code)
    with pytest.raises(CameraError):
        Pacer(client).send("#PTS5050")
    assert client.calls == 1


def test_stop_is_delivered_after_er2():
    client = _Client(2)
    assert Pacer(client).try_send("#PTS5050") == "OK"
