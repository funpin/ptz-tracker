# PTZ Tracker — несколько камер одновременно (сетка 2×2, до 4 геймпадов)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** До 4 камер в одной программе, у каждой своя сессия; окно — сетка 2×2 + правая панель выбранной камеры; Esc останавливает все; анализ ограничен настройкой; геймпады маршрутизируются по камерам (Create, сложение скоростей), флажки агента — у каждой камеры свои.

**Architecture:**
- Новый пакет `src/ptztracker/multicam/` без Qt: `analysis.py` (кто получает анализ — чистая функция), `velocity.py` (смеситель скоростей камеры по источникам), `pads.py` (маршрутизатор «геймпад → клетка»), `agent.py` (флажки агента одной камеры), `grid.py` (хозяин до 4 клеток: конфиг, сессия, смеситель, флажки).
- `camera/session.py` — анализ включается/выключается на ходу; снимок положения P/T/Z из ответов `#APC`/`#GZ` (любого источника, в том числе Follower) и история за 30 с.
- `ui/camera_cell.py` — виджет клетки (выбор камеры, видео, накладки, синяя рамка, значки геймпадов); `ui/service_cell.py` — служебная клетка.
- `ui/main_window.py` — сетка вместо одного видео; `session`, `preview`, `video_frame`, `power_overlay`, `target_label`, `reset_target_btn`, `skeleton_check` становятся свойствами **выбранной клетки** (старый код и тесты продолжают работать).
- `api/backend.py` — параметр `camera`, поле `cameras` в `/status`, флажки по камерам.

**Tech Stack:** Python 3.12–3.13, PySide6, pytest-qt. Новых зависимостей нет.

**Spec:** `docs/superpowers/specs/2026-10-31-multi-camera-design.md`; основа окна — `docs/superpowers/specs/2026-10-30-main-window-layout-design.md`; геймпад — `docs/superpowers/specs/2026-10-30-gamepad-dualsense-design.md` (уже в main).

## Сведение с main (2026-11-05)

Ветка геймпада уже в `main` (`gamepad/`, `ui/gamepad_bridge.py`), там же новое окно, питание/gain/баланс белого, `QUERY_MAX_WAIT_S`. Поправки к задачам ниже:

- **Смеситель (Task 2) остаётся единственным.** В `main` сумма «клавиатура + геймпад» живёт в `MainWindow._apply_held` (3 оси, ±1). Task 7 заменяет её смесителем клетки (источники `keyboard`, `mouse`); Task 10 — источник `pad{n}`. `limit_watch.set_demand(...)` по-прежнему получает скорость геймпада — у сессии его камеры.
- **Маршрутизатор (Task 3) остаётся.** В `main` один физический геймпад (`GamepadBridge`, один контроллер SDL) и «камера геймпада» = выбранная (`_pad_session`). Task 10 переписан: не «мост из другой ветки зовёт `pad_*`», а существующий `GamepadBridge` — это геймпад №1 в `PadRouter`; `_pad_session()` → сессия клетки `router.slot_of(1)`; `NextCamera` (Create) → `pad_create(1)` вместо перебора `camera_combo`; `Stop` с тачпада → стоп только камеры геймпада (спека, «Тачпад геймпада»), а не `emergency_stop()` всех; `Disconnected` → `pad_disconnected`; вибрации из `PadFeedback` → `bridge.rumble(RUMBLE_*)`; меню Options, открытое с геймпада, действует на камеру геймпада.
- **Огоньки игрока = номер клетки** (спека 2026-10-31 заменяет `pad_number` из спеки геймпада). `CameraConfig.pad_number` в конфиге остаётся (старые файлы читаются), но на точки больше не влияет; поле «Номер на геймпаде» в диалоге камеры убрать в Task 10, `pad_number_for` — удалить, если больше не используется. Запись в DECISIONS — Task 11.
- **Несколько физических геймпадов** (2–4) требуют своего `SdlBackend`/`GamepadThread`/моста на каждый контроллер — это новая **Task 12** (после Task 11), чтобы не держать сетку и API.
- `poll_position` (Task 5) шлёт `Priority.QUERY` — совместимо с `QUERY_MAX_WAIT_S`, ничего не меняем.
- Task 7: кроме перечисленного, `camera_menu` (питание, gain, баланс белого), чип геймпада и `_sync_pad_player` работают с выбранной клеткой; `camera_combo` из тулбара `main` убрать.

## Global Constraints

- **Границы.** `control/`, `framing/`, `vision/`, `video/` не менять (кроме публичного чтения в `Follower`, если понадобится — Task 5). Пакет `gamepad/` уже в `main`; до Task 12 его не менять, SDL не трогать (см. «Сведение с main»).
- Слотов ровно 4 (`multicam.grid.SLOTS = 4`), сетка всегда 2×2: клетки 0 1 / 2 3. Номер клетки для оператора = индекс + 1.
- Одна камера — не больше одной клетки. В выборе клетки есть «нет».
- `AppConfig.max_analyzed` — 1…4, по умолчанию 1; `CameraConfig.tracking` — по умолчанию `True`.
- Esc и «СТОП КАМЕРЫ» — все камеры; стоп геймпада — только его камера.
- Сложение скоростей: по каждой оси (pan, tilt, zoom, focus) сумма источников с ограничением ±1.
- Белые точки геймпада = номер клетки его камеры (1–4), 0 — погашены.
- Тексты: отказ TRACK сверх лимита — `f"Уже следит {имена через запятую} — выключите там или увеличьте число камер в настройках"`; без анализа — подсказка значка «Анализ»: `"Без анализа: слежение на этой камере выключено в настройках"` или `"Без анализа: место анализа занято другой камерой"`.
- Инварианты AGENTS.md — **на камеру**: ручное действие/смена камеры в клетке/остановка анализа/раскачка выключают слежение этой камеры до её следующей команды; стопы не теряются; поток Qt не ждёт сеть; пароли не попадают в подсказки; агент двигает камеру только при двух флажках **этой** камеры.
- Окно ~1640×850, `MainWindow.minimumSizeHint().height() <= 760` (тест layout уже есть — должен остаться зелёным).
- `--sim` не пишет в конфиг ни `last_camera_id`, ни `grid`.
- Последняя задача — строки в `CHANGELOG.md` → `[Не выпущено]` языком оператора; версию не поднимать.

## Review Focus

1. **Клетку закрыли или сменили камеру, пока геймпад держит стик.** Ожидание: прежней камере ушёл стоп, геймпад перешёл по правилу Create, новая камера не поехала, пока стик не вернётся в центр. Тест — Task 10.
2. **Одна камера в двух клетках** (оператор выбрал в клетке 2 камеру, которая уже в клетке 1, через устаревший список или API). Ожидание: отказ `ValueError`, клетка 2 без изменений. Тест — Task 4.
3. **Лимит анализа уменьшили, когда следят две камеры.** Ожидание: обе продолжают следить, третья включиться не может, выбранная без TRACK анализ теряет. Тест — Task 1.
4. **Esc при трёх клетках, одна камера недоступна** (IP `10.255.255.1`). Ожидание: остальные получили стоп сразу, окно не подвисло (> 0,3 с на Esc — провал). Тест — Task 7.
5. **Флажки агента стоят у камеры A; оператор двигает камеру B.** Ожидание: у A флажки остались; `POST /follow?camera=B` → 403; запрос без `camera` идёт на выбранную. Тест — Task 9.

---

### Task 1: Кто получает анализ

**Files:**
- Create: `src/ptztracker/multicam/__init__.py`, `src/ptztracker/multicam/analysis.py`
- Test: `tests/multicam/test_analysis.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class CellState: name: str; tracking_allowed: bool; tracking_on: bool`
  - `allocate_analysis(cells: Sequence[CellState | None], selected: int | None, limit: int) -> frozenset[int]` — индексы клеток с анализом. Порядок: (1) все с `tracking_on` (даже сверх лимита — никогда не отнимаем); (2) выбранная, если `tracking_allowed` и есть место; (3) остальные с `tracking_allowed` по порядку клеток, пока есть место *(моё решение: лимит — «сколько можно», места не простаивают)*.
  - `tracking_refusal(cells, slot: int, limit: int) -> str | None` — текст отказа из Global Constraints (имена — камеры с `tracking_on`), если включение TRACK в `slot` превысит лимит; `None` — можно. Камера без `tracking_allowed` → `"Слежение на этой камере выключено в настройках камеры"`.

- [ ] **Step 1: Failing tests** `test_limit_one_selected_gets_it`: 2 клетки allowed, selected=1, limit=1 → `{1}`. `test_tracking_first`: клетка 0 tracking_on, selected=1, limit=1 → `{0}`. `test_never_takes_from_tracking`: 0 и 1 tracking_on, limit=1 → `{0, 1}`. `test_not_allowed_never`: selected не allowed, limit=4 → его нет. `test_fill_rest_in_order`: 4 allowed, selected=2, limit=3 → `{2, 0, 1}`. `test_refusal_text`: 0 «PTZ 2» tracking_on, limit=1, slot=1 → `"Уже следит PTZ 2 — выключите там или увеличьте число камер в настройках"`; limit=2 → `None`; уже `tracking_on` в slot → `None`.
- [ ] **Step 2:** `.venv/bin/pytest -q tests/multicam/test_analysis.py` — FAIL (нет модуля).
- [ ] **Step 3:** реализовать.
- [ ] **Step 4:** PASS; `ruff check src tests`.
- [ ] **Step 5:** commit `feat(multicam): analysis allocation by limit`.

### Task 2: Смеситель скоростей камеры

**Files:**
- Create: `src/ptztracker/multicam/velocity.py`
- Test: `tests/multicam/test_velocity.py`

**Interfaces:**
- Consumes: у цели методы `set_pan_tilt_velocity(vx, vy)`, `set_zoom_velocity(v)`, `set_focus_velocity(v)` (как у `CameraSession`).
- Produces:
  - `class VelocityMixer(target)`: `set(source: str, pan=0.0, tilt=0.0, zoom=0.0, focus=0.0) -> None`; `release(source: str) -> None` (= set нулей и забыть источник); `clear_all() -> None` — все источники в ноль **без отправки** (стоп уже отправил `emergency_stop`) и каждый источник «защёлкнут»: его ненулевые значения игнорируются, пока он хоть раз не пришлёт все нули; `total() -> tuple[float, float, float, float]`.
  - Отправка только при изменении суммы по группе: pan/tilt → `set_pan_tilt_velocity`, zoom → `set_zoom_velocity`, focus → `set_focus_velocity`. Сумма с ограничением ±1.
  - `source(name: str) -> MixerSource` — объект для `ManualControls.bind(...)`: его `set_pan_tilt_velocity`/`set_zoom_velocity`/`set_focus_velocity` идут в смеситель от имени `name`, прочие атрибуты (`recall_preset`, `set_iris`, `image`, …) — через `__getattr__` к цели.

- [ ] **Step 1: Failing tests** на подставной цели, записывающей вызовы: `test_two_sources_sum` (pad1 pan 0.5 + pad3 tilt 0.5 → последний вызов `(0.5, 0.5)`), `test_clamped` (0.8 + 0.8 → 1.0), `test_release_one_keeps_other` (pad1 отпустил → `(0.0, 0.5)`), `test_no_resend_when_sum_same`, `test_clear_all_latches_until_zero` (после `clear_all` pad1 `set(pan=1)` — вызовов нет; `set()` нулей, затем `set(pan=1)` → `(1.0, 0.0)`), `test_source_forwards_other_methods`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать (без потоков: вызывается из Qt).
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(multicam): per-camera velocity mixer`.

### Task 3: Маршрутизатор геймпадов

**Files:**
- Create: `src/ptztracker/multicam/pads.py`
- Test: `tests/multicam/test_pads.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class PadFeedback: pad: int; kind: Literal["dots", "rumble_taken", "rumble_nowhere"]; value: int = 0` (`dots` — номер клетки 1–4 или 0).
  - `@dataclass(frozen=True) class PadMove: pad: int; old_slot: int | None; new_slot: int | None`
  - `class PadRouter`: `set_slots(open_slots: Sequence[bool]) -> tuple[list[PadMove], list[PadFeedback]]` (какие клетки с камерой; геймпады закрытых клеток переходят по правилу Create); `connect() -> tuple[int, list[PadMove], list[PadFeedback]]` (номер 1–4, первый свободный; `ValueError`, если 4 заняты); `disconnect(pad: int) -> list[PadMove]`; `create(pad) -> tuple[list[PadMove], list[PadFeedback]]`; `slot_of(pad) -> int | None`; `pads_on(slot) -> list[int]`.
  - Правила (спека, раздел «Геймпады»): подключение — первая по порядку клетка без геймпадов, иначе первая открытая; Create — следующая по кругу свободная, иначе просто следующая открытая; попал на занятую → `rumble_taken`; открыта одна клетка → без перехода, `rumble_nowhere`; при каждом переходе — `dots`.

- [ ] **Step 1: Failing tests** `test_connect_takes_first_free` (клетки 0,1 открыты; pad1→0, pad2→1, dots 1 и 2), `test_create_prefers_free` (клетки 0,1,2; pad1 на 0, pad2 на 1; pad1 Create → 2), `test_create_when_all_taken_goes_next_and_rumbles` (4 клетки, 4 геймпада; pad1 Create → клетка 1, `rumble_taken` для pad1, `PadMove(1, 0, 1)`), `test_single_camera_nowhere`, `test_closing_slot_moves_pads` (клетку 0 закрыли → pad1 на следующую, `PadMove(1, 0, 1)`), `test_no_cameras_dots_off` (все закрыты → `slot_of(1) is None`, `dots` 0), `test_disconnect_frees_number` (pad2 отключили, следующий connect получает 2).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(multicam): gamepad-to-camera router`.

### Task 4: Конфиг, флажки агента, сетка сессий

**Files:**
- Modify: `src/ptztracker/config.py`
- Create: `src/ptztracker/multicam/agent.py`, `src/ptztracker/multicam/grid.py`
- Test: `tests/test_config.py`, `tests/multicam/test_grid.py`

**Interfaces:**
- Consumes: `VelocityMixer` (Task 2), `CameraSession`.
- Produces:
  - `CameraConfig.tracking: bool = True`; `AppConfig.grid: list[str | None]` (длина 4, по умолчанию `[None]*4`); `AppConfig.max_analyzed: int = 1`. При загрузке: `grid` неверной длины/типа → дополнить/обрезать до 4, не-строки → `None`; если `grid` пустой и есть `last_camera_id` → `grid[0] = last_camera_id`; `max_analyzed` зажать в 1…4.
  - `class AgentGate` (перенос логики снимков из `AgentPanel`): поля `control_allowed`, `not_on_air` (снимки, читаются любым потоком), `set_allowed(on)`, `set_off_air(on)`, `revoke()`, `on_change: Callable[[], None] | None` (окно перерисует галочки в потоке Qt), `on_off_air_cleared: Callable[[], None] | None`.
  - `@dataclass class Cell: cfg: CameraConfig; session: CameraSession; mixer: VelocityMixer; gate: AgentGate`
  - `class CameraGrid(make_session: Callable[[CameraConfig, bool], CameraSession])` (второй аргумент — `analysis`): `cells: list[Cell | None]`; `selected: int | None`; `open(slot, cfg, analysis: bool) -> Cell` (закрывает прежнюю в этой клетке; `ValueError`, если `cfg.id` уже в другой клетке; `session.on_manual = gate.revoke`); `close(slot)` (gate.revoke → mixer.clear_all → `session.stop()`); `close_all(timeout=1.5)` — `stop()` всех сессий в параллельных потоках, ждать не дольше `timeout` в сумме; `slot_of(camera_id) -> int | None`; `emergency_stop_all()` (для каждой: gate.revoke, mixer.clear_all, `session.emergency_stop()`); `ids() -> list[str | None]`.

- [ ] **Step 1: Failing tests** config: `test_grid_default`, `test_grid_from_last_camera_id`, `test_grid_bad_shape_fixed` (`["a", 5]` → `["a", None, None, None]`), `test_max_analyzed_clamped` (0→1, 9→4), `test_camera_tracking_default_true`. grid (на симуляторах, `CameraSession(cfg, analysis=False)` с `kind="sim"`): `test_open_two_sims_independent` (pan в клетке 0 — у второго симулятора `#PTS` не было), `test_same_camera_twice_refused` (Review Focus 2), `test_close_sends_stop` (последний `#PTS` = `#PTS5050`), `test_emergency_stop_all` (обе в движении → обе `#PTS5050`, у обеих `gate.control_allowed is False`), `test_manual_revokes_only_own_gate`, `test_close_all_bounded` (одна сессия на `10.255.255.1` + симулятор: `close_all` ≤ 1,8 с).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать. Каждый симулятор уже поднимает свой `SimHttpServer` на порту 0 — проверить, что два одновременно работают.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(multicam): config grid, per-camera agent gate, camera grid`.

### Task 5: Сессия — анализ на ходу и положение камеры

**Files:**
- Modify: `src/ptztracker/camera/session.py` (и при необходимости только чтение в `control/follower.py`)
- Test: `tests/camera/test_session.py`

**Interfaces:**
- Produces:
  - `CameraSession.set_analysis(on: bool) -> None` — включить: создать `Analyzer` и `Follower` как в `start()` (вынести в `_start_analysis()`); выключить: `follower.disable("анализ отдан другой камере")` **до** `follower.stop()` и `analyzer.stop(timeout=0.2)`, затем `analyzer = follower = None`. Повторный вызов с тем же значением — ничего.
  - `@dataclass(frozen=True) class PtzPosition: t: float; pan_deg: float; tilt_deg: float; zoom_x: float` — pan/tilt = `(код − he42.APC_CENTER) · he42.APC_DEG_PER_UNIT`, zoom_x = `he42.ZOOM_RATIO ** ((z − LENS_MIN)/(LENS_MAX − LENS_MIN))`.
  - `CameraSession.ptz -> PtzPosition | None`; `ptz_history(seconds: float = 30.0) -> list[PtzPosition]` (deque, старше 30 с выбрасывать).
  - Разбор в `_on_reply`: ответы на `#APC` и `#GZ` из любого источника. Проверить, что ответы `scheduler.query` (опрос Follower) тоже доходят до `add_sent_listener`; если нет — добавить в `Follower` публичное `last_position`, и `ptz` берёт его.
  - `CameraSession.poll_position() -> None` — `submit` `#APC`/`#GZ` с `Priority.QUERY`, только если последнему ответу > 0,4 с; в standby не шлёт (камера ответит ER2).

- [ ] **Step 1: Failing tests** `test_analysis_on_off_at_runtime` (симулятор, `analysis=False` → `set_analysis(True)` → `analyzer.latest()` появился за 3 с; `follower.enable()` после `select_at` → `set_analysis(False)` → `follower` был выключен до остановки, `session.analyzer is None`), `test_disable_analysis_turns_tracking_off_first` (подставить follower-шпион: порядок вызовов `disable` → `stop`), `test_ptz_from_poll` (симулятор повёрнут `sim.pan_deg = 10` → `poll_position()` → `ptz.pan_deg ≈ 10` ±0,5), `test_ptz_history_trimmed`, `test_poll_position_rate_limited` (два вызова подряд → один `#APC` у симулятора).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать.
- [ ] **Step 4:** PASS + `.venv/bin/pytest -q -m "not slow" tests/camera tests/control`.
- [ ] **Step 5:** commit `feat(camera): analysis on/off at runtime, PTZ position snapshot`.

### Task 6: Виджет клетки

**Files:**
- Create: `src/ptztracker/ui/camera_cell.py`
- Modify: `src/ptztracker/ui/preview.py` (только `click_guard`)
- Test: `tests/ui/test_camera_cell.py`

**Interfaces:**
- Produces:
  - `PreviewWidget.click_guard: Callable[[], bool] | None` — если задан и вернул `False`, клик не выбирает человека.
  - `class CameraCell(QWidget)(slot: int)`: сигналы `cameraChosen(int, object)` (slot, id или `None`), `selectRequested(int)`; атрибуты `preview`, `video_frame`, `power_overlay`, `target_label`, `reset_target_btn`, `skeleton_check` (перенести создание из `MainWindow.__init__`); `set_choices(items: list[tuple[str, str]], current: str | None)` — пункты «нет» + переданные (окно уже убрало камеры других клеток); `set_selected(on: bool)` — синяя рамка (новый токен `C.SELECT` в `ui/theme.py`); `set_pads(n: int)` — n значков геймпада; номер клетки всегда в заголовке («1 · PTZ 2 ▾»); `set_service(widget: QWidget | None)` — когда камеры нет, вместо видео показывается служебный виджет.
  - Клик по видео невыбранной клетки → `selectRequested`, человек не выбирается (через `click_guard`).

- [ ] **Step 1: Failing tests** `test_choices_contain_none_first`, `test_choose_emits` (выбор пункта → `cameraChosen(2, "cam-b")`; «нет» → `None`), `test_click_unselected_only_selects` (`set_selected(False)`, клик по preview → `selectRequested(0)`, `click_guard()` → False), `test_pads_badges` (`set_pads(2)` → два значка видимы), `test_service_shown_without_camera`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать; цвета только из `ui/theme.py` (`test_no_hardcoded_colors.py`).
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(ui): camera cell widget`.

### Task 7: Главное окно на сетке

**Files:**
- Modify: `src/ptztracker/ui/main_window.py`, `src/ptztracker/ui/camera_dialog.py`
- Test: `tests/ui/test_main_window.py`, `tests/ui/test_multi_camera_window.py`, `tests/ui/test_camera_dialog.py`

**Interfaces:**
- Consumes: `CameraGrid`, `CameraCell`, `allocate_analysis`, `tracking_refusal`, `VelocityMixer.source`.
- Produces:
  - `MainWindow.grid: CameraGrid`; `MainWindow.cells: list[CameraCell]`; `select_slot(slot: int) -> None`; `set_cell_camera(slot: int, camera_id: str | None) -> None`.
  - Свойства выбранной клетки: `session`, `preview`, `video_frame`, `power_overlay`, `target_label`, `reset_target_btn`, `skeleton_check` (без выбранной — `None`/пустые как сейчас без камеры).
  - Выпадающий список камер из тулбара убрать; `_fill_combo`/`_on_combo`/`_activate` заменить на `set_cell_camera`. `sim_cameras() -> list[CameraConfig]` — `sim1…sim4`, «Симулятор 1…4»; при `use_sim` и пустой сетке — `["sim1", "sim2", None, None]`, выбрана клетка 0.
  - Клавиатура и мышь — через `grid.cells[sel].mixer.source("keyboard" | "mouse")`; `controls.bind(mixer.source("mouse"))`. Смена выбранной клетки: `release_all_held()` у прежней (источники keyboard/mouse прежней → `release`), затем пересчёт анализа.
  - `emergency_stop()` — `grid.emergency_stop_all()` + отпустить клавиши. Кнопка «СТОП КАМЕРЫ» — то же.
  - `_on_follow_requested(want=True)`: сначала `tracking_refusal(...)`; не `None` → `follow_panel.show_hint(текст)`, слежение не включается.
  - Пересчёт анализа `_reallocate_analysis()` — после выбора клетки, смены камеры в клетке, TRACK вкл/выкл, смены лимита: `allocate_analysis` → `session.set_analysis(...)` для каждой; затем перепривязать `cell.preview.set_analyzer/set_follow`.
  - `CameraDialog`: галочка «Слежение доступно (нейросеть на этой камере)» → `CameraConfig.tracking`. Меню ☰: подменю «Анализировать камер одновременно» 1–4 (радио) → `AppConfig.max_analyzed`, сохранить.
  - Удаление камеры, стоящей в клетке, — сначала клетка «нет»; изменение такой камеры — клетка открывается заново.
  - `closeEvent` — `grid.close_all()`.
  - Tally — рамка в каждой клетке по `atem_input` её камеры; значок ATEM — по выбранной. TRACK-тег (`VideoFrame.set_tracking_tag`) — в каждой клетке.
  - F9 — `save_moment` у каждой открытой сессии; в строке состояния — число сохранённых файлов. Возвращаемое значение для выбранной — как сейчас (совместимость тестов).
  - Сетка сохраняется в `cfg.grid` при каждом изменении (кроме `use_sim`).
  - Калибровка, диагностика, питание из ☰ — для выбранной сессии, захваченной при открытии; имя камеры в заголовке диалога; закрытие этой клетки закрывает её диалог.

- [ ] **Step 1: Failing tests** в `test_multi_camera_window.py` (`MainWindow(..., use_sim=True)`): `test_two_sims_open_in_grid` (клетки 0 и 1 с видео за 3 с), `test_keyboard_drives_selected_only` (выбрана 1, стрелка → `#PTS` только у симулятора клетки 1), `test_click_selects_cell_first`, `test_escape_stops_all` (обе движутся → обе `#PTS5050`), `test_escape_fast_with_dead_camera` (Review Focus 4: третья клетка — камера `10.255.255.1`; `emergency_stop()` ≤ 0,3 с), `test_cell_choices_exclude_other_cells`, `test_none_closes_session_with_stop`, `test_tracking_refused_over_limit` (limit 1; TRACK на клетке 0 включён; на клетке 1 → подсказка с «Уже следит Симулятор 1»), `test_analysis_follows_selection` (limit 1, без TRACK: выбрать клетку 1 → `analyzer` у 1, у 0 `None`), `test_grid_not_saved_in_sim`. В `test_camera_dialog.py` — `test_tracking_checkbox`. Существующие тесты `tests/ui/` должны пройти без правок или с правкой только способа выбора камеры.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать. Правая панель и тулбар — как в редизайне; `FollowPanel.status_label` накладывается на выбранную клетку (перенос при выборе).
- [ ] **Step 4:** PASS: `.venv/bin/pytest -q -m "not slow" tests/ui`; `test_main_window_layout.py` зелёный.
- [ ] **Step 5:** commit `feat(ui): 2x2 camera grid with per-cell sessions`.

### Task 8: Служебные клетки

**Files:**
- Create: `src/ptztracker/ui/service_cell.py`
- Modify: `src/ptztracker/ui/main_window.py`
- Test: `tests/ui/test_service_cell.py`

**Interfaces:**
- Consumes: `CameraSession.ptz`, `ptz_history`, `poll_position` (Task 5), `PadRouter.pads_on` (Task 3, может быть пустым до Task 11).
- Produces:
  - `class ServiceCell(QWidget)(kind: Literal["position", "graphs", "summary"])`; `update_view(rows: list[ServiceRow], selected: int | None) -> None`, где `@dataclass(frozen=True) class ServiceRow: slot: int; name: str; ptz: PtzPosition | None; history: list[PtzPosition]; analysis_fps: float | None; online: bool; pads: list[int]`.
  - «position» — строки `P +012.4° T −08.1° Z 2.3×` (формат `f"{pan:+06.1f}°"`, `f"{zoom:.1f}×"`) и вид сверху (луч на камеру, цвет/номер клетки); «graphs» — три линии за 30 с выбранной камеры + строка «Запись видео — в следующей версии»; «summary» — по камере: связь, анализ к/с, геймпады.
  - Порядок раздачи пустым клеткам: первая пустая — position, вторая — graphs, третья — summary.
  - Окно раз в 0,5 с (таймер статуса) зовёт `poll_position()` у всех открытых сессий, **только если** видна клетка position или graphs.

- [ ] **Step 1: Failing tests** `test_position_text` (ptz 12.4/−8.1/2.3 → текст содержит `+012.4°`, `-008.1°` или `−08.1°` — выбрать один формат и зафиксировать в тесте, `2.3×`), `test_no_ptz_shows_dash`, `test_graphs_paint_without_history` (пустая история — без исключений), `test_kinds_assigned_in_order` (окно с одной камерой → три служебные клетки position/graphs/summary), `test_no_polling_when_four_cameras` (4 клетки заняты → `poll_position` не вызывается).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать (`QPainter`, цвета из темы).
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(ui): service cells — position, graphs, summary`.

### Task 9: Агент — флажки и API по камерам

**Files:**
- Modify: `src/ptztracker/ui/agent_panel.py`, `src/ptztracker/api/backend.py`, `src/ptztracker/api/server.py`, `src/ptztracker/ui/main_window.py`
- Test: `tests/api/test_server.py`, `tests/ui/test_agent_panel.py` (новый)

**Interfaces:**
- Consumes: `AgentGate`, `CameraGrid`.
- Produces:
  - `AgentPanel` — строка на каждую открытую клетку («1 · PTZ 2: [ ] Разрешить управление агенту [ ] Камера НЕ в эфире»), галочки пишут в `cell.gate`; свойства `control_allowed`, `not_on_air`, `allow_check`, `off_air_check`, `revoke()` — для **выбранной** клетки (совместимость); `revoke_all()`.
  - Значок AGENT в тулбаре виден, если хоть у одной камеры «Разрешить».
  - API: параметр `camera` — в query (`?camera=`) для GET и в теле или query для POST; значение — id камеры или номер клетки `"1"…"4"`; нет такого → 404 `"Нет такой камеры в сетке: …"`. Без параметра — выбранная.
  - `status()` — прежние поля для выбранной + `"cameras": [{"slot": 1, ...те же поля..., "agent": {...}} , ...]`; `frame_jpeg(camera)`; `control(name, body, camera)` — проверки `_gated` по `cell.gate` и `cell.session`; 409 «Идёт калибровка камеры» — по этой сессии.
  - `_on_off_air_cleared` — по камере (слежение, включённое агентом на этой камере).

- [ ] **Step 1: Failing tests** `test_status_lists_cameras` (две клетки → `len(cameras) == 2`, без паролей), `test_camera_param_by_slot_and_id`, `test_unknown_camera_404`, `test_flags_per_camera` (Review Focus 5: флажки у клетки 0; ручное на клетке 1 → у 0 остались; `POST /follow {"on": true}` с `camera=2` → 403), `test_no_param_is_selected`, `test_escape_revokes_all`. Существующие тесты `tests/api/` без правок.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать.
- [ ] **Step 4:** PASS: `.venv/bin/pytest -q -m "not slow" tests/api tests/ui`.
- [ ] **Step 5:** commit `feat(api): per-camera agent permissions and camera parameter`.

### Task 10: Точки входа для геймпадов

**Files:**
- Modify: `src/ptztracker/ui/main_window.py`
- Test: `tests/ui/test_multi_camera_pads.py`

**Interfaces:**
- Consumes: `PadRouter` (Task 3), `VelocityMixer` (Task 2), `CameraGrid`.
- Produces (их вызывает `_on_pad_intents` для `GamepadBridge` = геймпад №1, в потоке Qt; см. «Сведение с main»):
  - `pad_connected() -> int`; `pad_disconnected(pad: int) -> None` (нули от `pad{n}` его камере); `pad_velocity(pad: int, pan: float, tilt: float, zoom: float, focus: float = 0.0) -> None` → `mixer.set(f"pad{pad}", ...)` его камеры; `pad_stop(pad) -> None` — стоп только его камеры: `gate.revoke()`, `mixer.clear_all()`, `session.emergency_stop()`; `pad_create(pad) -> None`; `pad_session(pad) -> CameraSession | None` (для пресетов, ириса, gain, R3 — мост зовёт методы сессии сам).
  - `on_pad_feedback: Callable[[PadFeedback], None] | None` — окно отдаёт сюда `dots`/`rumble_*`.
  - При любом `PadMove` (Create, закрытие клетки, отключение): `mixer.release(f"pad{n}")` у старой клетки; у новой — источник `pad{n}` защёлкнут до нулей (`VelocityMixer` — тот же механизм, что после `clear_all`; добавить `latch(source)`, если нужно).
  - `CameraCell.set_pads(len(router.pads_on(slot)))` после каждого изменения.
  - Ручное с геймпада снимает флажки агента только его камеры (через `session.on_manual = gate.revoke`, уже Task 4).

- [ ] **Step 1: Failing tests** (окно `use_sim=True`, две клетки): `test_pad_drives_its_camera_only`, `test_two_pads_sum_on_one_camera` (pad1 pan 0.5, pad2 tilt 0.5 на клетке 0 → последний `#PTS` симулятора соответствует (0.5, 0.5) через `velocity_to_code`), `test_pad_stop_only_its_camera` (клетка 1 едет от клавиатуры и не останавливается), `test_create_moves_and_stops_old` (`#PTS5050` у старой, новая не едет, пока не придут нули), `test_closing_cell_moves_pad` (Review Focus 1), `test_create_onto_taken_rumbles` (`rumble_taken` в `on_pad_feedback`), `test_dots_follow_slot`, `test_badges_count`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** реализовать.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(ui): gamepad routing hooks for multiple cameras`.

### Task 11: Медленный тест, документы, журнал изменений

**Files:**
- Create: `tests/multicam/test_multi_sim_slow.py`
- Modify: `docs/ARCHITECTURE.md`, `AGENTS.md`, `docs/BACKLOG.md`, `docs/DECISIONS.md`, `CHANGELOG.md`

- [ ] **Step 1:** `@pytest.mark.slow test_two_cameras_track_and_manual` — две сессии-симулятора с анализом (`max_analyzed=2`), на одной слежение за синтетической сценой 10 с (ошибка в кадре как в существующих slow-тестах слежения), на второй одновременно ручной поворот; проверить, что слежение первой не выключилось и вторая повернулась.
- [ ] **Step 2:** `.venv/bin/pytest -q -m slow tests/multicam` — PASS.
- [ ] **Step 3:** документы: ARCHITECTURE — пакет `multicam`, «выбранная клетка», путь команд геймпада; AGENTS.md — инварианты «на камеру», API `camera`; DECISIONS — `max_analyzed = 1` (причина: 13–14 к/с на одну камеру на GTX 1650, две не замерены), сложение скоростей, точки = номер клетки; BACKLOG — замер анализа на 2 камерах на площадке, нагрузка 4× H.264 1080p50, спека записи видео, подключение моста геймпада после слияния ветки геймпада; CHANGELOG `[Не выпущено]`: «До 4 камер одновременно: сетка 2×2, выбор камеры в каждой клетке…», «Esc останавливает все камеры», «Слежение можно разрешить не на всех камерах; число анализируемых камер — в меню».
- [ ] **Step 4:** полный прогон `.venv/bin/pytest -q` (~9+ мин, в фоне) и `ruff check src tests` — зелёные.
- [ ] **Step 5:** commit `docs: multiple cameras — architecture, invariants, changelog`.

### Task 12: Несколько физических геймпадов

**Files:**
- Modify: `src/ptztracker/gamepad/device.py`, `src/ptztracker/ui/gamepad_bridge.py`, `src/ptztracker/ui/main_window.py`
- Test: `tests/gamepad/`, `tests/ui/test_multi_camera_pads.py`

**Interfaces:**
- `SdlBackend` открывает контроллер по индексу/instance id; менеджер раз в 0,5 с ищет новые контроллеры и создаёт на каждый мост (`GamepadBridge`) с номером из `PadRouter.connect()`; отключение → `pad_disconnected(n)`. Один SDL на процесс (init в одном месте, события — одним потоком).
- Окно держит `pads: dict[int, GamepadBridge]`; все пути Task 10 параметризованы номером геймпада.

- [ ] **Step 1: Failing tests** на подставном бэкенде с двумя контроллерами: `test_two_pads_get_numbers_1_2`, `test_second_pad_drives_second_cell`, `test_unplug_one_keeps_other`, `test_dots_per_pad`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS (`tests/gamepad tests/ui`).
- [ ] **Step 5:** commit `feat(gamepad): up to four DualSense pads`; строка в CHANGELOG.
