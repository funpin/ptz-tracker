import socket
import time

from fake_hub import FakeHub


def _read_until(s: socket.socket, needle: bytes, timeout: float = 2.0) -> bytes:
    s.settimeout(0.1)
    buf = b""
    end = time.monotonic() + timeout
    while needle not in buf and time.monotonic() < end:
        try:
            buf += s.recv(65536)
        except TimeoutError:
            pass
    return buf


def test_dump_and_route_on_bare_socket():
    hub = FakeHub()
    try:
        s = socket.create_connection(("127.0.0.1", hub.port), timeout=1)
        dump = _read_until(s, b"END PRELUDE:\n\n")
        assert b"Video outputs: 20" in dump
        s.sendall(b"VIDEO OUTPUT ROUTING:\n4 2\n\n")
        resp = _read_until(s, b"4 2\n\n")
        assert resp.startswith(b"ACK\n\n")
        assert hub.input_on(5) == 3
        s.sendall(b"VIDEO OUTPUT ROUTING:\n40 2\n\n")
        assert _read_until(s, b"NAK").startswith(b"NAK\n\n")
        s.close()
    finally:
        hub.stop()
