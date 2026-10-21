"""Что показывает интерфейс: состояние слежения и Tally ATEM (чистые функции)."""

from dataclasses import dataclass

from ptztracker.ui.theme import C


@dataclass(frozen=True)
class TrackingView:
    kind: str  # off | on | lost | remote | swing
    word: str  # слово состояния в плитке
    why: str  # пояснение (до 2 строк)
    tag: str  # ярлык на видео
    color: str  # hex из C
    show_calibrate: bool


def tracking_view(
    enabled: bool,
    reason: str | None,
    target_id: int | None,
    target_state: str | None,
    diverged: bool = False,
    remote: bool = False,
) -> TrackingView:
    if remote:
        return TrackingView(
            "remote", "ПУЛЬТ",
            "Камерой управляют с пульта, слежение на паузе. Пробел — продолжить.",
            "ПУЛЬТ", C.REMOTE, False)
    if enabled and target_state == "lost":
        return TrackingView(
            "lost", "ЦЕЛЬ ПОТЕРЯНА",
            "Камера стоит и ждёт. Человек вернётся в кадр — слежение продолжится.",
            "ЦЕЛЬ ПОТЕРЯНА", C.LOST, False)
    if enabled:
        who = f" №{target_id}" if target_id is not None else ""
        return TrackingView(
            "on", "ВЕДУ ЦЕЛЬ",
            f"Камера ведёт цель{who}. Ручное действие или Esc выключат слежение.",
            "СЛЕЖЕНИЕ", C.ON, False)
    if diverged:
        return TrackingView(
            "swing", "РАСКАЧКА",
            "Слежение остановлено: камера быстрее, чем ожидалось.",
            "РАСКАЧКА", C.FAULT, True)
    why = (f"Выключено: {reason}" if reason else
           "Кликните по человеку на видео, затем нажмите Пробел.")
    return TrackingView("off", "ВЫКЛ", why, "ВЫКЛ", C.TEXT_FAINT, False)


def tally_chip(
    atem_configured: bool, online: bool, atem_input: int, state: str | None, *, known: bool = True
) -> tuple[str, str] | None:
    """(текст, kind чипа) или None, если ATEM не настроен. state: pgm | pvw | None.
    known=False — микшер прислал меньше флагов, чем номер входа: состояние неизвестно."""
    if not atem_configured:
        return None
    if not online:
        return ("ATEM · нет связи", "neutral")
    if atem_input == 0:
        return ("ATEM · вход не задан", "neutral")
    head = f"ATEM · вход {atem_input}"
    if not known:
        return (f"{head} вне диапазона", "warn")
    if state == "pgm":
        return (f"{head} · ЭФИР", "pgm")
    if state == "pvw":
        return (f"{head} · превью", "pvw")
    return (head, "neutral")
