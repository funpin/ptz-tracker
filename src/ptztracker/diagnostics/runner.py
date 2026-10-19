"""Прогон диагностики и упаковка отчёта в zip-архив для разработчика."""

from __future__ import annotations

import dataclasses
import json
import logging
import shutil
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from ptztracker import __version__
from ptztracker.camera import protocol as P
from ptztracker.diagnostics.checks import ALL_CHECKS, TITLES, CheckResult, DiagContext, mask

__all__ = ["DiagContext", "default_report_dir", "run_diagnostics"]

log = logging.getLogger(__name__)

_MARK = {"ok": "[OK]  ", "warn": "[!]   ", "fail": "[ОШИБ]", "skip": "[—]   "}


def default_report_dir() -> Path:
    """Рабочий стол пользователя (если его нет — текущая папка)."""
    import platformdirs

    desktop = Path(platformdirs.user_desktop_dir())
    return desktop if desktop.is_dir() else Path.cwd()


class _MaskFilter(logging.Filter):
    def __init__(self, ctx: DiagContext):
        super().__init__()
        self.ctx = ctx

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask(record.getMessage(), self.ctx)
        record.args = ()
        return True


def _report_txt(ctx: DiagContext, results: list[CheckResult], started: datetime) -> str:
    lines = [
        f"Диагностика PTZ Tracker {__version__}",
        f"Камера: {ctx.host}",
        f"Дата: {started:%Y-%m-%d %H:%M}",
        f"Движение камеры разрешено: {'да' if ctx.allow_move else 'нет'}",
        "",
    ]
    for r in results:
        lines.append(f"{_MARK[r.status]} {r.title}: {r.summary}")
    lines += ["", "Рекомендации:"]
    by_id = {r.id: r for r in results}
    if (r := by_id.get("rtsp")) and r.status == "ok":
        lines.append(f"- Адрес видеопотока: {r.details.get('best')}")
    if (r := by_id.get("min_interval")) and r.details.get("recommended_s"):
        lines.append(f"- Минимальный интервал команд: {int(r.details['recommended_s'] * 1000)} мс")
    if (r := by_id.get("command_latency")) and r.details.get("median_ms"):
        lines.append(f"- Задержка команда→кадр: ~{r.details['median_ms']} мс")
    lines += ["", "Пришлите этот архив разработчику целиком."]
    return "\n".join(lines) + "\n"


def run_diagnostics(ctx: DiagContext) -> Path:
    started = datetime.now()
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="ptz-diag-"))
    inner = dataclasses.replace(ctx, out_dir=work)
    handler = logging.FileHandler(work / "diag.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_MaskFilter(ctx))
    root = logging.getLogger()
    old_level = root.level
    root.setLevel(min(old_level, logging.INFO) if old_level else logging.INFO)
    root.addHandler(handler)
    state: dict = {}
    results: list[CheckResult] = []
    try:
        log.info("Диагностика %s, движение: %s", ctx.host, ctx.allow_move)
        for cid, fn, needs_http in ALL_CHECKS:
            if needs_http and not state.get("reachable"):
                results.append(CheckResult(cid, TITLES[cid], "skip", "Пропущено: нет HTTP-связи с камерой."))
                continue
            ctx.progress(f"Проверка: {TITLES[cid]}…")
            t0 = time.monotonic()
            try:
                res = fn(inner, state)
            except Exception as e:
                log.exception("Проверка %s упала", cid)
                res = CheckResult(cid, TITLES[cid], "fail", f"Ошибка: {e}")
            res.details["duration_s"] = round(time.monotonic() - t0, 2)
            log.info("%s → %s: %s", cid, res.status, res.summary)
            results.append(res)
    finally:
        if pacer := state.get("pacer"):
            pacer.try_send(P.STOP_PT)
            pacer.try_send(P.STOP_ZOOM)
        if src := state.get("source"):
            src.stop()
        if client := state.get("client"):
            client.close()
        root.removeHandler(handler)
        root.setLevel(old_level)
        handler.close()

    report = {
        "version": __version__,
        "host": ctx.host,
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now().isoformat(timespec="seconds"),
        "allow_move": ctx.allow_move,
        "checks": [dataclasses.asdict(r) for r in results],
    }
    (work / "report.json").write_text(
        mask(json.dumps(report, ensure_ascii=False, indent=2, default=str), ctx), encoding="utf-8"
    )
    (work / "report.txt").write_text(mask(_report_txt(ctx, results, started), ctx), encoding="utf-8")

    safe_host = "".join(ch if ch.isalnum() or ch in "-." else "_" for ch in ctx.host)
    zip_path = ctx.out_dir / f"ptz-diag-{safe_host}-{started:%Y%m%d-%H%M%S}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(work.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(work).as_posix())
    shutil.rmtree(work, ignore_errors=True)
    ctx.progress(f"Готово: {zip_path}")
    return zip_path
