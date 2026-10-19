"""Проверки диагностики. Каждая возвращает CheckResult; исключения ловит раннер.

Цель — собрать факты о реальной камере (⚠-пункты дизайна §7): адрес потока,
запросы положения, реальный интервал команд, задержку, скорости, уведомления.
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

import cv2
import httpx

from ptztracker.camera import protocol as P
from ptztracker.camera.client import AuthRequired, CameraClient, CameraUnreachable
from ptztracker.diagnostics.motion import estimate_scale, measure_shift, wait_for_motion
from ptztracker.diagnostics.notify import NotificationListener
from ptztracker.video.frames import FrameSource
from ptztracker.video.rtsp import RtspSource, rtsp_candidates, stream_url, with_credentials

log = logging.getLogger(__name__)

Status = Literal["ok", "warn", "fail", "skip"]
PACE_S = 0.15  # пауза между командами во время диагностики (с запасом к 130 мс)
RTSP_WAIT_S = 4.0
FPS_WINDOW_S = 1.5
SPEED_CODES = (55, 60, 70, 80)
SPEED_MAX_S = 1.5
SPEED_MAX_FRACTION = 0.25
INTERVALS = (0.05, 0.08, 0.10, 0.13)
MAX_OPEN_STREAMS = 3  # сколько разных работающих потоков держать открытыми для замера задержки
LATENCY_TRIES = 3
INTERVAL_TRIES = 10


@dataclass
class CheckResult:
    id: str
    title: str
    status: Status
    summary: str
    details: dict = field(default_factory=dict)
    log: list[str] = field(default_factory=list)


@dataclass
class DiagContext:
    host: str
    username: str | None
    password: str | None
    allow_move: bool
    ask_user: Callable[[str, float], None]
    progress: Callable[[str], None]
    out_dir: Path
    rtsp_override: str | None = None
    notify_port: int = 31004
    notify_wait_s: float = 15.0
    source_factory: Callable[[str], FrameSource] | None = None


class Pacer:
    """Отправка команд не чаще PACE_S."""

    def __init__(self, client: CameraClient, gap: float = PACE_S):
        self.client, self.gap, self._last = client, gap, 0.0

    def send(self, cmd: str) -> str:
        wait = self._last + self.gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        return self.client.send(cmd)

    def try_send(self, cmd: str) -> str | None:
        try:
            return self.send(cmd)
        except Exception as e:
            log.warning("%s: %s", cmd, e)
            return None


def mask(text: str, ctx: DiagContext) -> str:
    """Убрать пароль из строки (и в открытом, и в URL-кодированном виде)."""
    if ctx.password:
        from urllib.parse import quote

        for s in {ctx.password, quote(ctx.password, safe="")}:
            text = text.replace(s, "***")
    return text


# --- 1. связь ------------------------------------------------------------


def check_reachability(ctx: DiagContext, st: dict) -> CheckResult:
    client = CameraClient(ctx.host, ctx.username, ctx.password, timeout=2.0)
    st["client"] = client
    st["pacer"] = Pacer(client)
    title = "Связь с камерой"
    try:
        power = st["pacer"].send(P.Q_POWER)
    except AuthRequired:
        return CheckResult("reachability", title, "fail", "Камера требует логин/пароль — укажите их (--user/--password).")
    except CameraUnreachable as e:
        return CheckResult("reachability", title, "fail", f"Камера не отвечает по HTTP: {e}", {"error": str(e)})
    st["reachable"] = True
    details = {"power_raw": power}
    model_raw = st["pacer"].try_send(P.Q_MODEL)
    details["model_raw"] = model_raw
    try:
        details["model"] = P.parse_model(model_raw or "")
    except ValueError:
        details["model"] = None
    status: Status = "ok" if power == "p1" else "warn"
    summary = f"Камера отвечает, модель: {details['model'] or 'не определена'}, питание: {power}"
    return CheckResult("reachability", title, status, summary, details)


# --- 2. видеопоток ------------------------------------------------------------


def _open_source(ctx: DiagContext, url: str) -> FrameSource:
    if ctx.source_factory:
        return ctx.source_factory(url)
    return RtspSource(url, open_timeout_s=3.0)


def check_rtsp(ctx: DiagContext, st: dict) -> CheckResult:
    urls = ([ctx.rtsp_override] if ctx.rtsp_override else []) + rtsp_candidates(ctx.host, ctx.username, ctx.password)
    urls.append(with_credentials(stream_url(ctx.host, "h264_2"), ctx.username, ctx.password))
    urls = list(dict.fromkeys(urls))
    kept: list[tuple[str, FrameSource]] = []
    kept_keys: set[str] = set()
    frames_dir = ctx.out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    tried = []
    best = None
    for n, url in enumerate(urls, 1):
        ctx.progress(f"Пробую видеопоток {n}/{len(urls)}…")
        src = _open_source(ctx, url)
        src.start()
        t0 = time.monotonic()
        first = None
        while time.monotonic() - t0 < RTSP_WAIT_S:
            first = src.latest()
            if first is not None:
                break
            if src.stats().error and not src.stats().connected and time.monotonic() - t0 > 1.0:
                break  # адрес не открылся — не ждём весь таймаут
            time.sleep(0.05)
        row = {"url": mask(url, ctx), "opened": first is not None}
        if first is None:
            row["error"] = mask(src.stats().error or "нет кадров", ctx)
            src.stop()
        else:
            open_s = time.monotonic() - t0
            s0 = first.seq
            time.sleep(FPS_WINDOW_S)
            last = src.latest()
            row.update(
                open_s=round(open_s, 2),
                width=first.image.shape[1],
                height=first.image.shape[0],
                fps=round((last.seq - s0) / FPS_WINDOW_S, 1) if last else 0.0,
            )
            cv2.imwrite(str(frames_dir / f"{n}.jpg"), first.image)
            key = url.replace(":554/", "/")  # тот же поток на явном порту 554 — не отдельный поток
            if best is None:
                best = (url, src)
            if key not in kept_keys and len(kept) < MAX_OPEN_STREAMS:
                kept.append((url, src))
                kept_keys.add(key)
            else:
                src.stop()
        tried.append(row)
    if best is None:
        return CheckResult("rtsp", "Видеопоток", "fail", "Ни один из адресов видеопотока не открылся.", {"tried": tried})
    st["source"] = best[1]
    st["source_url"] = best[0]
    st["sources"] = kept
    return CheckResult(
        "rtsp",
        "Видеопоток",
        "ok",
        f"Работает: {mask(best[0], ctx)}",
        {"tried": tried, "best": mask(best[0], ctx)},
    )


# --- 3. запросы состояния ----------------------------------------------------


def check_queries(ctx: DiagContext, st: dict) -> CheckResult:
    pacer: Pacer = st["pacer"]
    details = {}
    for name, cmd in (("pan_tilt", P.Q_PAN_TILT), ("zoom", P.Q_ZOOM), ("focus", P.Q_FOCUS), ("iris", P.Q_IRIS)):
        try:
            details[name + "_raw"] = pacer.send(cmd)
        except Exception as e:
            details[name + "_raw"] = None
            details[name + "_error"] = str(e)
    try:
        st["start_pos"] = P.parse_pan_tilt(details["pan_tilt_raw"] or "")
        st["apc_ok"] = True
        details["pan_tilt"] = [f"{v:04X}" for v in st["start_pos"]]
    except ValueError:
        st["apc_ok"] = False
    try:
        st["start_zoom"] = P.parse_zoom(details["zoom_raw"] or "")
        details["zoom"] = f"{st['start_zoom']:03X}"
    except ValueError:
        st["start_zoom"] = None
    ok = st["apc_ok"] and st["start_zoom"] is not None
    summary = (
        "Камера сообщает положение и зум."
        if ok
        else "Камера не сообщает положение/зум стандартными запросами — нужен другой способ (см. детали)."
    )
    return CheckResult("queries", "Запрос положения и зума", "ok" if ok else "warn", summary, details)


# --- 4. минимальный интервал между командами -------------------------------


def check_min_interval(ctx: DiagContext, st: dict) -> CheckResult:
    client: CameraClient = st["client"]
    rows = []
    for gap in INTERVALS:
        ctx.progress(f"Интервал между командами {int(gap * 1000)} мс…")
        errors = 0
        time.sleep(0.3)
        next_t = time.monotonic()
        for _ in range(INTERVAL_TRIES):
            wait = next_t - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            next_t = time.monotonic() + gap
            try:
                client.send(P.Q_POWER)
            except Exception:
                errors += 1
        rows.append({"gap_s": gap, "errors": errors})
    clean = [r["gap_s"] for r in rows if r["errors"] == 0]
    rec = min(clean) if clean else None
    time.sleep(0.3)
    if rec is None:
        return CheckResult(
            "min_interval", "Интервал между командами", "warn", "Ошибки на всех интервалах.", {"rows": rows, "recommended_s": None}
        )
    status: Status = "ok" if rec <= 0.13 else "warn"
    return CheckResult(
        "min_interval",
        "Интервал между командами",
        status,
        f"Без ошибок от {int(rec * 1000)} мс.",
        {"rows": rows, "recommended_s": rec},
    )


# --- движения ----------------------------------------------------------------


def _stop_all(pacer: Pacer) -> None:
    pacer.try_send(P.STOP_PT)
    pacer.try_send(P.STOP_ZOOM)


def _return_home(st: dict) -> bool:
    """Вернуть камеру в исходное положение по запомненным координатам."""
    pacer: Pacer = st["pacer"]
    ok = True
    if st.get("apc_ok"):
        target = st["start_pos"]
        pacer.try_send(P.pan_tilt_abs(*target))
        deadline = time.monotonic() + 8
        ok = False
        while time.monotonic() < deadline:
            time.sleep(0.3)
            raw = pacer.try_send(P.Q_PAN_TILT)
            try:
                pos = P.parse_pan_tilt(raw or "")
            except ValueError:
                continue
            if abs(pos[0] - target[0]) <= 4 and abs(pos[1] - target[1]) <= 4:
                ok = True
                break
    if st.get("start_zoom") is not None:
        pacer.try_send(P.zoom_abs(st["start_zoom"]))
        time.sleep(1.0)
    return ok


def _need_motion(ctx: DiagContext, st: dict, cid: str, title: str) -> CheckResult | None:
    if not ctx.allow_move:
        return CheckResult(cid, title, "skip", "Пропущено: движение камеры не разрешено.")
    if "source" not in st:
        return CheckResult(cid, title, "skip", "Пропущено: нет видеопотока.")
    return None


def _latency_on(ctx: DiagContext, pacer: Pacer, src: FrameSource, label: str) -> list[float]:
    samples: list[float] = []
    for i, cmd in enumerate(("#PTS6050", "#PTS4050", "#PTS6050")[:LATENCY_TRIES]):
        ctx.progress(f"Замер задержки ({label}) {i + 1}/{LATENCY_TRIES}…")
        ref = src.latest()
        if ref is None:
            break
        threshold = 1.5 * ref.image.shape[1] / 1280
        time.sleep(max(0.0, pacer._last + pacer.gap - time.monotonic()))
        t_cmd = time.monotonic()
        pacer.send(cmd)
        t_seen = wait_for_motion(src, t_cmd, threshold_px=threshold, reference=ref.image)
        pacer.send(P.STOP_PT)
        if t_seen is not None:
            samples.append((t_seen - t_cmd) * 1000)
        time.sleep(1.0)
    if len(samples) % 2:  # нечётное число поворотов — вернуть камеру на место
        pacer.send("#PTS4050")
        time.sleep(0.6)
        pacer.send(P.STOP_PT)
        time.sleep(0.8)
    return samples


def check_command_latency(ctx: DiagContext, st: dict) -> CheckResult:
    title = "Задержка «команда → движение в кадре»"
    if r := _need_motion(ctx, st, "command_latency", title):
        return r
    pacer: Pacer = st["pacer"]
    per_stream = []
    try:
        for url, src in st.get("sources") or [(st["source_url"], st["source"])]:
            fr = src.latest()
            samples = _latency_on(ctx, pacer, src, mask(url, ctx).split("/")[-1])
            if samples:
                per_stream.append(
                    {
                        "url": mask(url, ctx),
                        "width": fr.image.shape[1] if fr is not None else 0,
                        "height": fr.image.shape[0] if fr is not None else 0,
                        "samples_ms": [round(x) for x in samples],
                        "median_ms": round(statistics.median(samples)),
                    }
                )
    finally:
        _stop_all(pacer)
        _return_home(st)
    if not per_stream:
        return CheckResult("command_latency", title, "fail", "Движение в кадре не обнаружено.", {"per_stream": []})
    fastest = min(per_stream, key=lambda r: r["median_ms"])
    summary = "; ".join(f"{r['width']}×{r['height']}: {r['median_ms']} мс" for r in per_stream)
    return CheckResult(
        "command_latency",
        title,
        "ok",
        f"Медианы по потокам — {summary} (сеть + разгон камеры + видеопоток).",
        {
            "per_stream": per_stream,
            "median_ms": per_stream[0]["median_ms"],
            "samples_ms": per_stream[0]["samples_ms"],
            "fastest": fastest["url"],
        },
    )


def _probe_run(
    pacer: Pacer,
    src: FrameSource,
    code: int,
    max_s: float = SPEED_MAX_S,
    max_fraction: float = SPEED_MAX_FRACTION,
) -> dict:
    """Повернуть с кодом code до 25% ширины кадра или 1.5 с; вернуть скорость в px/с, разгон и торможение."""
    first = src.latest()
    width = first.image.shape[1]
    prev, last_seq = first, first.seq
    cum = 0.0
    samples = [(0.0, 0.0)]
    t_cmd = time.monotonic()
    pacer.send(P.pan_tilt_speed(code, 50))
    while time.monotonic() - t_cmd < max_s and abs(cum) < max_fraction * width:
        fr = src.latest()
        if fr is not None and fr.seq != last_seq:
            dx, _ = measure_shift(prev.image, fr.image)
            cum += dx
            samples.append((time.monotonic() - t_cmd, cum))
            prev, last_seq = fr, fr.seq
        time.sleep(0.005)
    t_stop = time.monotonic()
    pacer.send(P.STOP_PT)
    decel_s = None
    quiet_since = None
    while time.monotonic() - t_stop < 2.0:
        fr = src.latest()
        if fr is not None and fr.seq != last_seq:
            dx, _ = measure_shift(prev.image, fr.image)
            prev, last_seq = fr, fr.seq
            if abs(dx) < 0.5:
                quiet_since = quiet_since or time.monotonic()
                if time.monotonic() - quiet_since > 0.2:
                    decel_s = round(quiet_since - t_stop, 3)
                    break
            else:
                quiet_since = None
        time.sleep(0.005)
    # скорость — по второй половине разгона (установившийся участок)
    tail = samples[len(samples) // 2 :]
    if len(tail) >= 2 and tail[-1][0] > tail[0][0]:
        v = (tail[-1][1] - tail[0][1]) / (tail[-1][0] - tail[0][0])
    else:
        v = 0.0
    accel_s = None
    if v:
        for i in range(1, len(samples)):
            t0, c0 = samples[i - 1]
            t1, c1 = samples[i]
            if t1 > t0 and abs((c1 - c0) / (t1 - t0)) >= 0.9 * abs(v):
                accel_s = round(t1, 3)
                break
    return {
        "code": code,
        "px_per_s": round(-v, 1),  # поворот вправо сдвигает картинку влево
        "frame_width": width,
        "accel_s": accel_s,
        "decel_s": decel_s,
        "moved_px": round(-cum, 1),
    }


def check_speed_probe(ctx: DiagContext, st: dict) -> CheckResult:
    title = "Скорости поворота"
    if r := _need_motion(ctx, st, "speed_probe", title):
        return r
    pacer: Pacer = st["pacer"]
    src: FrameSource = st["source"]
    runs = []
    try:
        for code in SPEED_CODES:
            for c in (code, 100 - code):
                ctx.progress(f"Скорость: код {c:02d}…")
                runs.append(_probe_run(pacer, src, c))
                time.sleep(0.5)
    finally:
        _stop_all(pacer)
        home = _return_home(st)
    ok = all(abs(r["px_per_s"]) > 0 for r in runs)
    return CheckResult(
        "speed_probe",
        title,
        "ok" if ok else "warn",
        "Скорости измерены." + ("" if home else " Не удалось точно вернуть камеру в исходное положение."),
        {"runs": runs, "returned_home": home, "zoom": st.get("start_zoom")},
    )


def check_zoom_probe(ctx: DiagContext, st: dict) -> CheckResult:
    title = "Зум"
    if r := _need_motion(ctx, st, "zoom_probe", title):
        return r
    pacer: Pacer = st["pacer"]
    src: FrameSource = st["source"]
    details = {}
    try:
        before = src.latest().image
        z0 = pacer.try_send(P.Q_ZOOM)
        pacer.send(P.zoom_speed(75))
        time.sleep(0.7)
        pacer.send(P.STOP_ZOOM)
        time.sleep(0.8)
        after = src.latest().image
        z1 = pacer.try_send(P.Q_ZOOM)
        details.update(zoom_before_raw=z0, zoom_after_raw=z1, image_scale=estimate_scale(before, after))
    finally:
        _stop_all(pacer)
        _return_home(st)
    changed = (details.get("image_scale") or 1.0) > 1.02 or (z0 and z1 and z0 != z1)
    return CheckResult(
        "zoom_probe",
        title,
        "ok" if changed else "warn",
        "Зум работает." if changed else "Изменение зума не обнаружено.",
        details,
    )


# --- уведомления и пульт -----------------------------------------------------


def check_notifications(ctx: DiagContext, st: dict) -> CheckResult:
    title = "Уведомления и движения с пульта"
    listener = NotificationListener(ctx.notify_port)
    try:
        listener.start()
    except OSError as e:
        return CheckResult("notifications", title, "fail", f"Не удалось открыть порт {ctx.notify_port}: {e}")
    port = listener.port
    auth = httpx.DigestAuth(ctx.username, ctx.password or "") if ctx.username else None
    reg = {}
    try:
        r = httpx.get(f"http://{ctx.host}/cgi-bin/event?connect=start&my_port={port}&uid=0", auth=auth, timeout=3.0)
        reg = {"status": r.status_code, "body": r.text[:200]}
    except httpx.HTTPError as e:
        reg = {"error": str(e)}
    polls: list[dict] = []
    stop = threading.Event()
    pacer: Pacer = st["pacer"]

    def poll():
        while not stop.is_set():
            polls.append(
                {"t": round(time.monotonic(), 2), "pt": pacer.try_send(P.Q_PAN_TILT), "z": pacer.try_send(P.Q_ZOOM)}
            )
            stop.wait(0.5)

    th = threading.Thread(target=poll, daemon=True)
    th.start()
    try:
        ctx.ask_user("Подвигайте камеру ПУЛЬТОМ (влево-вправо и зум), затем отпустите.", ctx.notify_wait_s)
    finally:
        stop.set()
        th.join(2)
        try:
            httpx.get(f"http://{ctx.host}/cgi-bin/event?connect=stop&my_port={port}&uid=0", auth=auth, timeout=3.0)
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
        listener.stop()
    events = listener.events()
    changes = sum(1 for a, b in zip(polls, polls[1:]) if (a["pt"], a["z"]) != (b["pt"], b["z"]))
    details = {
        "register": reg,
        "notifications": [{"t": round(t, 2), "hex": d.hex(), "text": d.decode("latin-1")} for t, d in events[:200]],
        "poll_changes": changes,
        "polls": polls[:200],
    }
    if events:
        return CheckResult("notifications", title, "ok", f"Получено уведомлений: {len(events)}.", details)
    if changes:
        return CheckResult(
            "notifications",
            title,
            "warn",
            "Уведомлений нет, но движения с пульта видны по опросу положения.",
            details,
        )
    return CheckResult(
        "notifications",
        title,
        "warn",
        "Нет ни уведомлений, ни изменений положения. Двигали ли камеру пультом? Разрешён ли входящий порт в брандмауэре?",
        details,
    )


ALL_CHECKS = (
    ("reachability", check_reachability, False),
    ("rtsp", check_rtsp, False),
    ("queries", check_queries, True),
    ("min_interval", check_min_interval, True),
    ("command_latency", check_command_latency, True),
    ("speed_probe", check_speed_probe, True),
    ("zoom_probe", check_zoom_probe, True),
    ("notifications", check_notifications, True),
)
"""(id, функция, нужна ли HTTP-связь с камерой)"""

TITLES = {
    "reachability": "Связь с камерой",
    "rtsp": "Видеопоток",
    "queries": "Запрос положения и зума",
    "min_interval": "Интервал между командами",
    "command_latency": "Задержка «команда → движение в кадре»",
    "speed_probe": "Скорости поворота",
    "zoom_probe": "Зум",
    "notifications": "Уведомления и движения с пульта",
}

