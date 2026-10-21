import subprocess
import sys
import time

import pytest
from fake_atem import FakeAtem

from ptztracker.tally.atem import AtemTally, TallySnapshot


def wait_for(cond, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


@pytest.fixture
def atem():
    fake = FakeAtem()
    yield fake
    fake.close()


@pytest.fixture
def make_client(atem):
    clients = []

    def make(**kw):
        c = AtemTally("127.0.0.1", port=atem.port, **kw)
        clients.append(c)
        c.start()
        return c

    yield make
    for c in clients:
        c.stop()


def test_snapshot_state():
    s = TallySnapshot(online=True, flags=(0, 1, 2, 3))
    assert s.state(1) is None
    assert s.state(2) == "pgm"
    assert s.state(3) == "pvw"
    assert s.state(4) == "pgm"  # оба бита — эфир
    assert s.state(0) is None and s.state(5) is None and s.state(-1) is None
    assert TallySnapshot(online=False, flags=()).state(1) is None


def test_connects_and_reads_tally(atem, make_client):
    atem.set_tally((0, 1, 2))
    c = make_client()
    assert wait_for(lambda: c.tally(2) == "pgm")
    assert c.tally(3) == "pvw"
    assert c.tally(1) is None
    assert c.online
    assert c.snapshot() == TallySnapshot(online=True, flags=(0, 1, 2))


def test_acks_server_packets(atem, make_client):
    make_client()
    assert wait_for(lambda: atem.acks >= 3)
    with atem._lock:
        assert set(atem.acked_ids) <= atem.sent_ids


def test_goes_offline_and_reconnects(atem, make_client):
    atem.set_tally((0, 1))
    c = make_client(timeout_s=0.6)
    assert wait_for(lambda: c.tally(2) == "pgm")
    atem.pause()
    assert wait_for(lambda: not c.online, timeout=1.5)
    assert c.tally(2) is None
    assert c.snapshot().flags == ()
    atem.resume()
    assert wait_for(lambda: c.online and c.tally(2) == "pgm")


def test_sends_hello_while_offline(atem, make_client):
    atem.pause()
    make_client(timeout_s=0.6)
    time.sleep(0.1)
    atem.resume()
    # первый hello пропал (пауза) — клиент повторит его через ~1 с
    assert wait_for(lambda: atem.hellos >= 1, timeout=2.0)


def test_survives_garbage(atem, make_client):
    atem.set_tally((2, 1))
    c = make_client()
    assert wait_for(lambda: c.tally(1) == "pvw")
    atem.send_garbage()
    time.sleep(0.3)
    assert c.online
    assert c.tally(1) == "pvw" and c.tally(2) == "pgm"
    atem.set_tally((1, 0))
    assert wait_for(lambda: c.tally(1) == "pgm" and c.tally(2) is None)


def test_on_change_called(atem, make_client):
    seen = []
    atem.set_tally((0, 1))
    c = make_client(on_change=seen.append)
    assert wait_for(lambda: c.tally(2) == "pgm")
    assert wait_for(lambda: seen and seen[-1] == TallySnapshot(online=True, flags=(0, 1)))
    n = len(seen)
    time.sleep(0.5)
    assert len(seen) == n  # без изменений — без вызовов


def test_on_change_exception_swallowed(atem, make_client):
    def boom(_snap):
        raise RuntimeError("упал обработчик")

    atem.set_tally((1,))
    c = make_client(on_change=boom)
    assert wait_for(lambda: c.tally(1) == "pgm")
    atem.set_tally((2,))
    assert wait_for(lambda: c.tally(1) == "pvw")


def test_thread_is_daemon_named(make_client):
    import threading

    make_client()
    t = [t for t in threading.enumerate() if t.name == "atem-tally"]
    assert t and all(x.daemon for x in t)


def test_stop_is_fast(make_client):
    c = make_client()
    time.sleep(0.3)
    t0 = time.monotonic()
    c.stop()
    assert time.monotonic() - t0 < 0.5
    assert not c.online


def test_stop_with_unreachable_host_is_fast():
    c = AtemTally("127.0.0.1", port=9, timeout_s=0.6)  # никто не слушает
    c.start()
    time.sleep(0.3)
    t0 = time.monotonic()
    c.stop()
    assert time.monotonic() - t0 < 0.5
    c.stop()  # повторный stop — без ошибок


def test_no_qt_import():
    code = "import sys, ptztracker.tally, ptztracker.tally.atem; assert 'PySide6' not in sys.modules, 'Qt'"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
