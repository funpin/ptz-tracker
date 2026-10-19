# PTZ Tracker — Этап 3: камера сама ведёт человека

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Оператор выбрал человека кликом и нажал «СЛЕЖЕНИЕ». После этого камера сама плавно поворачивается и зумирует, удерживая человека в выбранной композиции:
- пояс — по центру, слева или справа;
- в полный рост;
- общий план.

Ползунок «Мягко ↔ Резко» задаёт характер движения. Любое ручное действие или Esc мгновенно выключает слежение. Мастер калибровки за ~2 минуты измеряет реальные скорости камеры.

**Architecture:** Новый пакет `ptztracker/control`:
- `CameraModel` — таблица «код скорости → скорость картинки» с учётом зума, плюс динамика камеры (мёртвое время, разгон);
- `Composition` — где в кадре должен быть человек и какого размера, по голове и плечам;
- `EffortProfile` — числа характера движения;
- `FramingController` — чистая функция шага регулятора (тестируется без потоков и сети);
- `Follower` — поток внутри `CameraSession`. Читает `AnalysisResult`, вызывает регулятор, переводит скорости в коды и отправляет `#PTS`/`#Z` через планировщик с приоритетом TRACKING;
- `CalibrationRunner` — мастер калибровки.

Вся логика проверяется **замкнутым контуром на симуляторе**, который доводится до поведения реальной AW-HE42 по замерам.

**Tech Stack:** как на этапах 1–2 (numpy, PySide6), новых зависимостей нет.

**Spec:** `docs/superpowers/specs/2026-10-19-ptz-tracker-design.md` — §8 (регулятор), §4 (композиции, рабочая крупность), §7 (замеры с живой камеры), §9 (ручной перехват, аварийный стоп), §10 (кнопки и ползунок), §13 этап 3.

## Global Constraints

**Замеры с живой AW-HE42** (§7 спеки) — основа всех чисел по умолчанию:
- Задержка «команда → движение в кадре» по MJPEG — **344 мс**, по H.264 — ~830 мс. Слежение рассчитано на MJPEG 640×360. Если выбран H.264, регулятор должен учитывать бо́льшую задержку (параметр задержки видео берётся от потока).
- Разгон камеры до 90% скорости ~0,85 с, остановка ~0,8 с. Модель — мёртвое время + инерционное звено, **τ ≈ 0,37 с**.
- Скорость pan при зуме `0x73F` (в долях ширины кадра в секунду, fw/s): код 55 → ~0; 60 → 0,034; 70 → 0,33; 80 → 0,50. Шкала сильно нелинейна, рабочие коды — 56–75.
- Анализ 13–14 к/с на целевом ПК.

**Единицы и направления:**
- Все скорости в регуляторе — в долях кадра: fw/s по горизонтали, fh/s по вертикали.
- Зум — в логарифме масштаба в секунду (`ln/s`).
- Знаки: pan > 0 — камера вправо, человек в кадре уезжает влево; tilt > 0 — камера вверх; zoom > 0 — к теле.

**Тело человека** в единицах U (U — вертикаль «нос → линия плеч»): макушка −1,0; нос 0; плечи 1,0; середина груди 2,0; пояс 3,7; бёдра 4,3; колени 7,0; ступни 10,3. Размер для зума меряется по U: он не меняется, когда человек поворачивается боком.

**Безопасность:**
- Ручное действие (джойстик, зум, стрелки, пресет), Esc или смена камеры **выключают слежение немедленно**: отправляется стоп, а дальше идут ручные команды.
- Цель потеряна — камера плавно останавливается и ждёт (поиск — этап 4).
- Анализ стоит дольше 0,5 с — стоп.

**Пульт.** Пока слежение включено, оператор пульта эту камеру не трогает. Автоматическое обнаружение пульта — этап 4. Это правило пишется в README.

Интерфейс русский; всё работает на Windows 10, разрабатывается и тестируется на Mac через симулятор.

## Review Focus

1. **Человек стоит, качается у кафедры.** Камера не дёргается и не «плавает»: за 10 с покоя — не больше одной смены направления pan, и скорость возвращается к 0. Тест в Task 6.
2. **Человек пошёл и остановился.** Камера трогается плавно (код растёт без скачков больше чем на 10 за команду), идёт с упреждением, останавливается без перелёта: перелёт ≤ половины мёртвой зоны. Тест в Task 6.
3. **Слежение включено, а цель потеряна или анализ встал** (сбой видео, модель). Камера останавливается за ≤ 1 с и не уезжает. Тест в Task 5.
4. **Оператор хватает джойстик или жмёт Esc во время движения.** Слежение выключается в тот же момент, ручная команда уходит первой, автоматика больше ничего не шлёт. Тест в Task 5 и Task 8.
5. **Зум и поворот вместе.** Наезд не начинается, пока камера едет; отъезд срабатывает сразу, если человек подходит к краю. Зум не «качается» туда-сюда. Тест в Task 4 и Task 6.

---

## Структура файлов

```
src/ptztracker/control/__init__.py
src/ptztracker/control/camera_model.py   — SpeedTable, ZoomTable, CameraDynamics, Calibration (+ JSON)
src/ptztracker/control/composition.py    — COMPOSITIONS, BodyMeasure, measure_body, Framing
src/ptztracker/control/effort.py         — EffortProfile.at(x)
src/ptztracker/control/framing.py        — FramingController (чистый шаг регулятора)
src/ptztracker/control/follower.py       — Follower (поток слежения в сессии)
src/ptztracker/control/calibration.py    — CalibrationRunner
src/ptztracker/ui/calibration_dialog.py
src/ptztracker/ui/follow_panel.py        — кнопка СЛЕЖЕНИЕ, композиции, ползунок усилия
tests/control/…
```
Изменяются:
- `sim/ptz_model.py` — профиль HE42;
- `sim/frame_source.py` и `camera/session.py` — follower, ручной перехват, задержка потока;
- `config.py` — `composition`, `effort`, `calibration`;
- `ui/preview.py` — зоны на превью;
- `ui/main_window.py`, `README.md`.

---

### Task 0: Симулятор ведёт себя как живая AW-HE42

**Files:**
- Modify: `src/ptztracker/sim/ptz_model.py`, `src/ptztracker/camera/session.py` (задержка видео симулятора)
- Test: `tests/sim/test_ptz_model.py` (новые тесты; старые, завязанные на τ=0,12, получают параметр `tau=0.12`)

**Interfaces:**
- `SimPtz(..., tau: float = 0.37, dead_time: float = 0.08)`.
- Скорость pan/tilt по коду — кусочно-линейная таблица град/с, `HE42_DEG_S`: {50:0, 53:0, 55:0.05, 58:0.5, 60:1.24, 62:2.2, 65:4.5, 68:8.5, 70:12, 75:15, 80:18, 85:24, 90:35, 99:60}. Значение — по |code−50|+50, знак по стороне. «Speed with zoom» по-прежнему делит на `zoom_ratio`.
- Мёртвое время: новая заданная скорость начинает действовать через `dead_time` после команды (очередь команд с временем).
- В `CameraSession` для симулятора `latency_s = 0.15` (как MJPEG; было 0,30).
- [ ] **Step 1: Failing tests:**
```python
def test_he42_speed_table_shape():   # 55 почти стоит, 60 ≈ 1.24°/с, 70 ≈ 12°/с при zoom=0 без speed_with_zoom
    s = SimPtz(speed_with_zoom=False); s.handle("#PTS7050"); run(s, 3.0); v70 = s.pan_vel
    s2 = SimPtz(speed_with_zoom=False); s2.handle("#PTS5550"); run(s2, 3.0)
    assert abs(v70 - 12) < 0.5 and abs(s2.pan_vel) < 0.1
def test_ramp_like_he42():   # до 90% скорости за 0.75–1.0 с после команды
def test_dead_time():        # через 0.05 с после команды скорость ещё 0, через 0.2 с — уже > 0
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** Старые тесты, которым важна быстрая динамика, получают `SimPtz(tau=0.12, dead_time=0.0)`. **Step 4:** весь набор зелёный.
- [ ] **Step 5: Commit** `feat: simulator matches measured AW-HE42 dynamics`

---

### Task 1: Модель камеры и калибровочные данные

**Files:**
- Create: `src/ptztracker/control/__init__.py`, `control/camera_model.py`
- Modify: `src/ptztracker/config.py` (`CameraConfig.calibration: dict | None = None`)
- Test: `tests/control/test_camera_model.py`

**Interfaces:**
- `SpeedTable(points: dict[int, list[tuple[int, float]]])` — для каждой позиции зума (`#GZ`, 0x555–0xFFF) список (код 51–99, fw/s); симметрия для кодов < 50.
  - `.speed(code, zoom) -> float`: fw/s со знаком. Линейно между кодами, между зумами — по ближайшим двум позициям (линейно по позиции), за краями — по крайней.
  - `.code_for(v, zoom, current: int = 50, hysteresis: float = 0.15) -> int`: минимальный код, дающий |v|. Если |v| меньше половины скорости наименьшего «движущегося» кода (скорость > 0,005 fw/s) — 50. Если текущий код даёт скорость в пределах ±`hysteresis`·|v| от нужной — текущий код (без дребезга 63↔64).
- `ZoomTable(points: list[tuple[int, float]])` — (код 51–99, ln/s); `.rate(code)`, `.code_for(rate, current=50)` — по тем же правилам.
- `CameraDynamics(dead_time=0.08, tau=0.37)`. Модель картинки по оси:
  - `.command(t, v)` — записывает команду;
  - `.velocity(t) -> float` — модельная скорость;
  - `.displacement(t0, t1) -> float` — сдвиг картинки за [t0, t1], интеграл модельной скорости (численно, шаг 10 мс).
- `Calibration(pan: SpeedTable, zoom: ZoomTable, dead_time: float, tau: float, video_latency: dict[str, float], created: str | None)`:
  - `.to_json() -> dict`, `Calibration.from_json(d) -> Calibration`;
  - `default_calibration() -> Calibration`: из замеров §7 — pan при зуме 0x73F: {53:0, 55:0.0015, 58:0.012, 60:0.034, 62:0.07, 65:0.15, 68:0.26, 70:0.33, 75:0.42, 80:0.50, 90:0.75, 99:1.2}; та же таблица для всех зумов (поведение «Speed with zoom»). Зум: {55:0.02, 60:0.05, 65:0.1, 75:0.27, 85:0.5, 99:0.8}. dead_time 0.08, tau 0.37, video_latency {"mjpeg": 0.12, "h264": 0.6, "sim": 0.15}.
- [ ] **Step 1: Failing tests:**
```python
def test_speed_interpolates_and_symmetric(): t=default_calibration().pan; assert abs(t.speed(60,0x73F)-0.034)<1e-9 and abs(t.speed(40,0x73F)+0.034)<1e-9
def test_code_for_minimal_and_deadband():  # v=0.0005 → 50; v=0.034 → 60; v=-0.33 → 30
def test_code_for_hysteresis():            # current=64, нужная скорость чуть выше speed(64) → 64
def test_zoom_interpolation_between_positions():
def test_dynamics_displacement_step():     # команда 0.3 fw/s в t=0 → за первые 0.08 с сдвиг 0; к 2 с скорость >0.29
def test_calibration_json_roundtrip_and_config(tmp_path):  # через ConfigStore
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Commit** `feat: camera speed model and calibration data`

---

### Task 2: Композиции и измерение человека

**Files:**
- Create: `src/ptztracker/control/composition.py`
- Test: `tests/control/test_composition.py`

**Interfaces:**
- `@dataclass(frozen=True) Composition(key: str, title: str, anchor: tuple[float, float], u_frac: float)`:
  - `anchor` — где в кадре должен быть **нос** (доли ширины и высоты);
  - `u_frac` — желаемая U в долях высоты кадра.
- `COMPOSITIONS: dict[str, Composition]`:
  - `"waist_center"` «Пояс · центр» — (0.50, 0.25), 0.19;
  - `"waist_left"` «Пояс · слева» — (0.38, 0.25), 0.19;
  - `"waist_right"` «Пояс · справа» — (0.62, 0.25), 0.19;
  - `"full"` «Рост» — (0.50, 0.17), 0.075;
  - `"wide"` «Общий» — (0.50, 0.40), 0.035.
  - По умолчанию `"waist_center"`.
- `@dataclass BodyMeasure(nose: tuple[float, float], u: float | None, confident: bool)` — в долях кадра (x / ширина, y / высота).
- `measure_body(track_keypoints, box, frame_size, last_u: float | None) -> BodyMeasure | None`:
  - Есть нос (conf ≥ 0.3) и оба плеча: `u` = середина плеч − нос по y (если > 0), `confident=True`.
  - Только плечи: нос оценивается как середина плеч − `last_u` по y (x — середина плеч), `u = None`.
  - Нет плеч: нос = верх рамки + 1,0·`last_u` (или + 0,08·высоты рамки, если `last_u` нет), x — центр рамки, `confident=False`.
  - Пустые ключевые точки и пустая рамка дают `None`.
- [ ] **Step 1: Failing tests:**
```python
def test_measure_frontal():   # нос (320,100), плечи (290,160),(350,160) в кадре 640×360 → nose=(0.5,0.2778), u=60/360
def test_measure_profile_shoulders_only_uses_last_u():
def test_measure_box_fallback():
def test_compositions_sane():  # все anchor в (0,1), u_frac>0; waist_left.anchor.x < 0.5 < waist_right.anchor.x
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Commit** `feat: compositions and body measurement from keypoints`

---

### Task 3: Профиль усилия

**Files:**
- Create: `src/ptztracker/control/effort.py`
- Test: `tests/control/test_effort.py`

**Interfaces:**
- `@dataclass(frozen=True) EffortProfile(dead_x, dead_y, soft_x, soft_y, gain, v_max, a_max, lead, zoom_dead, zoom_gain, zoom_max)`:
  - `dead_*`, `soft_*` — полуширины мёртвой и мягкой зон в долях кадра;
  - `gain` — 1/с;
  - `v_max` — fw/s;
  - `a_max` — fw/s²;
  - `lead` — «место для взгляда»: доля кадра на 1 fw/s скорости человека, не больше 0,08;
  - `zoom_dead` — ln;
  - `zoom_gain` — 1/с;
  - `zoom_max` — ln/s.
- `EffortProfile.at(x: float) -> EffortProfile` — линейная интерполяция между тремя точками (x 0 / 0,5 / 1), x обрезается до [0, 1]:

| поле | Мягко (0) | Обычно (0.5) | Резко (1) |
|---|---|---|---|
| dead_x / dead_y | 0.07 / 0.06 | 0.05 / 0.045 | 0.03 / 0.03 |
| soft_x / soft_y | 0.25 / 0.20 | 0.22 / 0.18 | 0.18 / 0.15 |
| gain | 0.9 | 1.4 | 2.2 |
| v_max | 0.30 | 0.50 | 0.80 |
| a_max | 0.25 | 0.45 | 0.80 |
| lead | 0.10 | 0.08 | 0.06 |
| zoom_dead | 0.15 | 0.12 | 0.08 |
| zoom_gain | 0.5 | 0.8 | 1.2 |
| zoom_max | 0.15 | 0.25 | 0.40 |

- [ ] **Step 1: Failing test:** `at(0)`, `at(0.5)`, `at(1)` совпадают с таблицей; `at(0.25)` — середина между первыми двумя; `at(-1) == at(0)`.
- [ ] **Step 2–4.** **Step 5: Commit** `feat: effort profile`

---

### Task 4: Регулятор FramingController (чистый шаг)

**Files:**
- Create: `src/ptztracker/control/framing.py`
- Test: `tests/control/test_framing.py`

**Interfaces:**
- `@dataclass(frozen=True) FramingInput(now: float, t_capture: float | None, measure: BodyMeasure | None, zoom_pos: int, aspect: float = 16/9)`:
  - `measure=None` — цели нет;
  - `t_capture` — время прихода кадра, по которому измерено.
- `@dataclass(frozen=True) FramingOutput(pan: float, tilt: float, zoom: float, state: Literal["idle","holding","moving"], error: tuple[float, float], size_error: float | None)`:
  - `pan`, `tilt` — fw/s и fh/s;
  - `zoom` — ln/s.
- `FramingController(composition: Composition, effort: EffortProfile, calib: Calibration, video_latency: float)`:
  - `.set_composition(c)`, `.set_effort(e)`, `.reset()`;
  - `.step(inp: FramingInput) -> FramingOutput` — вызывается ~25 раз/с; новый замер приходит ~13 раз/с, между замерами — прогноз.
- Алгоритм по каждой оси (pan по x, tilt по y):
  1. **Где человек сейчас.** Кадр снят в `t_capture − video_latency`. За время от съёмки до `now` камера сместила картинку на `dyn.displacement(t_shot, now)`, поэтому позиция «сейчас» = измеренная − сдвиг. Это компенсация задержки.
  2. **Скорость человека «в мире».** α-β фильтр по «мировой» позиции = измеренная + накопленный сдвиг камеры к моменту съёмки (α=0,35, β=0,05). Скорость меньше 0,02 fw/s считается нулём.
  3. **Ошибка** = позиция сейчас − (anchor + сдвиг места для взгляда). Место для взгляда = −`lead`·v_world, ограничено ±0,08 и сглажено (τ 0,8 с).
  4. **Зоны с гистерезисом.**
     - Состояние `holding`: команда 0, пока |ошибка| < `dead` и |v_world| < 0,05.
     - Переход в `moving`: |ошибка| > `dead` или |v_world| ≥ 0,05.
     - Возврат в `holding`: |ошибка| < 0,4·`dead` и |v_world| < 0,03.
  5. **Желаемая скорость в `moving`:** v = v_world + gain·shape(ошибка), где shape — ошибка за вычетом 0,4·dead, умноженная на 1 внутри soft и на 1,8 за soft (догнать у края).
  6. **Ограничения.** |v| ≤ `v_max`. Изменение v за шаг ≤ `a_max`·dt (плавный разгон и торможение поверх механики камеры).
  7. Записать команду в динамику: `dyn.command(now, v)`.
- **Зум** (по `measure.u`):
  - Ошибка размера = ln(u / u_frac_desired); положительная — человек крупнее нужного, отъезжаем.
  - Мёртвая зона `zoom_dead` с гистерезисом 0,5.
  - **Наезд** (ошибка < 0) только если состояние pan и tilt — `holding` дольше 1,0 с.
  - **Отъезд** разрешён всегда. Если |ошибка pan| > `soft_x` (человек у края) — отъезд со скоростью `zoom_max` даже без замера размера.
  - Скорость зума = −`zoom_gain`·ошибка, ограничена `zoom_max`, с тем же ограничением ускорения (0,5 ln/s²).
- **Нет цели** (`measure is None` в течение > 0,3 с): все скорости плавно к 0 с `a_max`, состояние `idle`.
- **Tilt** использует ту же логику с `dead_y`/`soft_y` и переводом fh ↔ fw через `aspect`. Камера одна, поэтому динамика у tilt своя, но с теми же `dead_time`/`tau`.
- [ ] **Step 1: Failing tests** (виртуальное время; человек — синтетические BodyMeasure; «камера» — та же CameraDynamics, двигающая картинку):
```python
def test_holds_still_when_inside_dead_zone():          # ошибка 0.03 при dead 0.05 → pan == 0 всё время
def test_starts_smoothly_and_accel_limited():          # ошибка 0.2 → |Δpan| за шаг ≤ a_max*dt; знак верный (человек справа → pan > 0)
def test_feedforward_matches_walking_speed():          # человек идёт 0.2 fw/s в мире, контур замкнут на модель → через 3 с |pan-0.2|<0.05, |ошибка|<soft
def test_stops_without_overshoot_after_walk():         # идёт 3 с, останавливается → через 4 с pan==0, перелёт ≤ dead/2
def test_latency_compensation_reduces_overshoot():     # тот же сценарий с video_latency=0 vs 0.15 у «камеры» 0.15 → перелёт меньше с компенсацией
def test_no_target_ramps_to_zero():
def test_zoom_in_only_when_holding_and_zoom_out_at_edge():   # Review Focus 5
def test_zoom_does_not_hunt():                          # размер колеблется ±8% вокруг цели → zoom == 0 всё время
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Commit** `feat: framing controller with latency compensation, zones and effort`

---

### Task 5: Follower — слежение внутри сессии, ручной перехват

**Files:**
- Create: `src/ptztracker/control/follower.py`
- Modify: `src/ptztracker/camera/session.py`, `src/ptztracker/config.py` (`composition: str = "waist_center"`, `effort: float = 0.5`)
- Test: `tests/control/test_follower.py`

**Interfaces:**
- `Follower(analyzer, scheduler, calib: Calibration, video_latency: float, composition: Composition, effort: EffortProfile, query_zoom: Callable[[], int | None])`:
  - `.enable() -> bool`: False, если цели нет (`analyzer.latest().target_id is None`);
  - `.disable(reason: str)`: шлёт `#PTS5050` и `#Z50` с приоритетом TRACKING, `only_if_changed=False`;
  - `.enabled: bool`, `.last_reason: str | None`;
  - `.set_composition(c)`, `.set_effort(e)`;
  - `.output -> FramingOutput | None` — для отрисовки;
  - `on_change: Callable[[bool, str|None], None] | None`.
- Поток 25 Гц:
  - берёт `analyzer.latest()`;
  - если цель `tracking`, из её трека строит `measure_body`; иначе `measure=None`;
  - если результат анализа старше 0,5 с — `disable("анализ остановился")`;
  - вызывает `controller.step`;
  - переводит в коды: `pan_code = calib.pan.code_for(out.pan, zoom, current)`. Для tilt — `out.tilt` · (9/16), в fw-эквивалент; коды tilt по той же таблице;
  - `zoom_code = calib.zoom.code_for(out.zoom)`;
  - `scheduler.submit("pt", pan_tilt_speed(p, t), Priority.TRACKING)`, `submit("zoom", zoom_speed(z), Priority.TRACKING)`.
- Зум: `query_zoom()` раз в 0,5 с, вызывается **вне** цикла управления, в отдельном таймере потока. Между опросами позиция интегрируется по таблице зума.
- `CameraSession`:
  - создаёт `self.follower` после анализатора (если `analysis=True`);
  - `video_latency` берётся из калибровки по типу потока (`mjpeg` / `h264` / `sim`);
  - **любая ручная команда** (`set_pan_tilt_velocity`, `set_zoom_velocity`, `recall_preset`, `store_preset`, `set_focus_velocity`) сначала вызывает `follower.disable("ручное управление")`, если слежение включено; `emergency_stop()` — `disable("аварийный стоп")`;
  - `stop()` первым делом выключает follower.
- [ ] **Step 1: Failing tests (симулятор):**
```python
def test_enable_requires_target():
def test_follows_walking_sim_person():   # сим-сессия, цель выбрана, слежение вкл → за 8 с ошибка по x в среднем < 0.08 и человек всё время в кадре
def test_manual_joystick_disables_immediately():  # Review Focus 4: set_pan_tilt_velocity(…) → follower.enabled False, следующая отправленная PT-команда — ручная
def test_emergency_stop_disables():
def test_stale_analysis_stops_camera():   # Review Focus 3: остановить analyzer → ≤1 с: enabled False, последняя PT-команда #PTS5050
def test_target_lost_stops_camera():      # Review Focus 3: цель сброшена (clear_target) во время движения → pan_code 50 за ≤1 с, слежение остаётся вкл
```
- [ ] **Step 2–4.** **Step 5: Commit** `feat: follower thread with manual takeover and safety stops`

---

### Task 6: Замкнутый контур на симуляторе — поведение «как оператор»

**Files:**
- Test: `tests/control/test_closed_loop.py` (интеграционные, ~1 мин; помечены `@pytest.mark.slow`, но в CI запускаются)
- Modify (по результатам): числа в `effort.py` / `default_calibration()` — только через тесты этой задачи.

**Interfaces:** только потребляет Task 0–5. Метрики считаются по `SimPtz.received` и по истинной разметке кадра:
- число смен направления pan;
- макс. скачок кода за команду;
- среднее/макс. |ошибки|;
- доля кадров, где нос в кадре.
- [ ] **Step 1: Tests:**
```python
def test_static_speaker_no_hunting():      # Review Focus 1: человек стоит 10 с (сцена с флагом stand_still) → ≤1 смены направления pan, последние 5 с pan_code==50
def test_walk_and_stop_like_operator():    # Review Focus 2: сценарий SyntheticStage, «Обычно»: нос в кадре 100%, |ошибка| среднее < 0.08, скачок кода ≤ 10, перелёт после остановки ≤ dead/2
def test_soft_vs_sharp():                   # «Мягко»: меньше команд и макс. скорость ниже, чем у «Резко»; «Резко»: средняя ошибка меньше
def test_zoom_converges_to_waist():         # старт с общего плана → через 10 с u в пределах ±15% от waist u_frac, без «качания» (≤2 смены направления зума)
def test_with_h264_latency():               # задержка видео 0.6 с: перелёт ≤ dead, без раскачки
```
Синтетической сцене нужен режим «стоит на месте»: `SyntheticStage(script="stand")`, где человек стоит у кафедры и слегка покачивается ±1% кадра.
- [ ] **Step 2: Run** — часть тестов может упасть. Подстройка: менять только числа профиля и калибровки по умолчанию и ширину гистерезиса. Каждое изменение — с записью в ledger «было → стало → какой тест».
- [ ] **Step 3: All green.** **Step 4: Commit** `test: closed-loop operator-like behaviour on simulator; tune defaults`

---

### Task 7: Мастер калибровки

**Files:**
- Create: `src/ptztracker/control/calibration.py`, `src/ptztracker/ui/calibration_dialog.py`
- Modify: `src/ptztracker/ui/main_window.py` (меню «Сервис → Калибровка камеры…»)
- Test: `tests/control/test_calibration.py`

**Interfaces:**
- `CalibrationRunner(client_send: Callable[[str], str], source: FrameSource, progress: Callable[[str], None], zooms: list[int] | None = None, codes: tuple[int, ...] = (53, 55, 57, 59, 61, 63, 65, 67, 70, 75, 80, 90))`. `.run() -> Calibration`:
  1. Запомнить положение и зум (`#APC`, `#GZ`).
  2. Для каждого зума из `zooms` (по умолчанию: текущий, 0x555 и середина между ними и 0xFFF), через `#AXZ` с ожиданием 2 с: для каждого кода — разгон в чередующихся направлениях. По кадрам (фазовая корреляция, `diagnostics.motion.measure_shift`) измеряется установившаяся скорость в fw/s. Стоп по 25% кадра или 1,5 с.
  3. Ступенька на коде 70 → время до первого движения (`dead_time` + задержка видео) и до 63% скорости (`tau`). Время до первого движения записывается в `video_latency[тип потока]`, т. е. с вычетом `dead_time` по умолчанию 0,08.
  4. Зум: коды 60 / 75 / 90 по 0,6 с туда и обратно → ln/s через `estimate_scale`.
  5. Вернуть положение и зум.
  6. Пропуски (корреляция не нашла сдвиг — однотонная сцена) заполнить интерполяцией. Больше половины пропусков — `RuntimeError("Слишком однотонная картинка — наведите камеру на сцену с деталями")`.
- Результат сохраняется в `CameraConfig.calibration`. Диалог — как диагностика: флажок «камера не в эфире», прогресс, итог «готово, N замеров».
- [ ] **Step 1: Failing tests (симулятор):**
```python
def test_calibration_recovers_sim_table():   # результат на зуме 0x555 совпадает с истиной симулятора (HE42_DEG_S → fw/s) в пределах ±15% для кодов 60–80; tau 0.25–0.5
def test_camera_returned_home():
def test_flat_scene_reports_error():         # однородная сцена → RuntimeError с понятным текстом
```
- [ ] **Step 2–4.** **Step 5: Commit** `feat: camera calibration wizard`

---

### Task 8: Интерфейс слежения

**Files:**
- Create: `src/ptztracker/ui/follow_panel.py`
- Modify: `src/ptztracker/ui/main_window.py`, `src/ptztracker/ui/preview.py`
- Test: `tests/ui/test_follow_panel.py`

**Interfaces:**
- `FollowPanel(QWidget)` (над «Цель»):
  - большая кнопка-переключатель `follow_btn`: «● СЛЕЖЕНИЕ ВКЛ (пробел)» — зелёная / «СЛЕЖЕНИЕ ВЫКЛ» — серая;
  - кнопки композиций из `COMPOSITIONS` (`QButtonGroup`, по одной выбрано);
  - ползунок `effort_slider` 0–100 с подписями «Мягко» / «Резко»;
  - строка состояния слежения (`last_reason` при выключении, например «выключено: ручное управление»).
- Пробел (в фильтре клавиш окна) переключает слежение. Клавиши 1–5 выбирают композицию.
- Включение без цели — подсказка «Сначала кликните по человеку на видео».
- Композиция и усилие сохраняются в камеру (`CameraConfig.composition`, `.effort`) и сразу передаются в follower.
- Превью (`PreviewWidget.set_follow(follower)`): при включённом слежении рисуются мёртвая зона (зелёный пунктир) и мягкая (оранжевый пунктир) вокруг anchor композиции с учётом места для взгляда.
- Если камера не откалибрована (`calibration is None`), в панели предупреждение «Камера не откалибрована — Сервис → Калибровка (2 мин). Работаю по средним значениям.»
- [ ] **Step 1: Failing tests:**
```python
def test_follow_button_requires_target(qtbot, tmp_path):
def test_space_toggles_follow_after_target(qtbot, tmp_path):
def test_joystick_turns_follow_off_and_shows_reason(qtbot, tmp_path):   # Review Focus 4
def test_composition_and_effort_saved(qtbot, tmp_path):
def test_zones_painted_when_following(qtbot, tmp_path):    # grab() не падает, follower.output не None
```
- [ ] **Step 2–4.** **Step 5: Ручная проверка** `--sim`: клик по человеку → пробел → камера ведёт его. Джойстик → слежение выкл. Скриншот — в ledger. **Step 6: Commit** `feat: follow panel, compositions, effort slider, zones overlay`

---

### Task 9: Сборка, README, финал

**Files:**
- Modify: `README.md`, `tests/test_packaging.py`

- README, раздел «Слежение»:
  - как включить: клик → пробел или кнопка;
  - композиции и клавиши 1–5;
  - «Мягко ↔ Резко»;
  - что выключает слежение;
  - **правило пульта**;
  - калибровка (когда и как);
  - выбор потока: для слежения — «JPEG».
- [ ] Packaging-тест: README содержит «Слежение» и «Калибровка».
- [ ] Сборка на Mac (`pyinstaller`), собранное `--sim` — 20 с без ошибок, слежение включается из UI.
- [ ] **Commit** `docs: README for automatic following and calibration`

---

## После этапа 3

1. Пользователь пересобирает программу, ставит поток «JPEG», запускает «Калибровку» (камера не в эфире), затем проверяет слежение на реальном докладе. По его отзыву подстраиваются профили усилия.
2. Этап 4: поиск при потере цели, обнаружение пульта по опросу `#APC`, автоматическое возобновление после ручного вмешательства, «своя зона», классический и гибридный методы.
