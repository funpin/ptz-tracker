from types import SimpleNamespace

from ptztracker.videohub.protocol import (
    PING,
    BlockReader,
    HubState,
    apply_block,
    hub_input_of,
    hub_output_of,
    route_command,
)

DUMP = (
    "PROTOCOL PREAMBLE:\nVersion: 2.3\n\n"
    "VIDEOHUB DEVICE:\nDevice present: true\nModel name: Blackmagic Smart Videohub 20 x 20\n"
    "Video inputs: 20\nVideo processing units: 0\nVideo outputs: 20\nVideo monitoring outputs: 0\n"
    "Serial ports: 0\n\n"
    "INPUT LABELS:\n0 PTZ 1\n1 PTZ 2\n2 Зал\n\n"
    "OUTPUT LABELS:\n0 Эфир\n4 Монитор режиссёра\n\n"
    "VIDEO OUTPUT ROUTING:\n0 0\n4 2\n\n"
    "VIDEO OUTPUT LOCKS:\n0 U\n4 L\n\n"
    "END PRELUDE:\n\n"
)


def _state(text: str, chunk: int | None = None) -> HubState:
    r = BlockReader()
    data = text.encode()
    blocks = []
    if chunk is None:
        blocks = r.feed(data)
    else:
        for i in range(0, len(data), chunk):
            blocks += r.feed(data[i:i + chunk])
    st = HubState()
    for b in blocks:
        st = apply_block(st, b)
    return st


def test_full_dump_one_based():
    st = _state(DUMP)
    assert st.present is True
    assert st.model == "Blackmagic Smart Videohub 20 x 20"
    assert (st.inputs, st.outputs) == (20, 20)
    assert st.input_labels == {1: "PTZ 1", 2: "PTZ 2", 3: "Зал"}
    assert st.output_labels == {1: "Эфир", 5: "Монитор режиссёра"}
    assert st.routing == {1: 1, 5: 3}
    assert st.locks == {1: "U", 5: "L"}


def test_byte_by_byte_and_crlf_same_result():
    assert _state(DUMP, chunk=1) == _state(DUMP)
    assert _state(DUMP.replace("\n", "\r\n"), chunk=7) == _state(DUMP)


def test_unknown_blocks_and_lines_ignored():
    text = DUMP + "SOMETHING NEW:\nfoo bar\n\nVIDEO OUTPUT ROUTING:\nмусор\n5\n99 x\n1 7\n\n"
    st = _state(text)
    assert st.routing == {1: 1, 2: 8, 5: 3}


def test_update_changes_only_listed():
    st = _state(DUMP + "VIDEO OUTPUT ROUTING:\n4 1\n\n")
    assert st.routing == {1: 1, 5: 2}


def test_device_not_present():
    st = _state("PROTOCOL PREAMBLE:\nVersion: 2.3\n\nVIDEOHUB DEVICE:\nDevice present: false\n\n")
    assert st.present is False
    st = _state("VIDEOHUB DEVICE:\nDevice present: needs_update\n\n")
    assert st.present is False


def test_ack_nak_are_blocks_without_lines():
    blocks = BlockReader().feed(b"ACK\n\nNAK\n\n")
    assert [(b.header, b.lines) for b in blocks] == [("ACK", ()), ("NAK", ())]


def test_partial_block_waits():
    r = BlockReader()
    assert r.feed(b"VIDEO OUTPUT ROUTING:\n4 2\n") == []
    (b,) = r.feed(b"\n")
    assert b.header == "VIDEO OUTPUT ROUTING" and b.lines == ("4 2",)


def test_route_command_zero_based():
    assert route_command(5, 3) == b"VIDEO OUTPUT ROUTING:\n4 2\n\n"
    assert PING == b"PING:\n\n"


def test_numbers_from_config():
    assert hub_input_of(SimpleNamespace(videohub_input=3)) == 3
    for bad in (0, -1, 289, True, "3", None, 2.0):
        assert hub_input_of(SimpleNamespace(videohub_input=bad)) == 0
    assert hub_input_of(object()) == 0
    assert hub_output_of(SimpleNamespace(videohub_output=5)) == 5
    assert hub_output_of(SimpleNamespace(videohub_output=False)) == 0
