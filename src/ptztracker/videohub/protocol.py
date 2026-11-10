"""Текстовый протокол Blackmagic Videohub Ethernet (v2.3, TCP 9990) без сети и без Qt.

Блоки: заголовок заглавными с двоеточием, строки, пустая строка в конце. Порты в протоколе — с 0;
здесь наружу всё с 1, как на корпусе хаба. Неизвестные блоки и строки пропускаются (так велит описание
протокола; у части прошивок есть, например, «END PRELUDE:»).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

MAX_PORTS = 288  # самый большой хаб — Universal Videohub 288
PING = b"PING:\n\n"


@dataclass(frozen=True)
class Block:
    header: str  # без двоеточия: «VIDEO OUTPUT ROUTING», «ACK», «NAK»
    lines: tuple[str, ...]


@dataclass(frozen=True)
class HubState:
    """Что известно о хабе по его сообщениям. Номера портов — с 1."""

    present: bool | None = None  # «Device present»: None — ещё не сообщил
    model: str = ""
    inputs: int = 0
    outputs: int = 0
    input_labels: dict = field(default_factory=dict)  # {вход: подпись}
    output_labels: dict = field(default_factory=dict)  # {выход: подпись}
    routing: dict = field(default_factory=dict)  # {выход: вход}
    locks: dict = field(default_factory=dict)  # {выход: "O" | "L" | "U"}

    def __hash__(self) -> int:  # словари внутри: сравниваем по значению, хешируем грубо
        return hash((self.present, self.model, self.inputs, self.outputs))


class BlockReader:
    """Режет поток байтов на блоки; кусок посреди блока ждёт продолжения."""

    def __init__(self) -> None:
        self._buf = b""
        self._lines: list[str] = []

    def feed(self, data: bytes) -> list[Block]:
        self._buf += data
        out: list[Block] = []
        while True:
            i = self._buf.find(b"\n")
            if i < 0:
                break
            raw, self._buf = self._buf[:i], self._buf[i + 1:]
            line = raw.decode("utf-8", errors="replace").rstrip("\r").strip()
            if line:
                self._lines.append(line)
            elif self._lines:
                head, *rest = self._lines
                self._lines = []
                out.append(Block(head.rstrip(":").strip(), tuple(rest)))
        if len(self._buf) > 65536:  # строки без конца — мусор, не копим бесконечно
            self._buf = b""
        return out


def _pairs(lines: tuple[str, ...]):
    """Строки «номер значение» → (номер с 1, значение); прочее пропускается."""
    for ln in lines:
        num, _, rest = ln.partition(" ")
        if not num.isdigit() or not rest.strip():
            continue
        yield int(num) + 1, rest.strip()


def _int(v: str) -> int:
    try:
        return max(0, int(v))
    except ValueError:
        return 0


def apply_block(st: HubState, b: Block) -> HubState:
    """Новое состояние после блока хаба; блоки-ответы и чужие блоки состояние не меняют."""
    h = b.header
    if h == "VIDEOHUB DEVICE":
        kv = {}
        for ln in b.lines:
            k, _, v = ln.partition(":")
            kv[k.strip().lower()] = v.strip()
        present = kv.get("device present", "").lower() == "true"
        if not present:
            return HubState(present=False)
        return replace(st, present=True, model=kv.get("model name", st.model),
                       inputs=_int(kv.get("video inputs", str(st.inputs))),
                       outputs=_int(kv.get("video outputs", str(st.outputs))))
    if h == "INPUT LABELS":
        return replace(st, input_labels={**st.input_labels, **dict(_pairs(b.lines))})
    if h == "OUTPUT LABELS":
        return replace(st, output_labels={**st.output_labels, **dict(_pairs(b.lines))})
    if h == "VIDEO OUTPUT ROUTING":
        upd = {o: int(v) + 1 for o, v in _pairs(b.lines) if v.isdigit()}
        return replace(st, routing={**st.routing, **upd})
    if h == "VIDEO OUTPUT LOCKS":
        upd = {o: v for o, v in _pairs(b.lines) if v in ("O", "L", "U")}
        return replace(st, locks={**st.locks, **upd})
    return st


def route_command(output: int, input: int) -> bytes:
    """«На выход output подать вход input» (оба с 1) → блок протокола (с 0)."""
    return f"VIDEO OUTPUT ROUTING:\n{output - 1} {input - 1}\n\n".encode()


def _port(v) -> int:
    if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= MAX_PORTS:
        return v
    return 0


def hub_input_of(cam) -> int:
    """Вход хаба, на который заведена камера (1…288); прочее — 0, «не переключать»."""
    return _port(getattr(cam, "videohub_input", 0))


def hub_output_of(cfg) -> int:
    """Выход хаба, который идёт вслед за камерой (1…288); прочее — 0, «не задан»."""
    return _port(getattr(cfg, "videohub_output", 0))
