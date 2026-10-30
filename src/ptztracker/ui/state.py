"""Что показывает интерфейс: состояние слежения и Tally ATEM (чистые функции)."""

import re
from dataclasses import dataclass

from ptztracker.ui.theme import C
from ptztracker.video.frames import SourceStats


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


# --- значки верхней панели: (текст, kind, подсказка); kind — ok | warn | fault | off | pgm | pvw ---


def video_pill(stats: SourceStats | None, stream_label: str) -> tuple[str, str, str]:
    if stats is None:
        return ("", "off", "Камера не выбрана")
    head = f"Видео: {stream_label}"
    if stats.connected:
        return (f"{stats.width}×{stats.height} · {stats.fps:.0f}", "ok",
                f"{head}\n{stats.width}×{stats.height}, {stats.fps:.0f} к/с\n{stats.url}")
    return ("нет", "fault", f"{head}\nНет видео: {stats.error or 'нет кадров'}")


def analysis_pill(error: str | None, fps: float | None, infer_ms: float | None,
                  device: str | None) -> tuple[str, str, str]:
    if error:
        return ("нет", "fault", f"Распознавание недоступно: {error}")
    if fps is None:
        return ("…", "warn", "Анализ: загрузка модели…")
    return (f"{fps:.0f} к/с", "ok", f"Анализ: {fps:.0f} к/с · {infer_ms or 0:.0f} мс · {device or ''}".rstrip(" ·"))


def atem_pill(configured: bool, online: bool, inp: int, state: str | None,
              known: bool = True) -> tuple[str, str, str] | None:
    chip = tally_chip(configured, online, inp, state, known=known)
    if chip is None:
        return None
    tip = chip[0]
    if not online or inp == 0:
        return ("ATEM", "off", tip)
    if not known:
        return (f"ATEM {inp}", "warn", tip)
    if state in ("pgm", "pvw"):
        return (f"ATEM {inp} · {state.upper()}", state, tip)
    return (f"ATEM {inp}", "ok", tip)


def power_view(power: str, online: bool) -> str:
    """Что показать поверх видео: standby | switching | offline | on (on — ничего)."""
    if not online:
        return "offline"
    return power if power in ("standby", "switching") else "on"


_REASONS = (
    (("timed out", "timeout", "time out"), "камера не отвечает"),
    (("refused",), "камера отклонила подключение"),
    (("401", "unauthorized", "auth"), "неверный логин или пароль камеры"),
    (("invalid data", "5xx", " 5", "not found", "404"), "камера не отдаёт видео по этому адресу"),
)


def no_video_text(camera_name: str, error: str | None) -> str:
    """Текст поверх пустого видео: имя камеры и короткая причина без кодов и адресов (они — в подсказке)."""
    head = f"Нет видео с камеры {camera_name}"
    if not error:
        return head
    low = error.lower()
    for keys, reason in _REASONS:
        if any(k in low for k in keys):
            return f"{head}\n{reason}"
    short = re.sub(r"\[Errno -?\d+\]\s*|\S+://\S+|['\"]", "", error).strip(" :.")
    return f"{head}\n{short[:57] + '…' if len(short) > 60 else short}"
