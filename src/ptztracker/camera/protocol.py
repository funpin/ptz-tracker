"""Команды Panasonic AW-HE42 (HTTP CGI): построение и разбор.

Формат: http://<host>/cgi-bin/aw_ptz?cmd=%23<CMD>&res=1 — команды с «#»;
остальные (OSD/изображение) идут в aw_cam. Пункты, помеченные ⚠ в дизайне,
проверяются диагностикой на живой камере.
"""

from __future__ import annotations

import re
from typing import Literal

STOP_PT = "#PTS5050"
STOP_ZOOM = "#Z50"
Q_POWER = "#O"
Q_PAN_TILT = "#APC"
Q_ZOOM = "#GZ"
Q_FOCUS = "#GF"
Q_IRIS = "#GI"
Q_MODEL = "QID"

LENS_MIN = 0x555
LENS_MAX = 0xFFF


class CameraError(Exception):
    """Камера ответила ошибкой вида «er1:…» (er1 — нет такой команды, er2 — занята, er3 — вне диапазона)."""

    def __init__(self, code: str, body: str):
        super().__init__(f"Камера ответила ошибкой {code}: {body}")
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
