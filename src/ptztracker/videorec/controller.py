"""Сводка записи всех камер для окна: старт, стоп, запас места, разовые сообщения. Без Qt."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ptztracker.config import AppConfig, video_dir
from ptztracker.videorec.disk import RATE_CAMERA_BPS, RATE_LIGHT_BPS, DiskInfo, DiskWatch, is_fat32
from ptztracker.videorec.writer import FAT32_SPLIT_BYTES

WARN_MIN, CRIT_MIN, STOP_MIN = 20, 5, 1  # минут записи осталось
CLOSE_WAIT_S = 2.0
_GB = 1_000_000_000


@dataclass(frozen=True)
class RecRow:
    slot: int
    name: str
    active: bool
    signal: bool
    elapsed_s: float
    bytes_total: int


@dataclass(frozen=True)
class RecSummary:
    rows: list[RecRow] = field(default_factory=list)
    text: str = ""
    level: str = ""  # "" | "warn" | "crit"
    free_bytes: int = 0
    tooltip: str = ""


def format_clock(seconds: float) -> str:
    s = int(seconds)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def format_left(minutes: float) -> str:
    """Округляет вниз: запас лучше не обещать лишний."""
    total = int(minutes)
    h, m = total // 60, total % 60
    if not h:
        return f"{m} мин"
    return f"{h} ч {m} мин" if m and h < 2 else f"{h} ч"


def format_gb(n: float) -> str:
    v = n / _GB
    return str(round(v)) if v >= 10 else f"{v:.1f}".replace(".", ",")


def where(info: DiskInfo) -> str:
    return info.label if info.label not in ("", "/") else "диске"


class VideoController:
    def __init__(self, cfg: AppConfig, sessions: Callable[[], list], disk: DiskWatch | None = None):
        self.cfg, self._sessions = cfg, sessions
        self.disk = disk or DiskWatch(self.current_folder)
        self._rec_folder: Path | None = None
        self._level = ""
        self._tracked: dict[str, tuple[object, str]] = {}  # id камеры → (сессия, имя): пишут, ждём конца

    def current_folder(self) -> Path:
        return self._rec_folder if self._rec_folder is not None else video_dir(self.cfg)

    def disk_snapshot(self) -> DiskInfo | None:
        return self.disk.snapshot()

    def any_active(self) -> bool:
        return any(s.video_state().active for _, s in self._sessions())

    def toggle(self, session) -> str | None:
        """Начать или остановить запись; вернуть текст отказа, если запись не началась."""
        if session.video_state().active:
            self._tracked.pop(session.cfg.id, None)
            session.stop_video()
            self._release_folder()
            return None
        info = self.disk.snapshot()
        if info is not None and not info.connected:
            return f"Диск {info.label} не подключён"
        split = FAT32_SPLIT_BYTES if info is not None and is_fat32(info) else None
        self._rec_folder = self.current_folder()
        session.start_video(self._rec_folder, self.cfg.video_mode, split)
        self._tracked[session.cfg.id] = (session, session.cfg.name)
        return None

    def stop_all(self) -> list:
        """Остановить запись всех камер; вернуть рекордеры — их файлы закрываются в фоне."""
        self._tracked.clear()
        recorders = []
        for _, s in self._sessions():
            s.stop_video()
            if s.video is not None:
                recorders.append(s.video)
        self._rec_folder = None
        self.disk.refresh()
        return recorders

    def stop_slot(self, slot: int) -> None:
        for i, s in self._sessions():
            if i == slot and s.video_state().active:
                self.toggle(s)

    @staticmethod
    def wait_closed(recorders: list, timeout: float = CLOSE_WAIT_S) -> None:
        deadline = time.monotonic() + timeout
        for r in recorders:
            r.wait_closed(max(0.0, deadline - time.monotonic()))

    def _release_folder(self) -> None:
        if not self.any_active():
            self._rec_folder = None
            self.disk.refresh()

    def _rate(self, rows: list[RecRow], sessions: dict) -> float:
        fallback = RATE_LIGHT_BPS if self.cfg.video_mode == "light" else RATE_CAMERA_BPS
        return sum(sessions[r.slot].rate_bps or fallback for r in rows if r.signal)

    def _collect_messages(self, states: list) -> list[str]:
        msgs: list[str] = []
        for _, s, _st in states:
            while (note := s.video_note()) is not None:
                msgs.append(note)
        live = {s.cfg.id: st for _, s, st in states}
        for cid, (session, name) in list(self._tracked.items()):
            st = live.get(cid)
            if st is None or live_session(states, cid) is not session:
                del self._tracked[cid]
                msgs.append(f"«{name}»: запись остановлена")
            elif not st.active:
                del self._tracked[cid]
                msgs.append(st.error or f"«{name}»: запись остановлена")
        return msgs

    def tick(self) -> tuple[RecSummary, list[str]]:
        states = [(i, s, s.video_state()) for i, s in self._sessions()]
        msgs = self._collect_messages(states)
        rows = [RecRow(i, s.cfg.name, True, st.signal, st.elapsed_s, st.bytes_total)
                for i, s, st in states if st.active]
        if not rows:
            if self._rec_folder is not None:
                self._release_folder()
            return RecSummary(), msgs
        info = self.disk.snapshot()
        by_slot = {i: st for i, _, st in states}
        rate = self._rate(rows, by_slot)
        known = info is not None and info.connected  # вынутый диск — не «мало места»: причину назовёт рекордер
        minutes = info.free / rate / 60 if known and rate > 0 else None
        if minutes is not None and minutes < STOP_MIN:
            self.stop_all()
            msgs.append("Запись остановлена: нет места")
            return RecSummary(), msgs
        level = "" if minutes is None or minutes >= WARN_MIN else "warn" if minutes >= CRIT_MIN else "crit"
        if level == "crit" and self._level != "crit":
            msgs.append("Мало места")
        self._level = level
        written = f"Записано {format_gb(sum(r.bytes_total for r in rows))} ГБ"
        text, tooltip, free = "", written, 0
        if known:
            free = info.free
            tooltip += f" · на {info.name or where(info)} свободно {format_gb(free)} ГБ"
            if minutes is not None:
                text = f"~{format_left(minutes)}"
        return RecSummary(rows, text, level, free, tooltip), msgs


def live_session(states: list, camera_id: str):
    return next((s for _, s, _st in states if s.cfg.id == camera_id), None)
