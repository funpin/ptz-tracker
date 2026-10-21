"""Байты протокола Blackmagic ATEM (UDP 9910): заголовок, hello, подтверждения, команда TlIn.

Собрано по открытым описаниям (Skaarhoj, libqatem, PyATEMMax); на живом ATEM не проверено.
Чистые функции, без сети и без Qt.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass

HEADER_LEN = 12
CMD_HEADER_LEN = 8

FLAG_ACK_REQUEST = 0x08  # микшер ждёт подтверждения
FLAG_HELLO = 0x10
FLAG_RESEND = 0x20
FLAG_ACK = 0x80

HELLO_SESSION = 0x1337
HELLO_CONNECT = 0x01  # byte12 в hello клиента
HELLO_ACCEPTED = 0x02  # byte12 в ответе микшера

TALLY_PGM = 0x01
TALLY_PVW = 0x02


@dataclass(frozen=True)
class Header:
    flags: int  # старшие 5 бит byte0
    length: int  # длина всего пакета вместе с заголовком
    session: int
    ack_id: int  # номер подтверждаемого пакета (байты 4–5)
    packet_id: int  # номер этого пакета (байты 10–11)


def _packet(flags: int, session: int, ack_id: int, packet_id: int, payload: bytes = b"") -> bytes:
    length = HEADER_LEN + len(payload)
    return (
        bytes([flags | ((length >> 8) & 0x07), length & 0xFF])
        + struct.pack(">HH4xH", session & 0xFFFF, ack_id & 0xFFFF, packet_id & 0xFFFF)
        + payload
    )


def hello_packet(session: int = HELLO_SESSION) -> bytes:
    """Hello клиента: 20 байт, byte12 = 0x01."""
    return _packet(FLAG_HELLO, session, 0, 0, bytes([HELLO_CONNECT]) + bytes(7))


def ack_packet(session: int, packet_id: int) -> bytes:
    """Подтверждение пакета packet_id (12 байт, свой номер 0)."""
    return _packet(FLAG_ACK, session, packet_id, 0)


def parse_header(data: bytes) -> Header | None:
    """Заголовок пакета; None, если короче 12 байт или длина не совпадает с заявленной."""
    if len(data) < HEADER_LEN:
        return None
    length = ((data[0] & 0x07) << 8) | data[1]
    if length != len(data):
        return None
    session, ack_id, packet_id = struct.unpack_from(">HH4xH", data, 2)
    return Header(flags=data[0] & 0xF8, length=length, session=session, ack_id=ack_id, packet_id=packet_id)


def iter_commands(payload: bytes) -> Iterator[tuple[str, bytes]]:
    """Команды тела пакета: (имя, данные). На обрезанной или кривой команде — стоп."""
    pos = 0
    while pos + CMD_HEADER_LEN <= len(payload):
        (length,) = struct.unpack_from(">H", payload, pos)
        if length < CMD_HEADER_LEN or pos + length > len(payload):
            return
        name = payload[pos + 4 : pos + 8].decode("latin-1")
        yield name, bytes(payload[pos + CMD_HEADER_LEN : pos + length])
        pos += length


def parse_tlin(body: bytes) -> tuple[int, ...]:
    """Флаги Tally по входам из TlIn: элемент i — вход i+1 (бит 0 — PGM, бит 1 — PVW)."""
    if len(body) < 2:
        return ()
    (n,) = struct.unpack_from(">H", body, 0)
    if len(body) < 2 + n:
        return ()
    return tuple(body[2 : 2 + n])
