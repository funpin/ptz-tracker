"""Команды Panasonic AW-HE42 (HTTP CGI): построение и разбор.

Формат: http://<host>/cgi-bin/aw_ptz?cmd=%23<CMD>&res=1 — команды с «#»;
остальные (OSD/изображение) идут в aw_cam. Пункты, помеченные ⚠ в дизайне,
проверяются диагностикой на живой камере.

Питание, gain, баланс белого и цветовая температура — по пробе на живой AW-HE40
(Ver.1.54, 2026-10-30, §7 спеки): неверное значение → ER3, в standby → ER2.
"""

from __future__ import annotations

import re
from typing import Literal

STOP_PT = "#PTS5050"
STOP_ZOOM = "#Z50"
STOP_FOCUS = "#F50"
Q_POWER = "#O"
Q_PAN_TILT = "#APC"
Q_ZOOM = "#GZ"
Q_FOCUS = "#GF"
Q_IRIS = "#GI"
Q_MODEL = "QID"
POWER_ON = "#O1"
POWER_STANDBY = "#O0"
Q_GAIN = "QGU"
Q_WB = "QAW"
Q_COLOR_TEMP = "QSD:B1"

# Gain: 08 = 0 dB, шаг 3 dB до 38 (≈48 dB ⚠ не сверено с меню камеры), 80 — авто (AGC)
GAIN_AUTO = 0x80
GAIN_CODES: tuple[int, ...] = tuple(range(0x08, 0x39, 3))
# Баланс белого (OAW): 6–8 камера отвергает (ER3); подписи — по документации Panasonic ⚠
WB_MODES: dict[int, str] = {0: "ATW", 1: "AWB A", 2: "AWB B", 3: "ATW 2", 4: "3200K", 5: "5600K", 9: "VAR"}
WB_VAR = 9
COLOR_TEMP_MAX = 0x4B

LENS_MIN = 0x555
LENS_MAX = 0xFFF


class CameraError(Exception):
    """Камера ответила ошибкой вида «er1:…» (er1 — нет такой команды, er2 — занята, er3 — вне диапазона)."""

    def __init__(self, code: str, body: str):
        hint = " (камера занята пультом или в режиме ожидания)" if code == "er2" else ""
        super().__init__(f"Камера ответила ошибкой {code}: {body}{hint}")
        self.code = code
        self.body = body


def _check_range(name: str, v: int, lo: int, hi: int) -> None:
    if not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{name}={v!r} вне диапазона {lo}..{hi}")


def _speed(code: int) -> str:
    _check_range("code", code, 1, 99)
    return f"{code:02d}"


def pan_tilt_speed(pan: int, tilt: int) -> str:
    return f"#PTS{_speed(pan)}{_speed(tilt)}"


def zoom_speed(code: int) -> str:
    return f"#Z{_speed(code)}"


def zoom_abs(pos: int) -> str:
    _check_range("zoom", pos, LENS_MIN, LENS_MAX)
    return f"#AXZ{pos:03X}"


def pan_tilt_abs(pan: int, tilt: int) -> str:
    _check_range("pan", pan, 0, 0xFFFF)
    _check_range("tilt", tilt, 0, 0xFFFF)
    return f"#APC{pan:04X}{tilt:04X}"


def focus_auto(on: bool) -> str:
    return "#D11" if on else "#D10"


def focus_speed(code: int) -> str:
    return f"#F{_speed(code)}"


def focus_push_auto() -> str:
    return "OSE:69:1"  # ⚠ разовый AF, проверяется диагностикой


def iris_auto(on: bool) -> str:
    return "#D31" if on else "#D30"


def iris_abs(pos: int) -> str:
    _check_range("iris", pos, LENS_MIN, LENS_MAX)
    return f"#AXI{pos:03X}"


def preset_recall(n: int) -> str:
    _check_range("preset", n, 0, 99)
    return f"#R{n:02d}"


def preset_store(n: int) -> str:
    _check_range("preset", n, 0, 99)
    return f"#M{n:02d}"


def cgi_for(cmd: str) -> Literal["aw_ptz", "aw_cam"]:
    return "aw_ptz" if cmd.startswith("#") else "aw_cam"


def build_url(host: str, cmd: str) -> str:
    encoded = cmd.replace("#", "%23")
    return f"http://{host}/cgi-bin/{cgi_for(cmd)}?cmd={encoded}&res=1"


_ERR = re.compile(r"^\s*(er\d*)", re.IGNORECASE)


def check_response(body: str) -> str:
    m = _ERR.match(body)
    if m:
        raise CameraError(m.group(1).lower(), body.strip())
    return body.strip()


def parse_pan_tilt(body: str) -> tuple[int, int]:
    m = re.fullmatch(r"aPC([0-9A-Fa-f]{4})([0-9A-Fa-f]{4})", body.strip())
    if not m:
        raise ValueError(f"Не похоже на ответ положения: {body!r}")
    return int(m.group(1), 16), int(m.group(2), 16)


def parse_zoom(body: str) -> int:
    m = re.fullmatch(r"gz([0-9A-Fa-f]{3})", body.strip())
    if not m:
        raise ValueError(f"Не похоже на ответ зума: {body!r}")
    return int(m.group(1), 16)


def parse_model(body: str) -> str:
    m = re.fullmatch(r"OID:(.+)", body.strip())
    if not m:
        raise ValueError(f"Не похоже на ответ модели: {body!r}")
    return m.group(1).strip()


def parse_power(body: str) -> Literal["on", "standby", "switching", "unknown"]:
    """Ответ на #O. Непонятный ответ — «unknown», чтобы догадка не спрятала настоящую ошибку связи."""
    return {"p1": "on", "p0": "standby", "p3": "switching"}.get(body.strip(), "unknown")


def gain(code: int) -> str:
    if code != GAIN_AUTO and code not in GAIN_CODES:
        raise ValueError(f"gain={code!r}: допустимы {GAIN_CODES[0]:02X}..{GAIN_CODES[-1]:02X} шагом 3 и 80")
    return f"OGU:{code:02X}"


def gain_db(code: int) -> int | None:
    """dB по коду; None — авто."""
    return None if code == GAIN_AUTO else code - GAIN_CODES[0]


def wb_mode(mode: int) -> str:
    if mode not in WB_MODES:
        raise ValueError(f"wb={mode!r}: допустимы {sorted(WB_MODES)}")
    return f"OAW:{mode}"


def color_temp(index: int) -> str:
    _check_range("color_temp", index, 0, COLOR_TEMP_MAX)
    return f"OSD:B1:{index:03X}"


def color_temp_kelvin(index: int) -> int:
    """⚠ Догадка: 2400 K + 100 K на шаг (01A ≈ 5000 K); не сверено с меню камеры."""
    return 2400 + 100 * index


def _parse_hex(pattern: str, body: str, what: str) -> int:
    m = re.fullmatch(pattern, body.strip())
    if not m:
        raise ValueError(f"Не похоже на ответ {what}: {body!r}")
    return int(m.group(1), 16)


def parse_gain(body: str) -> int:
    return _parse_hex(r"OGU:([0-9A-Fa-f]{2})", body, "gain")


def parse_wb(body: str) -> int:
    return _parse_hex(r"OAW:([0-9A-Fa-f])", body, "баланса белого")


def parse_color_temp(body: str) -> int:
    return _parse_hex(r"OSD:B1:([0-9A-Fa-f]{3})", body, "цветовой температуры")


def parse_iris(body: str) -> int:
    """`gi` + 3 hex (+ необязательный флаг авто) -> позиция ириса."""
    return _parse_hex(r"gi([0-9A-Fa-f]{3})[0-9A-Fa-f]?", body, "ириса")


def parse_iris_auto(body: str) -> bool | None:
    """Флаг авто из 4-символьной формы `giXXXf` (1 — авто); в 3-символьной его нет -> None."""
    m = re.fullmatch(r"gi[0-9A-Fa-f]{3}([01])", body.strip())
    return None if m is None else m.group(1) == "1"
