import struct

from ptztracker.tally.atem_protocol import (
    Header,
    ack_packet,
    hello_packet,
    iter_commands,
    parse_header,
    parse_tlin,
)


def _cmd(name: bytes, body: bytes) -> bytes:
    return struct.pack(">HH4s", 8 + len(body), 0, name) + body


def test_hello_bytes():
    assert hello_packet() == bytes(
        [0x10, 0x14, 0x13, 0x37, 0, 0, 0, 0, 0, 0, 0, 0, 0x01, 0, 0, 0, 0, 0, 0, 0]
    )
    assert len(hello_packet()) == 20


def test_ack_bytes():
    assert ack_packet(0x8001, 0x0105) == bytes([0x80, 0x0C, 0x80, 0x01, 0x01, 0x05, 0, 0, 0, 0, 0, 0])


def test_parse_header_roundtrip():
    h = parse_header(ack_packet(0x8001, 7))
    assert h == Header(flags=0x80, length=12, session=0x8001, ack_id=7, packet_id=0)
    h = parse_header(hello_packet())
    assert h == Header(flags=0x10, length=20, session=0x1337, ack_id=0, packet_id=0)
    # длина больше 255: старшие биты в byte0
    payload = bytes(300 - 12)
    data = bytes([0x08 | (300 >> 8), 300 & 0xFF, 0x80, 0x02, 0, 0, 0, 0, 0, 0, 0x00, 0x2A]) + payload
    assert parse_header(data) == Header(flags=0x08, length=300, session=0x8002, ack_id=0, packet_id=42)


def test_parse_header_rejects_short():
    assert parse_header(b"") is None
    assert parse_header(hello_packet()[:11]) is None
    assert parse_header(hello_packet()[:19]) is None  # длина в заголовке не совпадает
    assert parse_header(hello_packet() + b"\x00") is None


def test_parse_tlin():
    assert parse_tlin(bytes([0x00, 0x03, 0x01, 0x02, 0x03])) == (1, 2, 3)
    assert parse_tlin(b"") == ()
    assert parse_tlin(b"\x00") == ()
    assert parse_tlin(bytes([0x00, 0x05, 0x01])) == ()  # N больше, чем байт
    assert parse_tlin(bytes([0x00, 0x02, 0x01, 0x00, 0xFF])) == (1, 0)  # хвост игнорируется


def test_iter_commands_skips_truncated():
    payload = _cmd(b"TlIn", b"\x00\x01\x01") + _cmd(b"Time", b"\x01\x02\x03\x04")
    assert list(iter_commands(payload)) == [("TlIn", b"\x00\x01\x01"), ("Time", b"\x01\x02\x03\x04")]
    truncated = _cmd(b"TlIn", b"\x00\x01\x01") + _cmd(b"Time", b"\x01\x02\x03\x04")[:-2]
    assert list(iter_commands(truncated)) == [("TlIn", b"\x00\x01\x01")]
    # длина меньше заголовка команды — стоп, без зацикливания
    assert list(iter_commands(struct.pack(">HH4s", 0, 0, b"TlIn"))) == []
    assert list(iter_commands(b"\x00\x09\x00")) == []
    assert list(iter_commands(b"")) == []
