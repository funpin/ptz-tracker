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


def test_snapshot_known():
    s = TallySnapshot(online=True, flags=(0, 1, 2))
    assert s.known(1) and s.known(3)
    assert not s.known(0) and not s.known(4) and not s.known(-1)
    assert not TallySnapshot(online=False, flags=(0, 1, 2)).known(1)  # нет связи — неизвестно


def _with_snapshot(snap):
    c = AtemTally("192.0.2.1")
    c._snap = snap
    return c


def test_safe_to_move():
    assert not AtemTally("192.0.2.1").safe_to_move(1)  # нет связи
    c = _with_snapshot(TallySnapshot(online=True, flags=(0, 1, 2, 3)))
    assert c.safe_to_move(1)  # не в эфире, состояние известно
    assert c.safe_to_move(3)  # превью
    assert not c.safe_to_move(2)  # эфир
    assert not c.safe_to_move(4)  # оба бита — эфир
    assert not c.safe_to_move(5)  # вход вне числа флагов — неизвестно
    assert not c.safe_to_move(0)
    assert c.tally(5) is None  # tally() не изменился: None и для «неизвестно»
    assert not _with_snapshot(TallySnapshot(online=False, flags=(0,))).safe_to_move(1)


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
    assert wait_for(lambda: atem.acked_count >= 3)
    assert atem.unknown_acks == 0
    assert atem.hello_acked


def test_not_online_before_first_tlin(make_client):
    fake = FakeAtem(dump_on_hello=False)
    try:
        fake.set_tally((0, 1))
        c = AtemTally("127.0.0.1", port=fake.port, timeout_s=0.6)
        c.start()
        try:
            # hello принят, keepalive идут и подтверждаются, но Tally ещё неизвестен
            assert wait_for(lambda: fake.hello_acked and fake.acked_count >= 3)
            assert not c.online
            assert c.tally(2) is None
            fake.set_tally((0, 1))  # первый TlIn
            assert wait_for(lambda: c.online and c.tally(2) == "pgm")
        finally:
            c.stop()
    finally:
        fake.close()


def test_resyncs_after_pause_without_tally_change(atem, make_client):
    atem.set_tally((0, 1))
    c = make_client(timeout_s=0.6)
    assert wait_for(lambda: c.tally(2) == "pgm")
    atem.pause()
    assert wait_for(lambda: not c.online, timeout=1.5)
    hellos = atem.hellos
    atem.resume()  # Tally не менялся: TlIn придёт только в ответ на новый hello
    assert wait_for(lambda: c.online and c.tally(2) == "pgm", timeout=2.0)
    assert atem.hellos > hellos
    assert atem.hello_addrs[-1][1] != atem.hello_addrs[0][1]  # новая сессия — с нового порта


@pytest.mark.parametrize("resend", [False, True])
def test_stale_tlin_does_not_roll_back(atem, make_client, resend):
    atem.set_tally((0, 0))
    c = make_client()
    assert wait_for(lambda: c.online)
    old = atem.last_id
    atem.set_tally((0, 1))  # вход 2 в эфире, пакет новее
    assert wait_for(lambda: c.tally(2) == "pgm")
    acked = atem.acked_count
    atem.send_tlin_raw((0, 0), old, resend=resend)  # запоздалый старый TlIn
    assert wait_for(lambda: atem.acked_ids.count(old) >= 2)  # подтверждён, но не применён
    time.sleep(0.1)
    assert c.tally(2) == "pgm"
    assert atem.acked_count > acked
    atem.set_tally((0, 0))  # свежий — применяется
    assert wait_for(lambda: c.tally(2) is None and c.online)


def test_bad_host_does_not_kill_thread():
    for host in (None, "", "   "):
        c = AtemTally(host)
        c.start()
        assert not c.online
        c.stop()
    c = AtemTally(5, port=9, timeout_s=0.6)  # str(5) — кривой адрес, поток жив
    c.start()
    time.sleep(0.3)
    assert c._thread is not None and c._thread.is_alive()
    t0 = time.monotonic()
    c.stop()
    assert time.monotonic() - t0 < 0.5


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


class _NullSock:
    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(data)


def _tlin_packet(packet_id, flags, resend=False):
    from fake_atem import SERVER_SESSION, _packet, _tlin

    return _packet(0x08 | (0x20 if resend else 0), SERVER_SESSION, 0, packet_id, _tlin(flags))


def test_handle_orders_tlin_across_wraparound():
    from ptztracker.tally.atem import _RunState

    c, sock, st = AtemTally("192.0.2.1"), _NullSock(), _RunState()
    c._handle(sock, _tlin_packet(0x7FFE, (0, 0)), st)
    c._handle(sock, _tlin_packet(0x7FFF, (0, 1)), st)
    assert c.tally(2) == "pgm"
    c._handle(sock, _tlin_packet(0x0000, (0, 2)), st)  # после переноса — новее
    assert c.tally(2) == "pvw"
    c._handle(sock, _tlin_packet(0x7FFF, (0, 1), resend=True), st)  # повтор до переноса — старый
    c._handle(sock, _tlin_packet(0x7FFE, (0, 0)), st)
    assert c.tally(2) == "pvw"
    assert len(sock.sent) == 5  # подтверждён каждый пакет
    assert st.last_tlin_id == 0


def test_stopped_run_cannot_publish_online():
    import threading

    from ptztracker.tally.atem import _RunState

    c = AtemTally("192.0.2.1")
    stop = threading.Event()
    st = _RunState(stop=stop)
    stop.set()  # поток уже остановлен, но пакет ещё дошёл
    c._handle(_NullSock(), _tlin_packet(5, (1,)), st)
    assert not c.online and c.tally(1) is None
