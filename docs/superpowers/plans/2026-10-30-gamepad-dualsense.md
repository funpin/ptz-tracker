# Геймпад DualSense — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** оператор ведёт камеру с DualSense (USB/Bluetooth, Windows/macOS), в том числе когда окно программы неактивно; полоса геймпада показывает состояние, вибрация подтверждает действия.

**Architecture:** пакет `ptztracker/gamepad/` без Qt: чистый автомат «кнопки/оси + время → намерения», чистый выбор цвета и вибрации, поток опроса SDL (pygame-ce) с подменяемым бэкендом. В Qt намерения приходят очередным сигналом в `MainWindow`, который вызывает те же методы сессии, что клавиатура — инварианты слежения/агента/стопов получаются тем же путём. Упор камеры — отдельный наблюдатель положения в фоне.

**Tech Stack:** Python 3.12–3.13, PySide6, pygame-ce ≥ 2.5 (SDL 2.32), pytest-qt, симулятор `sim/`.

**Spec:** `docs/superpowers/specs/2026-10-30-gamepad-dualsense-design.md` — исполнитель читает его целиком; раскладка, цвета и вибрации — оттуда.

## Global Constraints

- Ручное действие (поворот, зум, пресет, стоп) выключает слежение и снимает флажки агента **до** отправки команды — только через `CameraSession.set_pan_tilt_velocity / set_zoom_velocity / recall_preset / store_preset` и `MainWindow.emergency_stop()`. Новых путей к планировщику из геймпада нет.
- Поток Qt не ждёт SDL и сеть; SDL-устройство трогает только поток опроса.
- Отключение/падение геймпада и закрытие программы → нулевые скорости поворота, зума, фокуса; если камера стояла — ничего не шлём.
- Ирис, gain, L1/R1, L3, сосед, R3 слежение сами не выключают (кроме R3 — это переключатель).
- pygame-ce — обязательная зависимость в `pyproject.toml`; `pysdl2-dll` рядом не ставить. Нет pygame или SDL не стартовал → программа работает без геймпада, одна строка в журнале.
- `PYGAME_HIDE_SUPPORT_PROMPT=1`, `SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1` — до импорта pygame.
- Пороги (мёртвая зона 0,12, удержание пресета 1,0 с, автоповтор 0,4 с / 6 шагов/с, сосед 0,6, упор 0,3 с / 2 ед., импульс упора 0,7 с) — константы модулей с записью в `docs/DECISIONS.md` («по ощущению, без замера; донастроить на площадке»).
- Окно ~1640×850 не растёт; тексты — по-русски; лицензия AGPL совместима (pygame-ce LGPL-2.1).
- Gain и баланс белого: методы сессии делает работа по `2026-10-30-main-window-layout-design.md`. **Этот план их не пишет**: геймпад вызывает `session.gain_step(d)` и пункты меню WB, только если у сессии есть эти методы (`hasattr`), иначе крестовина ↑/↓ и пункт WB неактивны (журнал, без ошибок). Подключение — отдельная маленькая задача после слияния той работы (Задача 10).

## Review Focus

1. **Переключение в другое окно, держа стик** — камера едет дальше, без стоп-рывка (тест в Задаче 7: `test_deactivate_keeps_gamepad_motion`).
2. **Клавиша отпущена, пока стик держат (и наоборот)** — второй источник продолжает (Задача 7: `test_keyboard_release_keeps_gamepad`).
3. **Выдернули кабель на ходу при включённом слежении** — нули скоростей уходят, слежение не выключается (Задача 7: `test_disconnect_stops_motion_keeps_follow`).
4. **Смена камеры при отклонённом стике** — старой нули, новая не едет, пока стик не вернут в центр (Задача 7: `test_camera_switch_requires_recenter`).
5. **Стоп при отклонённом стике** — камера стоит, пока стик не вернут в центр (Задача 2: `test_stop_latches_until_recenter`; Задача 7: `test_touchpad_stop_latches`).

---

## Файлы

| Файл | Ответственность |
|---|---|
| `src/ptztracker/gamepad/__init__.py` | пусто |
| `src/ptztracker/gamepad/intents.py` | dataclass-намерения |
| `src/ptztracker/gamepad/mapping.py` | `PadState`, `Mapper` — автомат раскладки |
| `src/ptztracker/gamepad/feedback.py` | `PadStatus`, `lightbar_color`, вибро-паттерны, `is_auto_off` |
| `src/ptztracker/gamepad/device.py` | `Backend` (протокол), `SdlBackend`, `GamepadThread` |
| `src/ptztracker/camera/limit_watch.py` | `LimitWatch` — упор по `#APC`/`#GZ` |
| `src/ptztracker/ui/gamepad_bridge.py` | `GamepadBridge(QObject)` — сигнал в Qt, источники скорости |
| `src/ptztracker/ui/camera_menu.py` | меню Options поверх видео |
| `src/ptztracker/ui/hotkeys_dialog.py` | окно F1 |
| `src/ptztracker/lab.py`, `__main__.py` | `--lab gamepad` |

---

### Task 1: Пробник `--lab gamepad` и зависимость (ворота для остального)

**Files:**
- Modify: `pyproject.toml` (dependencies: `"pygame-ce>=2.5"`), `packaging/ptztracker.spec` (`collect_all("pygame")` в цикл `for pkg in …`)
- Create: `src/ptztracker/gamepad/__init__.py`, `src/ptztracker/gamepad/device.py` (пока только `SdlBackend`)
- Modify: `src/ptztracker/__main__.py:29` (`choices` + `"gamepad"`), `src/ptztracker/lab.py`
- Test: `tests/test_lab.py`, `tests/test_packaging.py`

**Interfaces:**
- Produces: `SdlBackend()` с методами `open() -> bool` (инициализация SDL joystick/controller, без видео), `poll() -> RawPad | None` (None — нет устройства), `info() -> dict` (`name`, `type`, `has_led`, `has_rumble`, `power`), `set_led(rgb: tuple[int,int,int])`, `rumble(low: float, high: float, ms: int)`, `close()`. `RawPad` — `axes: dict[str, float]` (−1…1 для `leftx, lefty, rightx, righty`, 0…1 для `l2, r2`; вниз = +, как SDL), `buttons: frozenset[str]` из `{"cross","circle","square","triangle","l1","r1","l3","r3","dpad_up","dpad_down","dpad_left","dpad_right","touchpad","options"}`.
- `--lab gamepad` камеру не трогает: в `MOVING` не входит, `--ip` не требует.

- [ ] **Step 1: Тест** `test_lab_gamepad_without_device` в `tests/test_lab.py`: подменить `SdlBackend` фейком (`open()→True`, `poll()→None`), `run_lab(args(lab="gamepad", seconds=0.2))` → код 0, `RESULT` содержит `"connected": false`. И `test_lab_gamepad_no_pygame`: `SdlBackend.open` бросает `ImportError` → код 1, `RESULT {"error": "no_sdl"}`.
- [ ] **Step 2:** `pytest tests/test_lab.py -k gamepad -v` → FAIL.
- [ ] **Step 3:** установить `pip install -e ".[dev]"`; реализовать `SdlBackend` (через `pygame._sdl2.controller`, `pygame.joystick`; env-переменные из Global Constraints — в `device.py` до импорта) и ветку `gamepad` в `run_lab`: открыть, раз в 0,05 с печатать изменения осей/кнопок (`_say`), в первые секунды — цвета по кругу (красный, оранжевый, фиолетовый, голубой, зелёный, белый по 1 с) и вибрация 0,2 с; итог `RESULT {"connected", "name", "type", "has_led", "has_rumble", "buttons_seen": [...], "background_ok": null}`; Ctrl+C — нормальный выход 130, как у других.
- [ ] **Step 4:** `pytest tests/test_lab.py tests/test_packaging.py -v` → PASS; `ruff check src tests` чисто.
- [ ] **Step 5:** Коммит `feat(gamepad): --lab gamepad probe, pygame-ce dependency`.
- [ ] **Step 6 (ворота, пользователь):** попросить пользователя на Mac (`run_mac.command` рядом — или `.venv/bin/python -m ptztracker --lab gamepad --seconds 60`) по USB и по Bluetooth: понажимать все кнопки, на 10 с переключиться в другое окно, держа стик (видно ли изменения в выводе), и прислать вывод; то же на Windows после сборки CI. **Если фон, LED или Bluetooth не работают — остановиться и вернуться к пользователю.** Если на Mac опрос вне главного потока не работает — в Задаче 3 включить режим `QTimer` (см. спеку «Запуск и упаковка»). Результат записать в `docs/DECISIONS.md`.

### Task 2: Автомат раскладки

**Files:**
- Create: `src/ptztracker/gamepad/intents.py`, `src/ptztracker/gamepad/mapping.py`
- Test: `tests/gamepad/test_mapping.py`

**Interfaces:**
- Consumes: `RawPad` (Task 1).
- Produces (`intents.py`, frozen dataclasses): `PanTilt(vx: float, vy: float)` (vy > 0 — вверх), `Zoom(v: float)` (+ ближе), `Preset(n: int, store: bool)` (n 0–3), `IrisStep(d: int)`, `GainStep(d: int)`, `Neighbor(d: int)` (−1 левее), `ToggleFollow()`, `Stop()`, `PushFocus()`, `Composition(d: int)` (−1 шире), `MenuOpen()`, `MenuNav(dx: int, dy: int)`, `MenuAccept()`, `MenuClose()`, `Disconnected()`.
- `Mapper(menu_open: Callable[[], bool])`, `Mapper.update(pad: RawPad | None, t: float) -> list[Intent]`; `Mapper.latch_stop()` — после стопа скорости держатся нулём, пока стики/курки не вернутся в мёртвую зону; `Mapper.cancel_holds()` — сбросить удержание пресета (смена камеры, меню).
- `PanTilt`/`Zoom` выдаются только при изменении (после мёртвой зоны, округление до 0,01). `pad=None` после устройства → `[Disconnected()]` один раз, внутреннее состояние сброшено.

- [ ] **Step 1: Тесты** (время — аргумент, без sleep):
  - `test_deadzone`: `leftx=0.1` → нет `PanTilt`; `leftx=1.0` → `PanTilt(1.0, 0.0)`; `lefty=-1.0` → `vy=+1.0`.
  - `test_triggers_zoom`: `r2=0.5` → `Zoom(v)` с `0 < v ≤ 0.5`; `l2=1.0` → `Zoom(-1.0)`; оба отпущены → `Zoom(0.0)`.
  - `test_change_only`: тот же `RawPad` дважды → второй раз `[]`.
  - `test_preset_short_recall`: `cross` 0,3 с и отпустить → `[Preset(0, False)]` в момент отпускания.
  - `test_preset_long_store`: `triangle` держать: в t=1,0 → `[Preset(3, True)]`; отпускание после → `[]`.
  - `test_preset_cancel_holds`: `square` 0,5 с, `cancel_holds()`, отпускание → `[]`.
  - `test_iris_autorepeat`: `dpad_right` в t=0 → `IrisStep(+1)`; до 0,4 с — ничего; затем ещё по одному примерно каждые 1/6 с (в 0,4…1,0 с — 4 ± 1 шага); `dpad_up` → `GainStep(+1)`.
  - `test_neighbor_needs_recenter`: `rightx=0.7` → `Neighbor(+1)`; держать → ничего; `0.0`, снова `-0.7` → `Neighbor(-1)`.
  - `test_buttons`: `r3`→`ToggleFollow`, `touchpad`→`Stop`, `l3`→`PushFocus`, `l1`→`Composition(-1)`, `r1`→`Composition(+1)`, `options`→`MenuOpen` — по нажатию, без повтора при удержании.
  - `test_menu_mode`: при `menu_open()=True`: `dpad_down`→`MenuNav(0,+1)`, `dpad_left`→`MenuNav(-1,0)`, `cross`→`MenuAccept`, `circle`/`triangle`/`options`→`MenuClose`; `square` → `[]`; стик и `touchpad` работают как обычно.
  - `test_stop_latches_until_recenter`: `leftx=1` → `PanTilt(1,0)`; `latch_stop()`; дальше `leftx=0.9` → `[]`; `leftx=0` → `[]` (или `PanTilt(0,0)` не выдаётся, т.к. уже 0); затем `leftx=0.8` → `PanTilt(0.8,0)`.
  - `test_disconnect`: `update(None)` после `leftx=1` → `[Disconnected()]`; повторный `None` → `[]`.
- [ ] **Step 2:** `pytest tests/gamepad -v` → FAIL.
- [ ] **Step 3:** реализовать. Кривая стика — линейная после мёртвой зоны `(|x|-0.12)/(1-0.12)` со знаком (квадратичную не делаем без жалобы с площадки — YAGNI).
- [ ] **Step 4:** PASS; ruff.
- [ ] **Step 5:** Коммит `feat(gamepad): button/axis mapping state machine`.

### Task 3: Поток опроса и выход на полосу/вибрацию

**Files:**
- Modify: `src/ptztracker/gamepad/device.py`
- Test: `tests/gamepad/test_device.py`

**Interfaces:**
- Consumes: `SdlBackend` API (Task 1), `Mapper` (Task 2).
- Produces: `GamepadThread(backend, mapper, on_intents: Callable[[list[Intent]], None], on_connection: Callable[[dict | None], None], hz: float = 100.0)`; `start()`, `stop(timeout=1.0)` (перед выходом шлёт `on_intents([Disconnected()])`, если был подключён, и `set_led((0,0,0))` нейтральный — решение: «выключить» полосу), `set_led(rgb)`, `rumble(pattern: tuple[tuple[float,float,int],...])` — кладут в очередь, выполняет поток. Исключение в цикле → `on_intents([Disconnected()])`, `log.exception`, поток завершается. Переподключение: `poll()` вернул устройство снова → `on_connection(info)`.
- Режим без потока (если Задача 1 показала, что на Mac нужен главный поток): `GamepadThread.tick()` публичный — то же тело цикла; `MainWindow` вызывает его по `QTimer`. Реализовать `tick()` в любом случае, поток просто зовёт его.

- [ ] **Step 1: Тесты** с `FakeBackend` (скриптуемая последовательность `RawPad | None`, запись `set_led`/`rumble`):
  - `test_intents_delivered`; `test_unplug_sends_disconnected_once`; `test_exception_in_poll_sends_disconnected`; `test_stop_sends_disconnected_and_neutral_led`; `test_led_rumble_run_on_poll_thread` (фейк запоминает `threading.get_ident()`); `test_reconnect_reports_connection`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS.
- [ ] **Step 5:** Коммит `feat(gamepad): polling thread with output queue`.

### Task 4: Цвет полосы и вибро-сигналы

**Files:**
- Create: `src/ptztracker/gamepad/feedback.py`
- Test: `tests/gamepad/test_feedback.py`

**Interfaces:**
- Produces: `PadStatus(on_program: bool, on_preview: bool, link_ok: bool, standby: bool, menu_open: bool, following: bool)`; `lightbar_color(s: PadStatus, t: float) -> tuple[int,int,int]` (приоритет и цвета — таблица спеки; мигание оранжевого 1 Гц: чётные полсекунды — цвет, нечётные — (0,0,0)); константы-паттерны `RUMBLE_PRESET_STORED`, `RUMBLE_NOWHERE`, `RUMBLE_FOLLOW_ON` (два по 80 мс, пауза 80), `RUMBLE_FOLLOW_AUTO_OFF` (0,5 с), `RUMBLE_LIMIT`; `is_auto_off(reason: str | None) -> bool` — `True` для всех причин, кроме операторских: `"выключено оператором"`, `"ручное управление"`, `"аварийный стоп"`, `"калибровка"`, `"конец --lab"`, `"разрешение агенту снято"`, `"остановка камеры"`.
- Цвета (RGB, решение агента): красный (255,0,0), оранжевый (255,110,0), фиолетовый (150,0,255), голубой (0,170,255), зелёный (0,200,0), тусклый белый (40,40,40).

- [ ] **Step 1: Тесты:** `test_priority_program_over_all`, `test_link_lost_blinks`, `test_menu_over_following`, `test_following_over_preview`, `test_idle_dim_white`, `test_is_auto_off` (раскачка/«анализ остановился»/«внутренняя ошибка» → True; операторские → False).
- [ ] **Step 2–4:** FAIL → реализовать → PASS.
- [ ] **Step 5:** Коммит `feat(gamepad): lightbar priority and haptic patterns`.

### Task 5: Сосед и шаг ириса

**Files:**
- Modify: `src/ptztracker/vision/analyzer.py`, `src/ptztracker/camera/protocol.py`, `src/ptztracker/camera/session.py`
- Test: `tests/vision/test_analyzer_neighbor.py`, `tests/camera/test_protocol.py`, `tests/camera/test_session.py`

**Interfaces:**
- Produces: `Analyzer.select_neighbor(direction: int) -> int | None` — под `self._lock`: среди `latest().tracks`, видимых в этом кадре (`last_seen == t_capture`), ближайший по центру рамки по x строго с нужной стороны от цели (нет цели — от центра кадра `frame_size[0]/2`); выбирает через `tracker.select`, `_refresh_target()`, событие `safe_event(recorder, "target", id=…, how="pad_left"|"pad_right")`; нет кандидата → `None`, цель не меняется.
- `protocol.parse_iris(body: str) -> int` (`"gi" + 3 hex [+ флаг]` → позиция; `ER`/мусор → `ValueError`).
- `CameraSession.iris_step(d: int) -> None` — не блокирует: в фоне (`threading.Thread(daemon=True)`) берёт последнее известное `self._iris_pos` или, если нет, `scheduler.query(P.Q_IRIS, timeout=1.0)`; если ирис был на авто (`self._iris_auto` не `False`) — сначала `set_iris_auto(False)`; затем `set_iris` с позицией ± `(LENS_MAX-LENS_MIN)/16`, ограничение диапазоном; запоминает `self._iris_pos`. `set_iris`/`set_iris_auto` обновляют эти поля. Слежение **не** трогает.
- Повтор чёрного ящика (`recorder/replay.py`): событие `target` с `how` `pad_*` воспроизводится выбором `id` (проверить, как сейчас обрабатывается `how="click"`; если по `x,y` — добавить ветку по `id`).

- [ ] **Step 1: Тесты:** `test_neighbor_right_of_target`, `test_neighbor_none_on_edge_keeps_target`, `test_neighbor_without_target_from_center`, `test_neighbor_ignores_unseen_tracks` (подменённый `_latest` и трекер с 3 людьми); `test_parse_iris` (`"giFFF0"`→0xFFF, `"gi555"`→0x555, `"ER1"`→ValueError); `test_iris_step_from_auto_switches_manual_without_jump` (симулятор: ирис на авто в позиции X → после `iris_step(+1)` первая команда — ирис-авто-выкл, затем абсолютный ирис ≈ X + шаг, не скачок); `test_iris_step_does_not_disable_follow`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS (+ тест повтора, если менялся `replay.py`).
- [ ] **Step 5:** Коммит `feat: neighbor target selection, iris step`.

### Task 6: Упор камеры

**Files:**
- Create: `src/ptztracker/camera/limit_watch.py`
- Modify: `src/ptztracker/camera/session.py` (создание/остановка)
- Test: `tests/camera/test_limit_watch.py`

**Interfaces:**
- Produces: `LimitWatch(query_pan_tilt: Callable[[], tuple[int,int] | None], query_zoom: Callable[[], int | None], on_limit: Callable[[], None], period_s=0.2, stuck_s=0.3, min_units=2, pulse_s=0.7)`; `set_demand(vx: float, vy: float, vz: float)` — зовётся из Qt при каждой новой скорости с геймпада (не блокирует); `stop()`. Свой поток, спит, пока спрос нулевой; при спросе опрашивает раз в `period_s`; если по оси со спросом позиция за `stuck_s` изменилась ≤ `min_units` (зум: или уже на 0x555/0xFFF в сторону спроса) → `on_limit()` не чаще раза в `pulse_s`. `CameraSession.limit_watch` создаётся в `start()` с `scheduler.query` (`P.Q_PAN_TILT`, `P.Q_ZOOM`, приоритет как у `query`), не опрашивает при `self._quiet` (калибровка), останавливается в `stop()`.

- [ ] **Step 1: Тесты:** виртуальные запросы (позиция стоит / едет): `test_pulses_while_stuck`, `test_no_pulse_while_moving`, `test_no_queries_without_demand`, `test_zoom_edge_immediate`; на симуляторе (`@pytest.mark.slow`): `test_sim_pan_limit` — повернуть к `PAN_LIMIT` быстрым `#PTS99`, держать спрос → `on_limit` вызван ≥ 2 раз за 2 с после упора.
- [ ] **Step 2–4:** FAIL → реализовать → PASS.
- [ ] **Step 5:** Коммит `feat(camera): limit watch for gamepad haptics`.

### Task 7: Мост в окно, источники скорости, сигналы слежения

**Files:**
- Create: `src/ptztracker/ui/gamepad_bridge.py`
- Modify: `src/ptztracker/ui/main_window.py` (`__init__`, `_apply_held`, `release_all_held`, смена камеры, `closeEvent`, `_sync_tally`/таймер статуса, `on_change` слежения)
- Test: `tests/ui/test_gamepad_bridge.py`

**Interfaces:**
- Consumes: `GamepadThread`, `Mapper`, интенты, `lightbar_color`, `PadStatus`, паттерны, `is_auto_off`, `Analyzer.select_neighbor`, `CameraSession.iris_step`, `LimitWatch.set_demand`.
- Produces: `GamepadBridge(QObject)`: сигнал `intents = Signal(list)` (испускается из потока опроса, соединение `Qt.QueuedConnection`), `connected: bool`, `info: dict | None`, `velocity() -> tuple[float,float,float]` (последние vx, vy, vz геймпада), `reset_motion()` (нули + `mapper.latch_stop()`), `set_status(s: PadStatus)` (цвет — только при изменении результата `lightbar_color`), `rumble(pattern)`. Конструктор принимает `backend` (по умолчанию `SdlBackend`; в тестах — `FakeBackend`); если `open()` упал — `connected=False`, всё остальное no-op.
- `MainWindow`: скорость = сумма клавиатуры (`_held`), экранного джойстика уже идёт своим путём через `ManualControls` — его не трогаем; геймпад и клавиатура суммируются в `_apply_held()` с ограничением ±1. `release_all_held()` обнуляет клавиатуру/мышь и переотправляет итог (геймпадная часть сохраняется). `changeEvent(ActivationChange)` — как раньше. `emergency_stop()` дополнительно `bridge.reset_motion()`. Смена камеры — `bridge.reset_motion()` + `mapper.cancel_holds()`. `closeEvent` — `bridge.stop()` до `session.stop()`.
- Обработка интентов в Qt: `PanTilt/Zoom` → запомнить, `_apply_held()`, `limit_watch.set_demand`; `Preset` → `recall_preset/store_preset` (+ `RUMBLE_PRESET_STORED`); `Stop` → `emergency_stop()`; `ToggleFollow` → `toggle_follow()`; `Composition(d)` → соседняя по `list(COMPOSITIONS)` от текущей `cfg.composition`, через `_on_composition`; `IrisStep` → `session.iris_step`; `GainStep` → `session.gain_step` если есть; `Neighbor` → `analyzer.select_neighbor`, `None` → `RUMBLE_NOWHERE`; `PushFocus` → `session.push_focus()`; `Menu*` → Задача 8 (пока игнорировать); `Disconnected` → нули геймпада + `_apply_held()` (только если они были не нулевые), слежение не трогать.
- Слежение: `follower.on_change` сейчас занят `agent_panel.on_follow_change` — сделать цепочку (обёртка вызывает оба). Включилось → `RUMBLE_FOLLOW_ON`; выключилось и `is_auto_off(reason)` → `RUMBLE_FOLLOW_AUTO_OFF`. Колбэк из потока слежения — в мост через очередной сигнал. Упор (`on_limit` из потока LimitWatch) → `RUMBLE_LIMIT` тем же путём.
- Статус для полосы — из существующего таймера статуса окна (не новый таймер): tally выбранной камеры (`AtemTally.tally(input)`, программа/предпросмотр), `session.online`, standby — только если у сессии появилось поле питания (`getattr`), меню — Задача 8, `follower.enabled`.

- [ ] **Step 1: Тесты** (pytest-qt, симулятор, `FakeBackend` с ручной подачей `RawPad`):
  - `test_stick_pans_and_disables_follow_before_move` — включить слежение на сцене симулятора, подать `leftx=1` → слежение выключено, флажки агента сняты, в `sim.received` после выключения первая `#PTS` — движение вправо.
  - `test_deactivate_keeps_gamepad_motion` — стик вправо, `QEvent.ActivationChange` с неактивным окном → последняя `#PTS` не `#PTS5050`.
  - `test_keyboard_release_keeps_gamepad` — стик вправо + `Key_Up` нажать/отпустить → последняя `#PTS` — вправо, не стоп.
  - `test_disconnect_stops_motion_keeps_follow` — слежение вкл, (стик нулевой) отключить → слежение вкл, лишних `#PTS5050` нет; со стиком вправо при выкл. слежении → последняя `#PTS5050`.
  - `test_touchpad_stop_latches` — стик вправо, `touchpad` → `#PTS5050`; стик держат 0,9 → нового движения нет; центр, затем 0,8 → движение.
  - `test_camera_switch_requires_recenter`.
  - `test_r3_toggles_like_space`, `test_l1_r1_composition_steps`, `test_nowhere_neighbor_rumbles`, `test_preset_store_rumbles`.
  - `test_follow_on_double_pulse`, `test_auto_off_long_pulse_not_for_operator` (выключение Пробелом — без длинной вибрации; `follower.disable("анализ остановился")` — с ней).
  - `test_lightbar_changes_only_on_change` — два тика статуса без изменений → один `set_led`.
  - `test_no_pygame_window_works` — бэкенд, у которого `open()` бросает → окно открывается, клавиатура работает.
- [ ] **Step 2–4:** FAIL → реализовать → PASS; `pytest -q -m "not slow"` целиком зелёный.
- [ ] **Step 5:** Коммит `feat(ui): gamepad bridge — motion sources, follow haptics, lightbar`.

### Task 8: Меню Options

**Files:**
- Create: `src/ptztracker/ui/camera_menu.py`
- Modify: `src/ptztracker/ui/main_window.py`, `src/ptztracker/ui/gamepad_bridge.py`, `src/ptztracker/api/backend.py:144`
- Test: `tests/ui/test_camera_menu.py`, `tests/api/test_server.py`

**Interfaces:**
- Consumes: интенты `Menu*`; методы сессии `set_focus_auto`, `set_focus_velocity`, `set_iris_auto`, и (если есть) gain/WB-методы работы по главному окну.
- Produces: `CameraMenu(QWidget)` — дочерний виджет области видео (не диалог, не модальный), `open()`, `close_menu()`, `is_open: bool`, `nav(dx, dy)`, `accept()`; пункты (спека): Фокус Авто/Ручной (в ручном ←/→ — фокус ближе/дальше, пока держишь: `set_focus_velocity(±0.3)` на нажатие, 0 на отпускание — `MenuNav` с `dx=0` по отпусканию в `Mapper`), Баланс белого (неактивен без методов сессии), Ирис Авто/Ручной, Gain Авто/Ручной (неактивен без методов). Автозакрытие 10 с без навигации. Мышь: клик по пункту; клавиатура: стрелки/Enter/Esc только когда меню открыто (Esc закрывает меню **и** остаётся аварийным стопом — сначала стоп). Горячая клавиша открытия — `M` (если редизайн занял — согласовать при слиянии), добавить в `_HOTKEYS`.
- `ApiBackend`: если `w.camera_menu.is_open` → 409 «В программе открыто меню камеры — дождитесь, пока оператор его закроет.»
- Смена камеры и стоп закрывают меню; `Mapper` получает `menu_open=lambda: menu.is_open` (чтение булева из потока опроса — допустимо, атомарно).
- Добавить в `Mapper` (Задача 2) выдачу `MenuNav(0,0)` при отпускании ←/→ в режиме меню — тест `test_menu_nav_release`.

- [ ] **Step 1: Тесты:** `test_options_opens_menu_blocks_presets`, `test_menu_focus_manual_near_far`, `test_menu_autoclose_10s` (виртуальный таймер/`qtbot.wait` с уменьшенной константой через monkeypatch), `test_menu_closes_on_camera_switch_and_stop`, `test_wb_gain_disabled_without_session_methods`, `test_api_409_while_menu_open`, `test_menu_fits_window` (геометрия при 1640×850 — меню внутри области видео), `test_menu_nav_release`.
- [ ] **Step 2–4:** FAIL → реализовать → PASS.
- [ ] **Step 5:** Коммит `feat(ui): camera menu on Options`.

### Task 9: Окно F1, чип геймпада, документы

**Files:**
- Create: `src/ptztracker/ui/hotkeys_dialog.py`
- Modify: `src/ptztracker/ui/main_window.py` (F1, чип), `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/LIVE_TEST_PROMPT.md`, `docs/BACKLOG.md`, `docs/superpowers/plans/2026-10-22-step-b-tracking-and-control.md` (часть 3 «Геймпад» → ссылка на спеку)
- Test: `tests/ui/test_hotkeys_dialog.py`, `tests/ui/test_main_window_layout.py`

**Interfaces:**
- `HotkeysDialog(QDialog)` — две таблицы: клавиатура (из `_HOTKEYS`) и геймпад (раскладка спеки, модульный список `GAMEPAD_HELP: list[tuple[str, str]]` в `hotkeys_dialog.py`). F1 открывает. Чип «Геймпад» в ряду чипов тулбара (после записи): скрыт без геймпада; подключён — «🎮 DualSense» + заряд из `info["power"]`, если есть.

- [ ] **Step 1: Тесты:** `test_f1_opens_hotkeys_with_gamepad_table` (есть строки «тачпад» → «стоп камеры», «R3» → «слежение»), `test_gamepad_chip_hidden_without_device`, существующий тест высоты окна проходит.
- [ ] **Step 2–4:** FAIL → реализовать → PASS.
- [ ] **Step 5:** документы: CHANGELOG `[Не выпущено]` — строка языком оператора («Управление с геймпада PS5: …»); ARCHITECTURE — пакет `gamepad`, путь интентов, правило «кто держит скорость»; DECISIONS — пороги из Global Constraints и итог пробника; LIVE_TEST_PROMPT — шаг проверки (камера НЕ в эфире: USB, Bluetooth, Windows, Mac, окно ATEM в фокусе, выдернуть кабель на ходу, цвета, упор, две вибрации); BACKLOG — подключение gain/WB к геймпаду после слияния работы по главному окну, квадратичная кривая стика по отзыву.
- [ ] **Step 6:** Коммит `feat(ui): F1 hotkeys window, gamepad chip; docs`.

### Task 10 (после слияния работы по главному окну): gain и баланс белого с геймпада

Делается, только когда в `main` у `CameraSession` есть gain и WB. Крестовина ↑/↓ → `gain_step(±1)` (первое нажатие на «Авто» — ручной с текущего значения, как ирис); пункты меню Gain и WB активны. Тесты: `test_dpad_gain_steps_3db`, `test_menu_wb_modes`. Если имена методов там другие — подключить к ним, не дублируя.

### Task 11: Полный прогон и ревью ветки

- [ ] `.venv/bin/pytest -q` (полный, ~9 мин, в фоне) — зелёный; `ruff check src tests` — чисто.
- [ ] Сборка CI Windows (`windows-build.yml`) проходит; `.exe` запускается без геймпада.
- [ ] Независимое ревью всей ветки (Opus) по спеке и Review Focus.
- [ ] Отчёт пользователю: что проверить на площадке (шаг LIVE_TEST_PROMPT), версия — `MINOR` (новая возможность) при релизе, только с его согласия.
