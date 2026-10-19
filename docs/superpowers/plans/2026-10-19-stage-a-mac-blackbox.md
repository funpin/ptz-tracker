# PTZ Tracker — Шаг А: Mac, «чёрный ящик», доступ агента к живой камере

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Программа работает на MacBook пользователя с живой AW-HE42 и постоянно пишет последние 5 минут работы: кадры, людей, цель, решения регулятора, команды и **фактическое** положение камеры. Оператор жмёт F9, и эпизод сохраняется в zip. Эпизод повторяется на Mac без камеры: через распознавание (видно перескоки цели) и через регулятор (видно рывки). Агент (Claude Code на том же Mac) запускает запись, слежение и калибровку из командной строки и читает состояние окна через локальный API.

**Architecture:** новый пакет `ptztracker/recorder/`:
- `events.py` — типы событий и их перевод в JSON;
- `ring.py` — кольцевой буфер в памяти;
- `writer.py` — фоновый поток: JPEG и запись zip;
- `reader.py` — чтение эпизода;
- `replay.py` — повтор через анализ и регулятор;
- `plots.py` — графики через cv2, без новых зависимостей.

Существующие классы получают **необязательный** крючок `recorder` (по умолчанию `NullRecorder`). Без записи их поведение не меняется.

Новые модули:
- `ptztracker/lab.py` — команды без окна;
- `ptztracker/api/server.py` — локальный HTTP API на `127.0.0.1` из стандартной библиотеки.

**Tech Stack:** без новых зависимостей. Python stdlib (`zipfile`, `http.server`, `queue`), numpy, cv2, PySide6; onnxruntime на Mac — с `CoreMLExecutionProvider`.

**Spec:** `docs/superpowers/specs/2026-10-19-stage-a-mac-blackbox.md`.

## Global Constraints

- **Инварианты эфира** (AGENTS.md) не ослабляются. Запись — только наблюдатель: исключение в записи никогда не выключает слежение и не задерживает команду. Все вызовы `recorder.*` — неблокирующие (`put_nowait`), ошибки ловятся внутри и логируются раз в 10 с.
- **Потоки:** Qt, анализ, слежение и планировщик не ждут запись. Если очередь записи (2000 элементов) переполнена, отбрасываются кадры, а события — нет: для событий отдельная очередь без кадров.
- **Пароли:** любой URL проходит `redact_url`, `meta.json` не содержит `password`. Это проверяет тест, который ищет пароль симулятора во всех байтах zip.
- **Время:** `time.monotonic()` везде, в `meta.json` — пара `(monotonic, wallclock)` для привязки.
- **Агент двигает камеру** только если оператор отметил «камера НЕ в эфире» (в окне) или передал флаг `--not-on-air` (в `lab`). Esc и любое ручное действие выключают «Разрешить управление агенту».
- Положение камеры (`#APC`) опрашивается **раз в 0,5 с** вместе с зумом в `Follower._poll_zoom`, а не раз в 0,3 с, как в спеке. Так опрос не отнимает окна у команд: камера принимает ~7 команд/с.
- Код и комментарии на русском, как в проекте. `ruff check src tests` чистый, быстрый набор ~1 мин.

## Review Focus

1. **Запись не влияет на слежение.** На симуляторе с записью частота анализа падает не больше чем на 5%, тесты замкнутого контура проходят как раньше. Тест в Task 2.
2. **Пароль нигде не виден.** Пароля нет ни в zip, ни в ответах API, ни в логе. Тесты в Task 1, Task 7.
3. **Повтор детерминирован.** Слежение на симуляторе 30 с → повтор через регулятор даёт те же коды pan/tilt/zoom (±1 код на округлении). Тест в Task 4.
4. **Агент не двигает камеру в эфире.** Без флага «НЕ в эфире» `lab follow/calibrate` и управляющие методы API отказывают; Esc в окне выключает управление агентом. Тесты в Task 6, Task 7.
5. **F9 работает всегда.** Работает без камеры, без цели и при выключенном слежении. Сохранение не подвешивает окно: zip пишется в фоне, статус показывает «Сохраняю… → Сохранено: путь».

---

## Структура файлов

```
src/ptztracker/
  recorder/__init__.py        Recorder (Protocol), NullRecorder
  recorder/events.py          kind-константы, to_json(track/output/…), redact
  recorder/ring.py            RingBuffer(seconds) — события и кадры по времени
  recorder/writer.py          Recorder: очередь, фон-поток, save_snapshot()
  recorder/reader.py          Episode.open(zip) → meta, events, frames
  recorder/replay.py          replay_controller(), replay_analysis()
  recorder/plots.py           PNG-графики ошибки и кодов (cv2)
  lab.py                      record / follow / calibrate без окна
  api/__init__.py, api/server.py   локальный API
  vision/pose_detector.py     + CoreML
  control/follower.py         + recorder, + опрос #APC
  camera/scheduler.py         + on_sent
  vision/analyzer.py          + recorder (кадр + результат)
  camera/session.py           создаёт Recorder, раздаёт крючки
  config.py                   + record_*, api_*
  ui/main_window.py           F9, переключатель агента, строка записи
  ui/replay_window.py         просмотр эпизода
  __main__.py                 --replay, --lab, --api-port
run_mac.command
tests/recorder/…, tests/test_lab.py, tests/api/…, tests/data/episodes/
```

---

### Task 0: Запуск на Mac (модель: Sonnet)

**Files:** Modify `src/ptztracker/vision/pose_detector.py`, `src/ptztracker/diagnostics/checks.py` (строка устройства). Create `run_mac.command`, раздел «Mac» в `README.md`. Test `tests/vision/test_pose_detector.py`.

- `_PREFERRED = ("DmlExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider")`, `_DEVICE_NAMES["CoreMLExecutionProvider"] = "CoreML"`. Логика отката на CPU при сбое первого прогона — та же, что для DirectML.
- `run_mac.command`: переходит в папку скрипта; если `.venv` нет, создаёт его и ставит `pip install -e .`; запускает `.venv/bin/python -m ptztracker "$@"`. `chmod +x`.
- README: первый запуск (разрешить «Локальную сеть» в Системных настройках → Конфиденциальность), где лежат настройки и лог на Mac (`platformdirs`).

- [ ] **Step 1: Failing tests:** `test_choose_providers_prefers_coreml_on_mac`, `test_coreml_failure_falls_back_to_cpu` (фальшивая сессия падает на первом прогоне → CPU, `device == "CPU"`).
- [ ] **Step 2:** реализация, проверка: `.venv/bin/python -m ptztracker --sim`, в строке анализа видно «CoreML», 2 к/с и больше.
- [ ] **Step 3: Commit** `feat: run on macOS with CoreML inference`

---

### Task 1: Ядро записи — события, кольцевой буфер, сохранение в zip (модель: Opus)

**Files:** Create `recorder/__init__.py`, `events.py`, `ring.py`, `writer.py`, `reader.py`. Test `tests/recorder/test_ring.py`, `test_writer.py`, `test_reader.py`.

**Interfaces:**
- `class Recorder(Protocol)`: `frame(fr: Frame) -> None`, `event(kind: str, **data) -> None`, `save_snapshot(reason: str) -> Future[Path]`, `close()`. `NullRecorder` ничего не делает.
- `BlackBox(out_dir: Path, meta: dict, buffer_s=300, frame_fps=5, jpeg_quality=70, to_disk=False)`:
  - `frame()` пропускает кадры чаще `frame_fps` (по `t_capture`) и кладёт **ссылку** на `fr.image` в очередь кадров. Кадры неизменяемы, копия не нужна; в JPEG их кодирует фоновый поток.
  - `event()` добавляет `t=monotonic()`, если его не передали, и кладёт в очередь событий.
  - Фоновый поток кладёт в `RingBuffer` уже закодированные JPEG (bytes). Старше `buffer_s` — выбрасывает.
  - `save_snapshot()` копирует буфер под замком и пишет zip в отдельном потоке: `meta.json`, `events.jsonl`, `frames/{seq:06d}.jpg`. Имя: `ПТЗ_ГГГГ-ММ-ДД_ЧЧ-ММ-СС_причина.zip`.
  - `to_disk=True` дополнительно пишет тот же формат кусками по 1 минуте в папку сеанса.
- `events.py`: `KINDS = {"analysis", "control", "cmd", "position", "follow", "target", "note"}`; `track_to_json(Track)` (keypoints → список из 17×3 `float`, округление до 0,1 px / 0,01); `redact_meta(dict)` — удаляет `password`, прогоняет все строки с `://` через `redact_url`.
- `Episode.open(path) -> Episode`: `.meta`, `.events(kind=None) -> Iterator[dict]`, `.frames() -> Iterator[(t, seq, ndarray)]`, `.duration`.

- [ ] **Step 1: Failing tests:**
```python
def test_ring_drops_older_than_buffer()
def test_frames_throttled_to_fps()
def test_queue_overflow_drops_frames_not_events()   # 5000 кадров + 100 событий без фон-потока → все 100 событий сохранены
def test_snapshot_roundtrip()                       # события и кадры → zip → Episode: те же kind, t, seq, размер кадра
def test_snapshot_contains_no_password()            # meta с паролем и rtsp://user:pass@ → в байтах zip нет «pass»
def test_event_never_raises()                       # несериализуемое значение → событие "note" с ошибкой, без исключения
def test_snapshot_does_not_block()                  # save_snapshot() возвращается < 20 мс при 1500 кадрах в буфере
```
- [ ] **Step 2–4:** реализация, `pytest tests/recorder`, ruff.
- [ ] **Step 5: Commit** `feat: black-box recorder with ring buffer and zip snapshots`

---

### Task 2: Крючки записи в анализе, слежении, планировщике и сессии (модель: Opus)

**Files:** Modify `camera/scheduler.py`, `vision/analyzer.py`, `control/follower.py`, `camera/session.py`, `camera/protocol.py` (при необходимости `parse_pan_tilt` уже есть), `config.py`. Test `tests/recorder/test_hooks.py`, `tests/recorder/test_overhead.py` (`slow`).

**Interfaces:**
- `CommandScheduler(..., on_sent: Callable[[dict], None] | None = None)`: после `_execute` вызывается с `{"cmd", "key", "priority", "queued_s", "reply", "ok"}`. Вызов обёрнут в `try/except`.
- `Analyzer(..., recorder=NullRecorder())`: на каждый обработанный кадр — `recorder.frame(fr)` и `event("analysis", seq, t_capture, frame_size, tracks=[…], target_id, target_state, infer_ms)`.
- `Follower(..., recorder=NullRecorder(), query_pan_tilt: Callable | None = None)`:
  - в `_tick` — `event("control", t_capture, measure={nose,u,confident}, zoom_pos, out={pan,tilt,zoom,state,error,size_error}, codes=(p,t,z), composition, effort)`;
  - в `enable/disable` — `event("follow", on, reason)`;
  - в `_poll_zoom` — дополнительно `query_pan_tilt()` и `event("position", pan, tilt, zoom)`. Опрос идёт, пока включено слежение **или** `recorder.active`.
  - `set_composition`/`set_effort` — `event("follow", composition=…, effort=…)`.
- `Analyzer.select_at` / `clear_target` — `event("target", id, how="click"|"reset")`.
- `CameraSession`: создаёт `BlackBox` при `cfg.record_enabled` (по умолчанию True; out_dir = `~/Desktop/Записи PTZ Tracker`, на Windows — рабочий стол через `platformdirs.user_desktop_dir`). `meta`: версия, `cfg` без пароля, калибровка, `stream_kind`, ОС, устройство анализа. Метод `save_moment(reason="F9") -> Future[Path]`.
- `config.py`: `record_enabled: bool = True`, `record_buffer_s: int = 300`, `record_to_disk: bool = False`, `record_dir: str = ""`.

- [ ] **Step 1: Failing tests:**
```python
def test_scheduler_reports_sent_commands()
def test_scheduler_on_sent_exception_does_not_break_sending()
def test_sim_session_records_all_kinds()          # сим-сессия, цель, слежение 3 с, save_moment → в эпизоде есть analysis, control, cmd, position, follow, target
def test_recorder_failure_keeps_following()       # recorder.event бросает → слежение продолжает слать команды
@pytest.mark.slow
def test_recording_overhead_under_5_percent()     # Review Focus 1: fps анализа с записью ≥ 0.95 × без записи (сим, 20 с)
```
- [ ] **Step 2–4:** реализация, быстрый набор + `pytest -m slow tests/control/test_closed_loop.py tests/recorder` без регрессий.
- [ ] **Step 5: Commit** `feat: record analysis, control, commands and camera position`

---

### Task 3: «Сохранить момент» в окне (модель: Sonnet)

**Files:** Modify `ui/main_window.py` (только привязка: клавиша и сигналы, без нового оформления — его унифицирует редизайн). Test `tests/ui/test_main_window.py`.

- Клавиша **F9** и пункт «Сервис → Сохранить момент (F9)» вызывают `session.save_moment()`. Строка статуса: «Сохраняю запись…» → «Запись сохранена: <имя>» (10 с), кнопка «Показать в папке».
- В строке статуса — индикатор «● запись 5 мин», если запись включена.
- «Сервис → Настройки записи»: длина буфера (1–15 мин), «Писать всё на диск», папка.
- F9 **не** выключает слежение: это не ручное управление камерой.

- [ ] **Step 1: Failing tests:** `test_f9_saves_without_disabling_follow`, `test_f9_without_camera_shows_message`, `test_save_runs_in_background` (окно отвечает, пока пишется zip).
- [ ] **Step 2–4.** **Step 5: Commit** `feat: save-moment hotkey and recording settings`

> Координация с редизайном: эта задача трогает `main_window.py`. Перед ней основная сессия смотрит ветку редизайна, и если файл там сильно изменён, договаривается с сессией редизайна, кто вносит правку.

---

### Task 4: Повтор через регулятор и графики (модель: Opus)

**Files:** Create `recorder/replay.py`, `recorder/plots.py`. Modify `__main__.py` (`--replay PATH [--mode controller|analysis] [--out DIR]`). Test `tests/recorder/test_replay_controller.py`.

**Interfaces:**
- `replay_controller(ep: Episode, calib: Calibration | None = None, composition=None, effort=None) -> ReplayResult`:
  - восстанавливает `FramingController` из `meta` (или с переданными параметрами — чтобы проверить новые настройки на старом эпизоде);
  - идёт по событиям `control` в записанном времени, подаёт `FramingInput` из записанного `measure` и `zoom_pos`;
  - возвращает ряды `t, err_x, err_y, size_err, codes_rec, codes_new, state`.
- `ReplayResult.summary()`: число разворотов pan, макс. ускорение, % времени цели вне мягкой зоны, потери цели, смены цели.
- `plots.render(result, path)`: PNG 1600×900. Ошибка x/y, коды «было/стало», команды зума, полосы «цель потеряна» и «слежение выкл»; `position` — поверх.
- CLI печатает `summary` по-русски и путь к PNG.

- [ ] **Step 1: Failing tests:**
```python
def test_replay_reproduces_recorded_codes()       # Review Focus 3: сим-эпизод 30 с (slow) → codes_new == codes_rec ±1
def test_replay_with_other_effort_changes_codes()
def test_summary_counts_target_switches()         # синтетический эпизод с переходом цели 3→7→3 → 2 смены
def test_plot_written()
```
- [ ] **Step 2–4.** **Step 5: Commit** `feat: replay recorded episodes through the controller`

---

### Task 5: Повтор через распознавание и окно просмотра (модель: Opus для повтора, Sonnet для окна)

**Files:** `recorder/replay.py` (`replay_analysis`), Create `ui/replay_window.py`. Test `tests/recorder/test_replay_analysis.py`, `tests/ui/test_replay_window.py`.

- `replay_analysis(ep, detector=None, tracker=None) -> AnalysisReplay`: кадры эпизода в порядке `seq` идут через детектор и `PersonTracker` синхронно, без потоков. Цель выбирается по первому событию `target` (клик в тех же координатах). Результат: цель по кадрам «было/стало», смены и потери. Так проверяются исправления трекера на реальных кадрах.
- `ReplayWindow(episode)`: превью кадра с рамками записанного анализа (переиспользует `PreviewWidget`, только чтение), ползунок времени, кнопки ▶/⏸, скорость ×0,25–×4, внизу текущие коды и состояние регулятора. Открывается через `--replay PATH --view` и «Сервис → Открыть запись…».

- [ ] **Step 1: Failing tests:** `test_analysis_replay_on_fixture_episode` (эпизод с `tests/fixtures/people.jpg` × 20 кадров → цель держится), `test_replay_window_scrubs`.
- [ ] **Step 2–4.** **Step 5: Commit** `feat: replay episodes through detection and a viewer window`

---

### Task 6: Команды без окна для агента — `--lab` (модель: Sonnet)

**Files:** Create `src/ptztracker/lab.py`. Modify `__main__.py`. Test `tests/test_lab.py` (на симуляторе: `--ip sim`).

- `ptztracker --lab record --ip IP --seconds N [--select x,y]`: сессия без окна, анализ и запись, камера **не двигается**. В конце — zip.
- `ptztracker --lab follow --ip IP --seconds N --select x,y --composition waist_center --effort 0.5 --not-on-air`: слежение с записью. В конце — стоп и zip, затем `replay_controller` печатает сводку.
- `ptztracker --lab calibrate --ip IP --not-on-air [--save]`: мастер калибровки с записью кадров, результат печатается в JSON. `--save` пишет его в конфиг камеры.
- Без `--not-on-air` команды с движением завершаются с кодом 3 и объяснением. Пароль берётся из `keyring` по IP камеры из конфига или из `PTZ_PASSWORD` (не из аргумента).
- Ctrl+C в любой момент — аварийный стоп и выход, `finally` ждёт `wait_emergency`.

- [ ] **Step 1: Failing tests:** `test_lab_follow_requires_not_on_air`, `test_lab_record_sim_writes_zip`, `test_lab_follow_sim_prints_summary` (slow), `test_lab_ctrl_c_sends_stop`.
- [ ] **Step 2–4.** **Step 5: Commit** `feat: headless lab commands for record, follow and calibrate`

---

### Task 7: Локальный API главного окна (модель: Opus)

**Files:** Create `api/__init__.py`, `api/server.py`. Modify `ui/main_window.py` (запуск и остановка, переключатель), `config.py` (`api_enabled: bool = True`, `api_port: int = 8765`, `api_token: str` — генерируется при первом запуске). Test `tests/api/test_server.py`, `tests/ui/test_main_window.py`.

- `ThreadingHTTPServer` на `127.0.0.1`, заголовок `Authorization: Bearer <token>`, без токена — 401. Токен лежит только в конфиге: агент читает его оттуда.
- Только чтение:
  - `GET /status` — камера (без пароля), онлайн, слежение, причина, цель, композиция, усилие, ошибки, коды, fps, устройство;
  - `GET /events?since=t` — последние события из буфера, без кадров;
  - `GET /frame.jpg` — последний кадр;
  - `POST /moment` — «Сохранить момент».
- Управление, только при включённом «Разрешить управление агенту», иначе 403:
  - `POST /follow {"on": bool}`;
  - `POST /target {"x","y"}`;
  - `POST /composition`, `POST /effort`;
  - `POST /calibrate` — ещё требует флаг «камера НЕ в эфире» в окне.
- Потоки: обработчики только читают снимки (`analyzer.latest()`, `follower.output`), а действия передают в поток Qt через `QMetaObject.invokeMethod`/сигнал. Ответ на управление — после выполнения в Qt-потоке, с таймаутом 2 с.
- Переключатель «Разрешить управление агенту» в панели слежения: по умолчанию выкл., не сохраняется в конфиг. Esc, любое ручное действие и смена камеры его **выключают** (тот же путь, что `follower.disable`).

- [ ] **Step 1: Failing tests:**
```python
def test_status_requires_token()
def test_status_has_no_password()                  # Review Focus 2
def test_control_forbidden_by_default()            # Review Focus 4: POST /follow → 403
def test_esc_revokes_agent_control()
def test_manual_joystick_revokes_agent_control()
def test_api_binds_localhost_only()
```
- [ ] **Step 2–4.** **Step 5: Commit** `feat: localhost API for agent with operator-gated control`

---

### Task 8: Документы, эпизоды-тесты, финал (модель: Sonnet; ревью — Opus)

- `docs/ARCHITECTURE.md`: пакет `recorder`, крючки, API, `lab`. `docs/DECISIONS.md`: 5 мин / 5 к/с / JPEG 70, опрос `#APC` раз в 0,5 с, API только на localhost, управление агентом выключается ручным действием. `docs/BACKLOG.md`: шаг А сделан, что ждём с площадки. `AGENTS.md`: как агенту работать с живой камерой (`--lab`, API, токен, правило «НЕ в эфире»).
- `README.md`: инструкция оператору «Если что-то пошло не так — нажмите F9 и пришлите файл».
- `tests/data/episodes/sim_walk_30s.zip` (≤ 3 МБ) и медленный тест повтора по нему.
- Полный прогон `pytest -q` (~9 мин) и ruff. Независимое ревью всей ветки (`superpowers:requesting-code-review`) с упором на Review Focus.
- [ ] **Commit** `docs: stage A — mac, black box, agent access`

---

## После шага А — что делает пользователь на площадке

1. Подключить MacBook к сети камер и дважды щёлкнуть `run_mac.command`. Разрешить «Локальную сеть», если macOS спросит.
2. Выбрать камеру, проверить видео, вести докладчика 2–3 минуты, как обычно. Когда цель перескочит, камера дёрнется или уедет, сразу нажать **F9**.
3. Прислать zip-файлы из папки «Записи PTZ Tracker» на рабочем столе. Если ноутбук можно оставить в сети, пока камера не в эфире, агент сам проведёт калибровку и пробное слежение (`--lab`).

Эти эпизоды — входные данные шага Б (исправление слежения): каждый плохой эпизод становится тестом повтора.

## Порядок и распределение

- Task 0 → 1 → 2 идут последовательно (Task 2 опирается на ядро).
- После Task 2 параллельно: Task 4 + 5 (повтор), Task 6 (lab), Task 7 (API). Файлы почти не пересекаются, общий только `__main__.py` (маленькие правки — сводит основная сессия).
- Task 3 и Task 7 трогают `main_window.py` — только после сверки с веткой редизайна.
- Исполнители — субагенты (модель указана у задачи), после каждой — проверка спекой и ревью кода (subagent-driven-development).
