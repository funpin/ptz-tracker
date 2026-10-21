"""Повтор записанного эпизода без камеры.

Режим «регулятор»: записанные такты слежения (событие `control`) заново идут через FramingController,
получаются новые коды pan/tilt/zoom. С теми же настройками коды совпадают с записанными — так
проверяется, что повтор честный; с другими (effort, composition, калибровка) видно, что изменилось
бы на этом эпизоде.

Режим «распознавание»: кадры записи заново идут через детектор и PersonTracker. Цель выбирается
тем же щелчком, что и в записи; сравниваются цель «было/стало», смены и потери — так исправления
трекера проверяются на настоящих кадрах.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields

from ptztracker.control.camera_model import Calibration, default_calibration
from ptztracker.control.composition import COMPOSITIONS, DEFAULT_COMPOSITION, BodyMeasure, Composition
from ptztracker.control.effort import EffortProfile
from ptztracker.control.follower import output_codes
from ptztracker.control.framing import FramingController, FramingInput
from ptztracker.recorder.reader import Episode, _num
from ptztracker.vision.tracker import _iou

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
    calibration_ok: bool = True
    skipped_events: int = 0

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
        if not self.calibration_ok:
            lines.append("Калибровка в записи не читается: повтор идёт по средним значениям, коды могут не совпасть.")
        if self.skipped_events:
            lines.append(f"Пропущено {self.skipped_events} повреждённых событий.")
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
    calibration_ok: bool = True  # False — калибровка в записи не читается, взяты средние значения
    skipped_events: int = 0  # повреждённые события, пропущенные при повторе
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
            target_losses=_count_losses([st for _, _, st in self.targets]),
            target_switches=_count_switches([tid for _, tid, _ in self.targets]),
            max_code_diff=max((abs(x - y) for a, b in zip(self.codes_rec, self.codes_new) for x, y in zip(a, b)),
                              default=0),
            partial_start=self.partial_start,
            calibration_ok=self.calibration_ok,
            skipped_events=self.skipped_events,
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


def _count_switches(ids: list[int | None]) -> int:
    """Сколько раз номер цели сменился на другой (сброс цели между ними — не смена)."""
    n, last = 0, None
    for tid in ids:
        if tid is None:
            continue
        if last is not None and tid != last:
            n += 1
        last = tid
    return n


def _count_losses(states: list[str]) -> int:
    return sum(1 for a, b in zip(states, states[1:]) if a == "tracking" and b == "lost")


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


def _time(e: dict) -> float | None:
    try:
        t = float(e["t"])
    except (KeyError, TypeError, ValueError):
        return None
    return t if math.isfinite(t) else None


def episode_calibration(ep: Episode) -> tuple[Calibration, bool]:
    """Калибровка из meta; (средние значения, False), если её нет или она не читается."""
    d = ep.meta.get("calibration")
    if d:
        try:
            return Calibration.from_json(d), True
        except Exception:
            pass
    return default_calibration(), False


def replay_controller(
    ep: Episode,
    calib: Calibration | None = None,
    composition: Composition | None = None,
    effort: EffortProfile | None = None,
) -> ReplayResult:
    """Прогоняет такты слежения эпизода через FramingController.

    Без параметров — с записанными калибровкой, композицией и характером (они пишутся в каждом такте),
    с параметрами — с новыми для всего эпизода. Регулятор сбрасывается при каждом включении слежения,
    как в Follower.enable(). Повреждённые события пропускаются и считаются (`skipped_events`).
    """
    res = ReplayResult()
    if calib is None:
        calib, res.calibration_ok = episode_calibration(ep)
    res.calib = calib
    # задержка из follow on (её выставил Follower.enable); в старых записях её нет — по виду потока
    meta_latency = calib.latency_for(ep.meta.get("stream_kind"))
    res.skipped_events = getattr(ep, "bad_lines", 0)
    events = []
    for e in ep.events():
        t = _time(e)
        if t is None:
            res.skipped_events += 1
        else:
            events.append((t, e))
    events.sort(key=lambda te: te[0])  # в zip события идут в порядке очереди, не строго по времени
    if events:
        res.t_start, res.t_end = events[0][0], events[-1][0]

    ctrl = FramingController(composition or COMPOSITIONS[DEFAULT_COMPOSITION], effort or EffortProfile.at(0.5),
                             calib, meta_latency)
    codes: Codes = (50, 50, 50)
    bounds: list[list[float]] = []  # [начало, конец] каждого включения слежения
    on = False
    implicit_t: float | None = None  # слежение «включилось» по такту, а событие follow on ещё не встретилось

    def start(t: float, latency: float) -> None:
        nonlocal codes, on
        ctrl.video_latency = latency  # как Follower.enable(): сброс регулятора и кодов
        ctrl.reset()
        codes = (50, 50, 50)
        bounds.append([t, t])
        on = True

    def late_follow_on(i: int, t: float) -> dict | None:
        """follow on, записанный чуть позже первого такта того же запуска."""
        for t2, e2 in events[i + 1:]:
            if t2 - t > LATE_FOLLOW_ON_S:
                return None
            if e2.get("kind") == "follow" and e2.get("on") is True:
                return e2
        return None

    def latency_of(e: dict | None) -> float:
        try:
            v = float(e["video_latency"])
            return v if math.isfinite(v) and v >= 0 else meta_latency
        except (KeyError, TypeError, ValueError):
            return meta_latency

    for i, (t, e) in enumerate(events):
        kind = e.get("kind")
        try:
            if kind == "follow" and "on" in e:  # без "on" — смена настроек: они есть в каждом такте
                if e["on"] and on and implicit_t is not None and t - implicit_t <= LATE_FOLLOW_ON_S:
                    pass  # тот же запуск: регулятор уже сброшен перед первым тактом
                elif e["on"]:
                    start(t, latency_of(e))
                elif on:
                    bounds[-1][1] = t
                    on = False
                implicit_t = None
            elif kind == "control":
                # сначала разобрать такт целиком: повреждённый не должен сдвинуть состояние регулятора
                inp = FramingInput(now=t, t_capture=None if e.get("t_capture") is None else float(e["t_capture"]),
                                   measure=_measure(e.get("measure")), zoom_pos=int(e.get("zoom_pos", 0x555)),
                                   aspect=float(e.get("aspect", 16 / 9)))
                rec = e.get("codes") or [50, 50, 50]
                rec_codes = (int(rec[0]), int(rec[1]), int(rec[2]))
                if not on:
                    first = late_follow_on(i, t)
                    start(t, latency_of(first))
                    implicit_t = t
                    if len(bounds) == 1 and first is None:
                        res.partial_start = True  # запись началась посреди слежения
                ctrl.set_composition(composition or _composition(e.get("composition"), ctrl.composition))
                ctrl.set_effort(effort or _effort(e.get("effort"), ctrl.effort))
                out = ctrl.step(inp)
                codes = output_codes(calib, out, inp.zoom_pos, codes)
                soft_x, soft_y = ctrl.effort.soft_x, ctrl.effort.soft_y
                res.t.append(t)
                res.err_x.append(float(out.error[0]))
                res.err_y.append(float(out.error[1]))
                res.size_err.append(out.size_error)
                res.codes_rec.append(rec_codes)
                res.codes_new.append(codes)
                res.state.append(out.state)
                res.zoom_pos.append(inp.zoom_pos)
                res.out_of_soft.append(out.state in ("holding", "moving")
                                       and (abs(out.error[0]) > soft_x or abs(out.error[1]) > soft_y))
                res.segment.append(len(bounds) - 1)
                bounds[-1][1] = t
            elif kind == "analysis":
                res.targets.append((t, e.get("target_id"), e.get("target_state") or "none"))
            elif kind == "position":
                z = e.get("zoom")
                res.position.append((t, int(e["pan"]), int(e["tilt"]), None if z is None else int(z)))
        except (KeyError, TypeError, ValueError, IndexError):
            res.skipped_events += 1
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


# --- повтор через распознавание -----------------------------------------------

MATCH_T_S = 0.01  # событие analysis относится к кадру, если его t_capture совпадает с моментом кадра
AGREE_IOU = 0.5  # рамки цели «было» и «стало» — один человек

Box = tuple[float, float, float, float]


@dataclass
class AnalysisSummary:
    frames: int
    failed_frames: int
    selected: bool  # щелчок из записи выбрал цель и в повторе
    switches_rec: int
    switches_new: int
    losses_rec: int
    losses_new: int
    agree_pct: float | None  # % кадров, где рамка цели «стало» совпадает с записанной
    skipped_events: int = 0

    def text(self) -> str:
        lines = [f"Кадров: {self.frames}"]
        if not self.selected:
            lines.append("Цель не выбрана: в записи нет щелчка по человеку или в повторе на его месте никого нет.")
        lines += [
            f"Смены цели: было {self.switches_rec}, стало {self.switches_new}",
            f"Потери цели: было {self.losses_rec}, стало {self.losses_new}",
        ]
        if self.agree_pct is not None:
            lines.append(f"Совпадение рамки цели с записанной: {self.agree_pct:.0f}% кадров")
        if self.failed_frames:
            lines.append(f"Кадров не распознано (ошибка детектора): {self.failed_frames}")
        if self.skipped_events:
            lines.append(f"Пропущено {self.skipped_events} повреждённых событий.")
        return "\n".join(lines)


@dataclass
class AnalysisReplay:
    # ряды по распознанным кадрам; координаты — в пикселях кадра записи
    t: list[float] = field(default_factory=list)
    seq: list[int] = field(default_factory=list)
    rec: list[tuple[int | None, str] | None] = field(default_factory=list)  # записанная цель; None — нет события
    new: list[tuple[int | None, str]] = field(default_factory=list)  # (номер, состояние) цели в повторе
    rec_box: list[Box | None] = field(default_factory=list)
    new_box: list[Box | None] = field(default_factory=list)
    people: list[int] = field(default_factory=list)  # сколько людей нашёл детектор
    click: tuple[float, float, float] | None = None  # (t, x, y) щелчка из записи, в пикселях кадра записи
    selected_id: int | None = None
    failed_frames: int = 0
    skipped_events: int = 0

    def summary(self) -> AnalysisSummary:
        rec = [r for r in self.rec if r is not None]
        both = sum(1 for a, b in zip(self.rec_box, self.new_box) if a is not None or b is not None)
        agree = sum(1 for a, b in zip(self.rec_box, self.new_box)
                    if a is not None and b is not None and _iou(a, b) >= AGREE_IOU)
        return AnalysisSummary(
            frames=len(self.t),
            failed_frames=self.failed_frames,
            selected=self.selected_id is not None,
            switches_rec=_count_switches([tid for tid, _ in rec]),
            switches_new=_count_switches([tid for tid, _ in self.new]),
            losses_rec=_count_losses([st for _, st in rec]),
            losses_new=_count_losses([st for _, st in self.new]),
            agree_pct=100.0 * agree / both if both else None,
            skipped_events=self.skipped_events,
        )


def _analysis_events(ep: Episode) -> tuple[list[tuple[float, dict]], int]:
    """События analysis, отсортированные по t_capture; (события, сколько повреждённых пропущено)."""
    out, bad = [], 0
    for e in ep.events("analysis"):
        try:
            tc = float(e["t_capture"])
            fw = float(e["frame_size"][0])
        except (KeyError, TypeError, ValueError, IndexError):
            bad += 1
            continue
        if math.isfinite(tc) and math.isfinite(fw) and fw > 0:
            out.append((tc, e))
        else:
            bad += 1
    out.sort(key=lambda te: te[0])
    return out, bad


def _nearest(items: list[tuple[float, dict]], t: float) -> tuple[float, dict] | None:
    return min(items, key=lambda te: abs(te[0] - t), default=None)


def _first_click(ep: Episode) -> tuple[float, float, float] | None:
    """(t, x, y) первого щелчка по человеку — в пикселях кадра анализа."""
    clicks = []
    for e in ep.events("target"):
        if e.get("how") != "click":
            continue
        t, x, y = _time(e), _num(e.get("x")), _num(e.get("y"))
        if t is not None and x is not None and y is not None:
            clicks.append((t, x, y))
    return min(clicks, default=None)


def _recorded_target(e: dict | None, k: float) -> tuple[tuple[int | None, str] | None, Box | None]:
    """Записанная цель кадра и её рамка (пересчитанная коэффициентом k в кадр записи)."""
    if e is None:
        return None, None
    tid = e.get("target_id")
    box = None
    for tr in e.get("tracks") or []:
        try:
            if tid is not None and tr["id"] == tid and tr["last_seen"] == e["t_capture"]:
                box = tuple(float(v) * k for v in tr["box"][:4])
        except (KeyError, TypeError, ValueError):
            continue
    return (tid, e.get("target_state") or "none"), box


def replay_analysis(ep: Episode, detector=None, tracker=None) -> AnalysisReplay:
    """Прогоняет кадры эпизода через детектор и PersonTracker — синхронно, по порядку записи.

    Так же, как Analyzer._run: детекция → гистограммы → tracker.update. Цель выбирается первым
    записанным щелчком (`target`, how="click") в тех же координатах, пересчитанных из кадра анализа
    в кадр записи (кадры в записи бывают уменьшены до 640×360). Щелчок применяется к состоянию
    трекера после последнего кадра до него — как в жизни, между двумя анализами.
    Без детектора берётся нейросеть (PoseDetector).
    """
    from ptztracker.video.frames import Frame
    from ptztracker.vision.tracker import PersonTracker, compute_histograms

    if detector is None:
        from ptztracker.vision.pose_detector import PoseDetector

        detector = PoseDetector()
    tracker = tracker or PersonTracker()
    res = AnalysisReplay()
    analysis, bad = _analysis_events(ep)
    res.skipped_events = getattr(ep, "bad_lines", 0) + bad
    by_seq: dict[int, list[tuple[float, dict]]] = {}
    for tc, e in analysis:
        try:
            by_seq.setdefault(int(e["seq"]), []).append((tc, e))
        except (KeyError, TypeError, ValueError):
            res.skipped_events += 1
    click = _first_click(ep)
    pending = click is not None
    n = 0

    def apply_click(img_w: int) -> None:
        nonlocal pending
        pending = False
        ct, cx, cy = click
        near = _nearest(analysis, ct)
        k = img_w / float(near[1]["frame_size"][0]) if near else 1.0  # из кадра анализа в кадр записи
        res.click = (ct, cx * k, cy * k)
        res.selected_id = tracker.select_at(cx * k, cy * k)

    for t, seq, img in ep.frames():
        if pending and n > 0 and t > click[0]:
            apply_click(img.shape[1])
        try:
            people = detector.detect(Frame(image=img, t_capture=t, seq=seq))
        except Exception:
            res.failed_frames += 1
            continue
        hists = compute_histograms(img, people)
        tracker.update(people, t, img, hists)
        n += 1
        if pending and t >= click[0]:  # щелчок раньше первого кадра (или ровно на нём)
            apply_click(img.shape[1])

        near = _nearest(by_seq.get(seq, []), t)
        ev = near[1] if near is not None and abs(near[0] - t) <= MATCH_T_S else None
        k = img.shape[1] / float(ev["frame_size"][0]) if ev is not None else 1.0
        rec, rec_box = _recorded_target(ev, k)
        tgt = tracker.target
        state = tracker.target_state
        res.t.append(t)
        res.seq.append(seq)
        res.rec.append(rec)
        res.rec_box.append(rec_box)
        res.new.append((tracker.target_id, state))
        res.new_box.append(tuple(float(v) for v in tgt.box) if tgt is not None and state == "tracking" else None)
        res.people.append(len(people))
    return res
