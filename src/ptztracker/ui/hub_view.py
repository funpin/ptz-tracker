"""Что показать про Videohub: значок верхней строки и строка меню ☰. Без Qt."""

from __future__ import annotations

from ptztracker.videohub.client import HubSnapshot


def hub_source_name(snap: HubSnapshot, output: int, cameras: list[tuple[str, int]]) -> str | None:
    """Что сейчас на выходе: имя камеры с этим входом (первая по порядку), иначе «вход N · подпись хаба»."""
    inp = snap.state.routing.get(output)
    if not inp:
        return None
    for name, cam_in in cameras:
        if cam_in == inp:
            return name
    label = snap.state.input_labels.get(inp, "")
    return f"вход {inp} · {label}" if label else f"вход {inp}"


def hub_pill(configured: bool, snap: HubSnapshot, output: int,
             cameras: list[tuple[str, int]]) -> tuple[str, str, str] | None:
    """(текст, kind, подсказка) значка хаба; None — хаб не настроен, значка нет."""
    if not configured:
        return None
    if not snap.online:
        return ("Хаб: нет связи", "fault", "Videohub не отвечает: проверьте адрес и что хаб в той же сети")
    if not snap.state.present:
        return ("Хаб: нет устройства", "fault", "Сервер Videohub отвечает, но сам хаб не подключён или ждёт обновления")
    if not output:
        return ("Хаб: выход не задан", "warn", "Выберите выход в меню ☰ → Videohub…")
    if snap.state.locks.get(output) == "L":
        return (f"Хаб: выход {output} занят", "warn",
                f"Выход {output} заблокирован с другого пульта — программа его не переключает")
    src = hub_source_name(snap, output, cameras)
    head = f"Хаб: вых. {output} ← {src}" if src else f"Хаб: выход {output}"
    if snap.last_error == "nak":
        return (head, "warn", "Хаб отказал в последнем переключении (NAK)")
    model = snap.state.model or "Videohub"
    return (head, "ok", f"{model}: на выходе {output} — {src or 'неизвестно'}")


def hub_menu_status(configured: bool, snap: HubSnapshot, ip: str) -> tuple[str, str]:
    """Справа от «Videohub…» в ☰."""
    if not configured:
        return ("не настроен", "off")
    if snap.online and snap.state.present:
        return (f"на связи · {ip}", "ok")
    return ("нет связи", "fault")
