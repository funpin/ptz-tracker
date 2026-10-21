"""Повтор записанного эпизода без камеры.

Режим «регулятор»: записанные такты слежения (событие `control`) заново идут через FramingController,
получаются новые коды pan/tilt/zoom. С теми же настройками коды совпадают с записанными — так
проверяется, что повтор честный; с другими (effort, composition, калибровка) видно, что изменилось
бы на этом эпизоде.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields

from ptztracker.control.camera_model import Calibration, default_calibration
from ptztracker.control.composition import COMPOSITIONS, DEFAULT_COMPOSITION, BodyMeasure, Composition
from ptztracker.control.effort import EffortProfile
from ptztracker.control.follower import output_codes
from ptztracker.control.framing import FramingController, FramingInput
from ptztracker.recorder.reader import Episode

DEFAULT_LATENCY_S = 0.15  # как в CameraSession, если для вида потока нет замера
# follow on, записанный позже первого такта (поток слежения успел раньше enable()), — тот же запуск
LATE_FOLLOW_ON_S = 0.2
MAX_DT_S = 0.2  # длиннее — пауза в тактах, в долю «вне мягкой зоны» не идёт

Codes = tuple[int, int, int]


@dataclass
class ReplaySummary:
    following_s: float  # сколько секунд шло слежение
    steps: int
    pan_reversals_rec: int
    pan_reversals_new: int
    max_accel_rec: float  # fw/s², по скоростям кодов pan/tilt
    max_accel_new: float
    out_of_soft_pct: float  # % времени слежения, когда цель вне мягкой зоны (по новому прогону)
    target_losses: int
    target_switches: int
    max_code_diff: int  # самое большое расхождение «было/стало» по коду
    partial_start: bool  # запись началась посреди слежения

    def text(self) -> str:
        lines = [
            f"Слежение: {self.following_s:.1f} с, тактов регулятора: {self.steps}",
            f"Развороты pan: было {self.pan_reversals_rec}, стало {self.pan_reversals_new}",
            f"Макс. ускорение по командам: было {self.max_accel_rec:.2f}, стало {self.max_accel_new:.2f} fw/s²",
            f"Цель вне мягкой зоны: {self.out_of_soft_pct:.1f}% времени слежения",
            f"Потери цели: {self.target_losses}",
            f"Смены цели: {self.target_switches}",
            f"Расхождение кодов «было/стало»: до {self.max_code_diff}",
        ]
        if self.partial_start:
            lines.append("Запись началась посреди слежения: первые такты могут не совпасть с записанными.")
        return "\n".join(lines)


@dataclass
class ReplayResult:
    # ряды по тактам регулятора
    t: list[float] = field(default_factory=list)
    err_x: list[float] = field(default_factory=list)
    err_y: list[float] = field(default_factory=list)
    size_err: list[float | None] = field(default_factory=list)
    codes_rec: list[Codes] = field(default_factory=list)
    codes_new: list[Codes] = field(default_factory=list)
    state: list[str] = field(default_factory=list)
    zoom_pos: list[int] = field(default_factory=list)
    out_of_soft: list[bool] = field(default_factory=list)  # цель вне мягкой зоны (только при активной цели)
    segment: list[int] = field(default_factory=list)  # номер включения слежения
    # интервалы и события эпизода
    segments: list[tuple[float, float]] = field(default_factory=list)  # слежение включено
    lost: list[tuple[float, float]] = field(default_factory=list)  # цель потеряна
    targets: list[tuple[float, int | None, str]] = field(default_factory=list)  # (t, id цели, состояние)
    position: list[tuple[float, int, int, int | None]] = field(default_factory=list)  # (t, pan, tilt, zoom)
    t_start: float = 0.0
    t_end: float = 0.0
    partial_start: bool = False
    calib: Calibration | None = None

    def summary(self) -> ReplaySummary:
        return ReplaySummary(
            following_s=sum(b - a for a, b in self.segments),
            steps=len(self.t),
            pan_reversals_rec=self._reversals(self.codes_rec),
            pan_reversals_new=self._reversals(self.codes_new),
            max_accel_rec=self._max_accel(self.codes_rec),
            max_accel_new=self._max_accel(self.codes_new),
            out_of_soft_pct=self._out_of_soft_pct(),
            target_losses=sum(1 for a, b in zip(self.targets, self.targets[1:])
                              if a[2] == "tracking" and b[2] == "lost"),
            target_switches=self._switches(),
            max_code_diff=max((abs(x - y) for a, b in zip(self.codes_rec, self.codes_new) for x, y in zip(a, b)),
                              default=0),
            partial_start=self.partial_start,
        )

    def _pairs(self):
        """Соседние такты одного включения слежения: (i-1, i)."""
        for i in range(1, len(self.t)):
            if self.segment[i] == self.segment[i - 1]:
                yield i - 1, i

    def _reversals(self, codes: list[Codes]) -> int:
        n, last, seg = 0, 0, None
        for i, c in enumerate(codes):
            if self.segment[i] != seg:
                seg, last = self.segment[i], 0
            sgn = (c[0] > 50) - (c[0] < 50)
            if sgn and last and sgn != last:
                n += 1
            if sgn:
                last = sgn
        return n

    def _max_accel(self, codes: list[Codes]) -> float:
        speed = (self.calib or default_calibration()).pan.speed
        best = 0.0
        for a, b in self._pairs():
            dt = max(1e-3, self.t[b] - self.t[a])
            for k in (0, 1):  # pan и tilt — по одной таблице скоростей
                dv = speed(codes[b][k], self.zoom_pos[b]) - speed(codes[a][k], self.zoom_pos[a])
                best = max(best, abs(dv) / dt)
        return best

    def _out_of_soft_pct(self) -> float:
        total = out = 0.0
        for a, b in self._pairs():
            dt = self.t[b] - self.t[a]
            if dt > MAX_DT_S:
                continue
            total += dt
            if self.out_of_soft[a]:
                out += dt
        return 100.0 * out / total if total > 0 else 0.0

    def _switches(self) -> int:
        n, last = 0, None
        for _, tid, _ in self.targets:
            if tid is None:
                continue
            if last is not None and tid != last:
                n += 1
            last = tid
        return n


def _composition(key, fallback: Composition) -> Composition:
    return COMPOSITIONS.get(key, fallback) if key else fallback


def _effort(d, fallback: EffortProfile) -> EffortProfile:
    if not isinstance(d, dict):
        return fallback
    try:
        return EffortProfile(**{f.name: float(d[f.name]) for f in fields(EffortProfile)})
    except (KeyError, TypeError, ValueError):
        return fallback


def _measure(m) -> BodyMeasure | None:
    if not m:
        return None
    return BodyMeasure((float(m["nose"][0]), float(m["nose"][1])),
                       None if m.get("u") is None else float(m["u"]), bool(m.get("confident", True)))


def episode_calibration(ep: Episode) -> Calibration:
    d = ep.meta.get("calibration")
    if d:
        try:
            return Calibration.from_json(d)
        except Exception:
            pass
    return default_calibration()


def replay_controller(
    ep: Episode,
    calib: Calibration | None = None,
    composition: Composition | None = None,
    effort: EffortProfile | None = None,
) -> ReplayResult:
    """Прогоняет такты слежения эпизода через FramingController.

    Без параметров — с записанными калибровкой, композицией и характером (они пишутся в каждом такте),
    с параметрами — с новыми для всего эпизода. Регулятор сбрасывается при каждом включении слежения,
    как в Follower.enable().
    """
    calib = calib or episode_calibration(ep)
    latency = calib.video_latency.get(ep.meta.get("stream_kind") or "", DEFAULT_LATENCY_S)
    res = ReplayResult(calib=calib)
    events = sorted(ep.events(), key=lambda e: e.get("t", 0.0))  # в zip события идут в порядке очереди
    if events:
        res.t_start, res.t_end = events[0]["t"], events[-1]["t"]

    comp = composition or COMPOSITIONS[DEFAULT_COMPOSITION]
    eff = effort or EffortProfile.at(0.5)
    ctrl = FramingController(comp, eff, calib, latency)
    codes: Codes = (50, 50, 50)
    bounds: list[list[float]] = []  # [начало, конец] каждого включения слежения
    on = False
    implicit_t: float | None = None  # слежение «включилось» по такту, а событие follow on ещё не встретилось

    def start(t: float) -> None:
        nonlocal codes, on
        ctrl.video_latency = latency  # как Follower.enable(): сброс регулятора и кодов
        ctrl.reset()
        codes = (50, 50, 50)
        bounds.append([t, t])
        on = True

    for e in events:
        kind, t = e.get("kind"), e["t"]
        if kind == "follow" and "on" in e:  # без "on" — смена настроек: они есть в каждом такте
            if e["on"] and on and implicit_t is not None and t - implicit_t <= LATE_FOLLOW_ON_S:
                if len(bounds) == 1:
                    res.partial_start = False  # первый запуск записан целиком
            elif e["on"]:
                start(t)
            elif on:
                bounds[-1][1] = t
                on = False
            implicit_t = None
        elif kind == "control":
            if not on:
                start(t)
                implicit_t = t
                if len(bounds) == 1:
                    res.partial_start = True  # пока не встретился follow on сразу после такта
            ctrl.composition = composition or _composition(e.get("composition"), ctrl.composition)
            ctrl.effort = effort or _effort(e.get("effort"), ctrl.effort)
            zoom_pos = int(e.get("zoom_pos", 0x555))
            out = ctrl.step(FramingInput(now=t, t_capture=e.get("t_capture"), measure=_measure(e.get("measure")),
                                         zoom_pos=zoom_pos, aspect=float(e.get("aspect", 16 / 9))))
            codes = output_codes(calib, out, zoom_pos, codes)
            rec = e.get("codes") or [50, 50, 50]
            soft_x, soft_y = ctrl.effort.soft_x, ctrl.effort.soft_y
            res.t.append(t)
            res.err_x.append(float(out.error[0]))
            res.err_y.append(float(out.error[1]))
            res.size_err.append(out.size_error)
            res.codes_rec.append((int(rec[0]), int(rec[1]), int(rec[2])))
            res.codes_new.append(codes)
            res.state.append(out.state)
            res.zoom_pos.append(zoom_pos)
            res.out_of_soft.append(out.state in ("holding", "moving")
                                   and (abs(out.error[0]) > soft_x or abs(out.error[1]) > soft_y))
            res.segment.append(len(bounds) - 1)
            bounds[-1][1] = t
        elif kind == "analysis":
            res.targets.append((t, e.get("target_id"), e.get("target_state") or "none"))
        elif kind == "position":
            res.position.append((t, int(e["pan"]), int(e["tilt"]), e.get("zoom")))
    res.segments = [(a, b) for a, b in bounds]
    res.lost = _lost_intervals(res.targets, res.t_end)
    return res


def _lost_intervals(targets: list[tuple[float, int | None, str]], t_end: float) -> list[tuple[float, float]]:
    out, since = [], None
    for t, _, st in targets:
        if st == "lost" and since is None:
            since = t
        elif st != "lost" and since is not None:
            out.append((since, t))
            since = None
    if since is not None:
        out.append((since, max(since, t_end)))
    return out
