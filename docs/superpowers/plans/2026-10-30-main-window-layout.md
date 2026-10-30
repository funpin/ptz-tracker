# PTZ Tracker — компактное главное окно, gain/WB и кнопка питания

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Верхняя панель — одна строка значков с короткими подписями и TRACK ON/OFF справа; всё редкое — в одном меню ☰ в окне; правая панель без прокрутки с ирисом, gain, пресетами, фокусом и балансом белого; камера в standby показывает круглую кнопку питания вместо ошибки.

**Architecture:**
- `camera/protocol.py` — команды и разбор питания, gain, WB, цветовой температуры (по замеру HE40 2026-10-30).
- `camera/session.py` — снимок питания и изображения из ответов, которые уже приходят планировщику (`on_sent`); команды питания/gain/WB через планировщик. Qt сети не ждёт.
- `sim/ptz_model.py` — питание (p0/p3/p1, ER2 в standby) и gain/WB с теми же диапазонами и ER3.
- `ui/state.py` — чистые функции «что показать» для значков (текст, kind, подсказка); `ui/widgets.py` — `StatusPill`, `TrackButton`, `PowerOverlay`; `ui/icons.py` + SVG Tabler.
- `ui/main_window.py` — новая сборка тулбара, меню ☰, накладки на видео; `ui/manual_controls.py` — строки Gain и WB, компактнее; `ui/follow_panel.py` — без плитки состояния.

**Tech Stack:** PySide6 (QtSvg уже в составе PySide6), Tabler Icons (MIT) как SVG-файлы. Новых пакетов нет.

**Spec:** `docs/superpowers/specs/2026-10-30-main-window-layout-design.md`; замеры — §7 `docs/superpowers/specs/2026-10-19-ptz-tracker-design.md` (блок AW-HE40 2026-10-30).

## Global Constraints

- **Границы.** `framing/`, `control/`, `vision/`, `video/` не трогать. `camera/he42.py` параллельно правит сессия калибровки HE40 — в него не писать; факты питания/gain/WB живут в §7 спеки и в docstring `camera/protocol.py`. Геймпад PS5 делает другая сессия (в своей ветке): здесь не добавлять ввод геймпада.
- Окно 1640×850: `MainWindow.minimumSizeHint().height() <= 760`; правая панель при 1640×850 **без прокрутки** (виджет внутри `QScrollArea` по высоте не больше области).
- Инварианты AGENTS.md: ручное действие/Esc/смена камеры выключают слежение **до** следующей команды; аварийный стоп не теряется; поток Qt не ждёт сеть; пароли не попадают в подсказки (адреса — через `redact_url`/`strip_credentials`); агент двигает камеру только при двух флажках, ручное действие их снимает.
- Подписи — по-русски; короткие английские допустимы: `TRACK ON` / `TRACK OFF`, `PGM`, `PVW`, `AF`, `WB`, `Gain`.
- Значки верхней панели: kind ∈ `ok | warn | fault | off`; ok — зелёный (`C.PVW` не использовать: зелёный Tally только для Tally — новый токен `C.OK`), warn — `C.LOST`, fault — `C.FAULT`, off — `C.TEXT_FAINT`. Цвета только из `ui/theme.py` (тест `test_no_hardcoded_colors.py`).
- Замер HE40: gain `OGU:` коды `08 + 3·k` до `38` (≈0…48 dB, шаг 3 dB ⚠) и `80` = авто; WB `OAW:` 0 ATW, 1 AWB A, 2 AWB B, 3 ATW?, 4 3200K, 5 5600K, 9 VAR; цветовая температура `OSD:B1:` индекс `000–04B`, подпись ⚠ «≈ K» = 2400 + 100·индекс; `#O` → `p1`/`p0`/`p3`; в standby прочие команды → `ER2`/`eR2`; MJPEG в standby продолжает идти; включение: `p3` ~3,5 с, `p1` через 4 с, видео ~8 с.
- Каждое заметное изменение — строка в `CHANGELOG.md` → `[Не выпущено]` языком оператора (последняя задача). Версию не поднимать без пользователя.

## Review Focus

1. **Команда в standby.** Оператор двигает джойстик/жмёт пресет, пока камера в standby: камера отвечает `ER2`, планировщик считает er2 «занята» и повторяет бесконечно. Ожидание: в standby сессия не ставит в очередь ручные и gain/WB-команды (тихо пропускает), аварийный стоп — ставит. Тест в Task 3.
2. **Неизвестный ответ на `#O`** (другая модель, мусор, ошибка HTTP): никаких «камера выключена» — `power == "unknown"`, экран как раньше. Тест в Task 1 и Task 3.
3. **Выключение во время слежения/агента.** Пункт «Перевести в режим ожидания…» при TRACK ON: слежение выключается до отправки `#O0`, флажки агента снимаются; «Отмена» в подтверждении — слежение уже выключено, `#O0` не ушёл. Тест в Task 8.
4. **Узкое окно 1280.** Подписи «Видео»/«Анализ» прячутся, имя камеры и TRACK целы, ничего не обрезано посередине слова. Тест в Task 6.
5. **Gain/WB вне диапазона или ER3.** Ползунок отправляет только допустимые коды; если камера вернула ER3, интерфейс после следующего опроса показывает значение камеры. Тест в Task 1 (коды) и Task 3 (откат по опросу).

---

### Task 1: Протокол — питание, gain, WB, цветовая температура

**Files:**
- Modify: `src/ptztracker/camera/protocol.py`
- Test: `tests/camera/test_protocol.py`

**Interfaces:**
- Produces:
  - константы `POWER_ON = "#O1"`, `POWER_STANDBY = "#O0"`, `Q_GAIN = "QGU"`, `Q_WB = "QAW"`, `Q_COLOR_TEMP = "QSD:B1"`
  - `parse_power(body: str) -> Literal["on", "standby", "switching", "unknown"]` — `p1`/`p0`/`p3`, всё остальное `unknown` (без исключений)
  - `GAIN_AUTO = 0x80`; `GAIN_CODES: tuple[int, ...] = (0x08, 0x0B, …, 0x38)`; `gain_db(code: int) -> int | None` (None для авто); `gain(code: int) -> str` → `OGU:0B`; `ValueError` для кода вне `GAIN_CODES ∪ {GAIN_AUTO}`; `parse_gain(body) -> int` (`OGU:11` → 0x11)
  - `WB_MODES: dict[int, str] = {0: "ATW", 1: "AWB A", 2: "AWB B", 3: "ATW 2", 4: "3200K", 5: "5600K", 9: "VAR"}`; `wb_mode(m: int) -> str` → `OAW:9`; `parse_wb(body) -> int`
  - `COLOR_TEMP_MAX = 0x4B`; `color_temp(index: int) -> str` → `OSD:B1:01A`; `parse_color_temp(body) -> int`; `color_temp_kelvin(index: int) -> int` = 2400 + 100·index (⚠ в docstring)
  - `cgi_for` уже шлёт не-`#` в `aw_cam` — ничего не менять.

- [ ] **Step 1: Failing tests** `test_parse_power`: `"p1"→"on"`, `"p0"→"standby"`, `"p3"→"switching"`, `"ER2:O"`, `""`, `"x"` → `"unknown"`. `test_gain_codes`: `GAIN_CODES[0]==0x08`, `GAIN_CODES[-1]==0x38`, `len==17`, `gain(0x0B)=="OGU:0B"`, `gain(0x80)=="OGU:80"`, `gain(0x09)` и `gain(0x39)` → `ValueError`, `gain_db(0x11)==9`, `gain_db(0x80) is None`, `parse_gain("OGU:11")==0x11`. `test_wb`: `wb_mode(9)=="OAW:9"`, `wb_mode(6)` → `ValueError`, `parse_wb("OAW:9")==9`. `test_color_temp`: `color_temp(0x1A)=="OSD:B1:01A"`, `color_temp(0x4C)` → `ValueError`, `parse_color_temp("OSD:B1:01A")==0x1A`, `color_temp_kelvin(0x1A)==5000`. `test_build_url_cam`: `build_url("h","OGU:0B")` содержит `/aw_cam?cmd=OGU:0B`.
- [ ] **Step 2:** `.venv/bin/pytest -q tests/camera/test_protocol.py` — FAIL (нет имён).
- [ ] **Step 3:** Реализовать в `protocol.py`; парсеры при неверном ответе бросают `ValueError` (как `parse_zoom`), кроме `parse_power`. В docstring модуля — блок «HE40 2026-10-30» со ссылкой на §7.
- [ ] **Step 4:** тесты PASS, `ruff check src tests`.
- [ ] **Step 5:** commit `feat(camera): power, gain, white balance commands (HE40 live probe)`.

### Task 2: Симулятор — питание и изображение

**Files:**
- Modify: `src/ptztracker/sim/ptz_model.py` (`_dispatch`, новые поля)
- Test: `tests/sim/test_ptz_model.py`

**Interfaces:**
- Consumes: Task 1 (`GAIN_CODES`, `GAIN_AUTO`, `WB_MODES`, `COLOR_TEMP_MAX`).
- Produces: `SimPtz.power: str` (`"on"|"standby"|"switching"`), `SimPtz.power_on_s: float = 4.0` (время `p3`→`p1`), `SimPtz.gain_code = 0x08`, `SimPtz.wb = 0`, `SimPtz.color_temp = 0x1A`. Ответы: `#O` → `p1|p0|p3`; `#O0` → `p0`; `#O1` → `p1` (а `#O` затем `p3` до `power_on_s`, затем `p1`); в `standby`/`switching` любая другая команда → `"ER2:<cmd без #>"` (движение не меняется); `OGU:xx` / `QGU` → `OGU:xx`; `OAW:x` / `QAW`; `OSD:B1:xxx` / `QSD:B1`; недопустимое значение → `"ER3:OGU"` и т.п.
- Видео симулятора в standby не меняем (у HE40 MJPEG тоже идёт). По той же причине логику переподключения видео не трогаем: признак standby — только `#O` (спека уточнена).

- [ ] **Step 1: Failing tests** (часы симулятора — как в существующих тестах `test_ptz_model.py`): `test_standby_rejects_motion` (`#O0` → `p0`; `#PTS7050` → начинается с `ER2`; позиция не меняется), `test_power_on_switching_then_on` (`#O1`; `#O`=="p3" при t+1 с; =="p1" при t+4,1 с), `test_gain_range` (`OGU:0B` → `OGU:0B`, `QGU` → `OGU:0B`; `OGU:09` → `ER3:OGU`), `test_wb_and_temp` (`OAW:6` → `ER3:OAW`; `OSD:B1:04B` ok; `OSD:B1:04C` → `ER3:OSD`).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Реализовать; `CameraError` из `check_response` уже понимает `ER2`/`ER3` (регистр не важен).
- [ ] **Step 4:** `pytest -q tests/sim tests/camera` PASS.
- [ ] **Step 5:** commit `feat(sim): camera power state and gain/WB`.

### Task 3: Сессия — снимок питания и изображения, команды

**Files:**
- Modify: `src/ptztracker/camera/session.py`
- Test: `tests/camera/test_session.py`

**Interfaces:**
- Consumes: Task 1, Task 2.
- Produces (всё потокобезопасно, только чтение полей/запись через планировщик — UI вызывает из Qt без ожидания):
  - `CameraSession.power -> str` — последний разбор ответа heartbeat (`"unknown"` до первого ответа и при `ok=False`/непонятном ответе)
  - `CameraSession.image -> ImageState` — `@dataclass(frozen=True) ImageState(gain: int | None, wb: int | None, color_temp: int | None)` в `session.py`
  - `set_power(on: bool) -> None` — `#O1`/`#O0` ключом `"power"`, `Priority.MANUAL`, `only_if_changed=False`, `ttl=ONE_SHOT_TTL_S`; при `on=False` сначала `self._manual()` (выключает слежение, снимает флажки агента). Ответ `p0` сразу обновляет `power`.
  - `set_gain(code: int)`, `set_wb(mode: int)`, `set_color_temp(index: int)` — ключи `"gain"`, `"wb"`, `"ctemp"`; **не** вызывают `_manual()` (изображение, не движение — как ирис)
  - `refresh_image() -> None` — ставит запросы `QGU`, `QAW`, `QSD:B1` (`Priority.QUERY`); вызывается на старте и после ответа `p1` при переходе из не-`on`; `_heartbeat` каждые 5-й цикл (≈10 с) тоже его вызывает
  - Разбор ответов — новый слушатель `self.scheduler.add_sent_listener(self._on_reply)` (инфо: `cmd`, `reply`, `ok`); слушатель не бросает исключений
  - **Standby-фильтр:** `_submit` при `power in ("standby","switching")` не ставит команду, кроме ключа `"power"` (аварийный стоп идёт мимо `_submit` — не трогать)
- [ ] **Step 1: Failing tests** на `CameraSession` с `cfg.kind == "sim"`, `analysis=False` (образец — существующие тесты файла): `test_power_from_heartbeat` (по умолчанию `"on"` через ≤3 с), `test_standby_roundtrip` (`set_power(False)` → `power=="standby"` ≤1 с; `set_pan_tilt_velocity(0.5,0)` → в `sim.received` нет нового `#PTS` за 0,5 с; `set_power(True)` → `"switching"`, затем `"on"` при `sim.power_on_s=0.5`), `test_power_off_disables_follow` (follower включён → `set_power(False)` → `not follower.enabled` и в `sim.received` `#PTS5050`/стоп раньше `#O0`), `test_image_state_read_and_set` (`image.gain == 0x08` после старта; `set_gain(0x0B)` → `image.gain == 0x0B` после следующего `refresh_image`), `test_unknown_power_reply` (подменить ответ `#O` на `"ER1:O"` — `power=="unknown"`, `_submit` команды пропускает).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Реализовать.
- [ ] **Step 4:** `pytest -q tests/camera -m "not slow"` PASS.
- [ ] **Step 5:** commit `feat(camera): power and image state in session`.

### Task 4: Иконки и значок верхней панели

**Files:**
- Create: `src/ptztracker/ui/icons/*.svg`, `src/ptztracker/ui/icons/LICENSE-tabler.txt`, `src/ptztracker/ui/icons.py`
- Modify: `src/ptztracker/ui/theme.py` (токен `OK`), `src/ptztracker/ui/widgets.py`, `src/ptztracker/ui/state.py`, `packaging/ptztracker.spec` (datas `ptztracker/ui/icons`)
- Test: `tests/ui/test_widgets.py`, `tests/ui/test_state.py`, `tests/test_packaging.py`

**Interfaces:**
- Produces:
  - `icons.icon(name: str, color: str, size: int = 18) -> QIcon` — читает `icons/<name>.svg`, подменяет `currentColor` на `color`, рисует через `QSvgRenderer`; кэш по `(name,color,size)`. Набор (имена Tabler): `video`, `adjustments`, `photo-video`, `brain`, `switch-horizontal`, `history`, `robot`, `menu-2`, `focus-2`, `power`, `user-x`, `skeleton` (если нет в Tabler — `body-scan`). Брать SVG из пакета `@tabler/icons` (MIT) той версии, лицензию положить рядом.
  - `widgets.StatusPill(QToolButton)`: `set(text: str, kind: str, tooltip: str)`, `set_compact(on: bool)` (прятать текст), `kind -> str`, `full_text -> str`; клик — `clicked`, или `setMenu(...)` + `InstantPopup`.
  - `state.py` — чистые функции, каждая `-> tuple[str, str, str]` (текст, kind, подсказка):
    - `video_pill(stats: SourceStats | None, stream_label: str) -> …` — подключено: `"1280×720 · 25"`, ok; нет: `"нет"`, fault, подсказка = `stats.error` целиком (+ `stats.url`, уже без пароля)
    - `analysis_pill(error: str | None, fps: float | None, infer_ms: float | None, device: str | None) -> …` — `"14 к/с"` ok; `None` fps → `"…"`, warn, подсказка «Загрузка модели…»; error → `"нет"`, fault
    - `atem_pill(configured, online, inp, state, known=True) -> … | None` — None, если не настроен; иначе текст `"ATEM"` / `"ATEM 3"` / `"ATEM 3 · PGM"` / `"ATEM 3 · PVW"`, kind: off — нет связи/вход не задан, warn — вне диапазона, `pgm`/`pvw` — Tally-цвета; подсказка = текст прежнего `tally_chip`
    - `power_view(power: str, online: bool) -> str` — `"standby" | "switching" | "offline" | "on"` (для накладки на видео)
- [ ] **Step 1: Failing tests:** `test_video_pill_ok/ error_text_only_in_tooltip` (текст ≤ 14 символов, `"Errno" not in text`, `"Errno" in tooltip`, `"***"`-адрес не содержит пароля), `test_analysis_pill_loading`, `test_atem_pill_pgm` (`("ATEM 3 · PGM","pgm",…)`), `test_power_view` (таблица из 5 случаев: `("standby",True)→"standby"`, `("on",False)→"offline"`, `("unknown",True)→"on"`…), `test_icon_recolors` (`icon("power","#ff0000").pixmap(18,18)` не пустой), `test_status_pill_compact_hides_text`, `test_spec_bundles_icons` (в `packaging/ptztracker.spec` есть `ui", "icons"` или путь к папке).
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** `pytest -q tests/ui tests/test_packaging.py -m "not slow"` PASS.
- [ ] **Step 5:** commit `feat(ui): icons and status pills`.

### Task 5: Верхняя панель и меню ☰

**Files:**
- Modify: `src/ptztracker/ui/main_window.py`, `src/ptztracker/ui/agent_panel.py` (обёртка в немодальное окно), `src/ptztracker/ui/follow_panel.py` (плитка `follow_btn` уходит)
- Modify tests: `tests/ui/test_main_window.py`, `test_main_window_layout.py`, `test_update_chip.py`, `test_tally_ui.py`, `test_follow_panel*.py`, `tests/api/*` где есть `agent_panel`
- Test: `tests/ui/test_main_window_layout.py`

**Interfaces:**
- Consumes: Task 4.
- Produces (атрибуты `MainWindow`, на них опираются тесты и Task 6–8):
  - `camera_combo` (как было), `cameras_btn: QToolButton` с меню `add_action/edit_action/del_action` (заменяют `add_btn/edit_btn/del_btn`; слоты `_add_camera/_edit_camera/_delete_camera` те же)
  - `video_pill`, `analysis_pill`, `atem_pill` (`StatusPill`); `video_pill` меню — `QActionGroup` потоков из `STREAM_CHOICES` (заменяет `stream_combo`; `_on_stream_changed` получает key)
  - `blackbox_btn` (`StatusPill` только иконка) с меню: `save_moment_action`, `open_replay_action`, `rec_settings_action`
  - `agent_badge` (`StatusPill` «AGENT», виден ⇔ `agent_panel.allow_check.isChecked()`), клик открывает агент-окно
  - `main_menu_btn` с меню: `calib_action`, `diag_action`, `atem_action`, `agent_action`, `power_action` (Task 8), `check_updates_action`, `update_action` (видим, если есть новая версия; заменяет `update_chip`), подменю «Чёрный ящик» (те же 3 действия)
  - `track_btn: TrackButton` — справа, checkable, `NoFocus`; текст `TRACK ON`/`TRACK OFF`, kind on/off/недоступно; клик = `toggle_follow()`; подсказка = прежнее пояснение FollowPanel (`TrackingView.why`) + «Пробел»
  - `self.menuBar()` не используется: меню «Сервис» нет. F9 — `QShortcut(Qt.Key_F9, self)` с `Qt.WindowShortcut`.
  - `agent_panel` живёт в `QDialog` с `setModal(False)`/`Qt.Tool`; `agent_action` показывает его. API отвечает 409 при `QApplication.activeModalWidget()` (`api/backend.py:144`) — немодальное окно его не задевает; тест это фиксирует.
  - Удаляются: `link_label`, `video_label`, `analysis_label`, `record_label`, `atem_chip`, `update_chip`, `stream_combo`, `add_btn/edit_btn/del_btn`, `service_menu`, плитка `follow_panel.follow_btn` и `status_label`; `FollowPanel.set_state/show_hint` переезжают на `track_btn` (те же сигнатуры на `MainWindow`, тесты переписать на `w.track_btn`).
  - Сжатие в узком окне: `resizeEvent` → при ширине тулбара меньше суммы `sizeHint` по очереди `video_pill.set_compact(True)`, `analysis_pill…`, `atem_pill…`; `camera_combo` и `track_btn` не сжимаются.
- [ ] **Step 1: Failing tests:** `test_no_service_menu` (`w.menuBar().actions() == []` или меню скрыто), `test_f9_without_menu` (F9 → `save_moment` вызван, monkeypatch), `test_top_bar_texts_short` (после 2 тиков симулятора у всех pill `len(full_text) <= 16`), `test_track_btn_toggles_follow` (выбрать цель, клик → `follower.enabled`, `track_btn.text()=="TRACK ON"`, клик → `OFF`), `test_agent_badge_follows_flag`, `test_agent_window_does_not_block_api` (открыть `agent_action` → `POST /follow` при обоих флажках не 409), `test_narrow_window_compacts` (`resize(1280, 800)` → `video_pill` компактен, `camera_combo.isVisible()`, `track_btn.text()` полный), `test_stream_menu_switches_stream`.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать; переписать затронутые старые тесты на новые имена (поведение то же). **Step 4:** `pytest -q -m "not slow"` PASS.
- [ ] **Step 5:** commit `feat(ui): compact top bar, single in-window menu, TRACK button`.

### Task 6: Область видео — накладки, понятная ошибка, кнопка питания

**Files:**
- Modify: `src/ptztracker/ui/widgets.py` (`PowerOverlay`), `src/ptztracker/ui/main_window.py`, `src/ptztracker/ui/preview.py` (только текст «Нет видео»)
- Test: `tests/ui/test_power_overlay.py` (новый), `tests/ui/test_preview_overlay.py`

**Interfaces:**
- Consumes: Task 3 (`power`, `set_power`), Task 4 (`power_view`, `icons`).
- Produces:
  - `PowerOverlay(QWidget)` поверх `preview` (дочерний `video_frame`, растянут на него): `set_view(view: str, camera_name: str)`; `"standby"` — круглая кнопка `power_btn` (≥ 72 px) + «Камера {name} найдена, но выключена (режим ожидания)» + «Нажмите, чтобы включить»; `"switching"` — кнопка неактивна, «Камера включается…»; иначе скрыт. Сигнал `powerOnRequested`.
  - `MainWindow`: `powerOnRequested` → `session.set_power(True)` (без подтверждения). Обновление вида — в `_update_status` по таймеру.
  - Поверх видео в левом верхнем углу: `reset_target_btn` (иконка `user-x`), `skeleton_check` → `QToolButton` checkable с иконкой `skeleton` (имя атрибута `skeleton_check` сохранить, `isChecked/toggled` те же). Подсказка «Цель не выбрана — кликните по человеку» — короткий ярлык внизу видео, пока цели нет (заменяет `target_label`; имя `target_label` сохранить на этом ярлыке, тексты прежние).
  - `preview` «Нет видео»: первая строка «Нет видео с камеры {name}», вторая — короткая причина ≤ 60 символов (без `[Errno …]`, без адреса); полный текст — в подсказке `video_pill`.
- [ ] **Step 1: Failing tests:** `test_overlay_shows_on_standby` (sim: `session.set_power(False)` → в ≤2 с `overlay.isVisible()` и текст содержит «режим ожидания»), `test_power_button_turns_on` (клик `power_btn` → `"#O1"` в `sim.received`, затем `overlay` скрыт при `sim.power_on_s=0.5`), `test_overlay_hidden_when_offline` (power `unknown` → скрыт), `test_no_video_text_is_human` (подставить `stats.error` с `[Errno 1094995529] … rtsp://admin:***@…` → в тексте превью нет `Errno` и `rtsp://`).
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(ui): standby power button and video overlays`.

### Task 7: Правая панель без прокрутки, Gain и WB

**Files:**
- Modify: `src/ptztracker/ui/manual_controls.py`, `src/ptztracker/ui/follow_panel.py`, `src/ptztracker/ui/main_window.py` (сборка `side`: убрать раздел «Цель» и `agent_panel`)
- Test: `tests/ui/test_manual_controls.py`, `tests/ui/test_main_window_layout.py`

**Interfaces:**
- Consumes: Task 1 (`GAIN_CODES`, `GAIN_AUTO`, `gain_db`, `WB_MODES`, `COLOR_TEMP_MAX`, `color_temp_kelvin`), Task 3 (`set_gain/set_wb/set_color_temp`, `image`).
- Produces:
  - `ManualControls.gain_slider` (QSlider 0…16 по индексам `GAIN_CODES`, подпись «{dB} dB»), `gain_auto` (QCheckBox «Авто» → `GAIN_AUTO`); `wb_combo` (QComboBox из `WB_MODES`), `ctemp_slider` (0…`COLOR_TEMP_MAX`, подпись «≈{K} K», активен только при `wb == 9`)
  - `ManualControls.sync_image(img: ImageState) -> None` — выставляет значения из снимка **без** отправки команд (`blockSignals`), не трогает виджет, который оператор держит/меняет в последние 1,5 с
  - Порядок: Кадр (`comp_buttons` в один ряд, подписи «1 Пояс», «2 ◧», «3 ◨», «4 Рост», «5 Общий», полные имена в подсказках) · Мягко–Резко · `calib_label` (только когда нужно) · Камера (джойстик + зум) · Ирис · Gain · Пресеты (1–8 одной строкой) · Фокус · WB; «СТОП» закреплён как сейчас
  - Высота: при окне 1640×850 `side.sizeHint().height() <= side_scroll.viewport().height()`. Если не влезает — сначала ужать отступы/высоты строк; сворачивание Фокуса/WB — только если после этого не влезло (решение в коммите с цифрами).
- [ ] **Step 1: Failing tests:** `test_side_panel_fits_without_scroll` (окно `resize(1640,850)`, `qtbot.wait(100)`, условие выше; `side_scroll.verticalScrollBar().maximum() == 0`), `test_gain_slider_sends_valid_code` (sim: сдвинуть на индекс 1 → в `sim.received` есть `"OGU:0B"`), `test_gain_auto` (`"OGU:80"`), `test_ctemp_only_in_var` (`wb_combo` = 5600K → `ctemp_slider.isEnabled()` False; VAR → True; сдвиг → `"OSD:B1:…"`), `test_sync_image_does_not_send` (вызов `sync_image` → новых `OGU/OAW/OSD` в `sim.received` нет), `test_image_controls_do_not_disable_follow` (слежение включено → сдвиг gain → `follower.enabled` всё ещё True).
- [ ] **Step 2:** FAIL. **Step 3:** реализовать; `MainWindow._update_status` вызывает `controls.sync_image(s.image)`. **Step 4:** `pytest -q -m "not slow"` PASS.
- [ ] **Step 5:** commit `feat(ui): right panel without scrolling; gain and white balance`.

### Task 8: Перевод камеры в standby из меню

**Files:**
- Modify: `src/ptztracker/ui/main_window.py`
- Test: `tests/ui/test_power_overlay.py`

**Interfaces:**
- Consumes: Task 3, Task 5 (`power_action`).
- Produces: `power_action` текст «Включить камеру» при `power in ("standby",)`, иначе «Перевести камеру в режим ожидания…»; `_power_menu() -> None`: если включение — `set_power(True)`; если выключение — **сначала** выключить слежение и флажки агента (тот же путь, что ручное действие), затем `QMessageBox.question` «Перевести камеру {name} в режим ожидания? Видео пропадёт, включение займёт около 8 секунд.»; «Да» → `set_power(False)`.
- [ ] **Step 1: Failing tests:** `test_standby_asks_and_stops_follow` (TRACK ON → monkeypatch `QMessageBox.question` → `No`: `follower.enabled` False, `"#O0"` нет в `sim.received`; → `Yes`: `"#O0"` есть), `test_power_action_text_follows_state`.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(ui): camera standby from menu with confirmation`.

### Task 9: Документы и полный прогон

**Files:**
- Modify: `CHANGELOG.md`, `docs/ARCHITECTURE.md` (раздел UI: тулбар/меню ☰/питание), `docs/BACKLOG.md` (⚠ сверить dB и кельвины с меню камеры; HE42 — подтвердить те же коды; ER2-повторы аварийного стопа в standby), `docs/LIVE_TEST_PROMPT.md` (проверка кнопки питания и gain/WB на площадке)
- [ ] **Step 1:** Строки в `CHANGELOG.md` → `[Не выпущено]`: «Верхняя панель — короткие значки, подробности во всплывающих подсказках», «Всё редкое — в меню ☰ в окне; меню “Сервис” убрано», «Кнопка TRACK ON/OFF справа вверху», «Gain и баланс белого в правой панели», «Камера в режиме ожидания — кнопка включения прямо на видео».
- [ ] **Step 2:** `.venv/bin/ruff check src tests` и `.venv/bin/pytest -q` (полный, ~9 мин, в фоне) — PASS; при падениях из BACKLOG «flaky under load» — перезапустить только их.
- [ ] **Step 3:** `.venv/bin/python -m ptztracker --sim` — снимок окна 1640×850 и 1280×800 (через встроенный API `/frame.jpg` не годится — нужен скриншот окна; `QWidget.grab()` в маленьком скрипте) — приложить пользователю.
- [ ] **Step 4:** commit `docs: main window redesign, power, gain/WB`.
