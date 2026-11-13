import time

import pytest
from fake_hub import FakeHub

from ptztracker.videohub.client import VideohubClient


def wait(cond, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


@pytest.fixture
def hub():
    h = FakeHub()
    yield h
    h.stop()


@pytest.fixture
def client_for():
    made = []

    def make(port, host="127.0.0.1", **kw):
        c = VideohubClient(host, port=port, **kw)
        c.start()
        made.append(c)
        return c

    yield make
    for c in made:
        c.stop()


def test_online_and_snapshot(hub, client_for):
    c = client_for(hub.port)
    assert wait(lambda: c.snapshot().online)
    s = c.snapshot()
    assert s.state.model == "Smart Videohub 20 x 20"
    assert (s.state.inputs, s.state.outputs) == (20, 20)
    assert s.state.input_labels[3] == "Вход 3"
    assert s.state.routing[5] == 1


def test_route_changes_output(hub, client_for):
    c = client_for(hub.port)
    assert wait(lambda: c.snapshot().online)
    assert c.route(5, 3) is True
    assert wait(lambda: hub.input_on(5) == 3)
    assert wait(lambda: c.snapshot().state.routing[5] == 3)
    assert hub.routes_received == [(5, 3)]


def test_route_while_offline_is_dropped(client_for):
    hub = FakeHub()
    port = hub.port
    hub.stop()
    c = client_for(port)
    t0 = time.perf_counter()
    assert c.route(5, 3) is False
    assert time.perf_counter() - t0 < 0.005
    hub = FakeHub(port=port)
    try:
        assert wait(lambda: c.snapshot().online, 5)
        time.sleep(0.3)
        assert hub.routes_received == []
    finally:
        hub.stop()


def test_rapid_switches_end_on_last(hub, client_for):
    c = client_for(hub.port)
    assert wait(lambda: c.snapshot().online)
    for i in (1, 2, 1, 2):
        c.route(5, i)
    assert wait(lambda: c.snapshot().state.routing.get(5) == 2 and hub.input_on(5) == 2)
    time.sleep(0.2)
    assert hub.input_on(5) == 2
    assert hub.routes_received[-1] == (5, 2)


def test_no_route_on_reconnect(hub, client_for):
    c = client_for(hub.port)
    assert wait(lambda: c.snapshot().online)
    c.route(5, 3)
    assert wait(lambda: hub.input_on(5) == 3)
    hub.restart()
    assert wait(lambda: not c.snapshot().online)
    assert wait(lambda: c.snapshot().online, 5)
    hub.routes_received.clear()
    time.sleep(0.3)
    assert hub.routes_received == []


def test_dropped_connection_reconnects(hub, client_for):
    seen = []
    c = client_for(hub.port, on_change=seen.append)
    assert wait(lambda: c.snapshot().online)
    before = len(seen)
    hub.drop_clients()
    # по событиям, а не опросом: «нет связи» длится 0,05 с до новой попытки, и на занятой машине опрос раз в
    # 0,01–0,03 с это окно пропускал (CI macos-15, 2026-11-13)
    assert wait(lambda: any(not s.online for s in seen[before:]))
    assert wait(lambda: c.snapshot().online, 5)
    assert c.route(5, 4)
    assert wait(lambda: hub.input_on(5) == 4)


def test_nak_sets_error_and_success_clears(hub, client_for):
    c = client_for(hub.port)
    assert wait(lambda: c.snapshot().online)
    hub.nak_next = True
    c.route(5, 3)
    assert wait(lambda: c.snapshot().last_error == "nak")
    c.route(5, 4)
    assert wait(lambda: c.snapshot().last_error is None)
    assert hub.input_on(5) == 4


def test_on_change_called(hub, client_for):
    seen = []
    c = client_for(hub.port, on_change=seen.append)
    assert wait(lambda: c.snapshot().online)
    assert wait(lambda: any(s.online for s in seen))


@pytest.mark.slow
def test_silent_hub_goes_offline_and_pings(hub, client_for):
    c = client_for(hub.port)
    assert wait(lambda: c.snapshot().online)
    assert wait(lambda: hub.pings >= 1, 5)  # тишина 3 с — PING
    hub.silent = True
    assert wait(lambda: not c.snapshot().online, 10)


def test_unreachable_address_is_fast():
    t0 = time.perf_counter()
    c = VideohubClient("10.255.255.1", port=9990)
    c.start()
    assert c.route(5, 3) is False
    c.stop()
    assert time.perf_counter() - t0 < 0.6
    assert not c.snapshot().online


def test_empty_host_does_not_start():
    c = VideohubClient("  ")
    c.start()
    assert c._thread is None
    assert c.route(1, 1) is False
    c.stop()


def test_stop_is_quick_and_offline(hub, client_for):
    c = client_for(hub.port)
    assert wait(lambda: c.snapshot().online)
    t0 = time.perf_counter()
    c.stop()
    assert time.perf_counter() - t0 < 0.6
    assert not c.snapshot().online


def test_route_refused_without_device(client_for):
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        c, _ = srv.accept()
        c.sendall(b"PROTOCOL PREAMBLE:\nVersion: 2.3\n\nVIDEOHUB DEVICE:\nDevice present: false\n\n")
        time.sleep(1)
        c.close()

    threading.Thread(target=serve, daemon=True).start()
    c = client_for(srv.getsockname()[1])
    assert wait(lambda: c.snapshot().online)
    assert c.route(1, 1) is False
    srv.close()
