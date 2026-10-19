# PTZ Tracker — новый вид пульта и Tally с ATEM

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Интерфейс получает единый вид из одного модуля темы. Состояние слежения читается с первого взгляда: плитка постоянного размера и ярлык на видео. Рамка видео показывает Tally с микшера ATEM: красная — камера в эфире (PGM), зелёная — в превью (PVW).

**Architecture:**
- `ui/theme.py` хранит все цвета, размеры, шрифт и общий стиль Qt (QSS).
- `ui/widgets.py` содержит компоненты: кнопку с клавишей, плитку состояния, чип, заголовок раздела, рамку видео.
- `ui/state.py` — чистые функции: из состояния слежения и Tally получается то, что показать (слово, цвет, пояснение).
- Новый пакет `tally/` без Qt: UDP-клиент ATEM в фоновом потоке, снимок состояния для опроса таймером. Его же потом использует основная ветка для защиты эфира.

**Tech Stack:** PySide6 (QSS, QPainter), стандартный `socket`/`threading`. Новых зависимостей нет.

**Spec:** согласованный макет v2 — https://claude.ai/artifact/HRgSmcoGCrcdoCd7rL5Xp8 (копия токенов и состояний ниже, в «Global Constraints»). Архитектура — `docs/ARCHITECTURE.md`.

## Global Constraints

**Границы.** Менять только `src/ptztracker/ui/`, `tests/ui/`, новый `src/ptztracker/tally/`, `tests/tally/`, документы. В `config.py` — только два новых поля **в конце** dataclass (Task 8). `control/`, `vision/`, `camera/`, `video/`, `sim/` не трогать: их параллельно переделывает основная ветка.
- `ui/preview.py`: только цвета и шрифты рамок (позже там появится редактор зон).
- Публичные сигналы и методы, которыми `MainWindow` связан со слежением, сохраняются: `FollowPanel.followRequested/compositionChanged/effortChanged`, `set_state(enabled, reason)`, `show_hint`, `set_composition`, `set_effort`, `set_calibrated`; атрибуты `follow_btn` (checkable, `NoFocus`), `comp_buttons`, `effort_slider`, `status_label`, `calib_label`; в `MainWindow` — `preview`, `controls`, `follow_panel`, `camera_combo`, `stream_combo`, `add_btn/edit_btn/del_btn`, `target_label`, `reset_target_btn`, `skeleton_check`, `link_label`, `video_label`, `analysis_label`, `calib_action`, `diag_action`, `toggle_follow()`, `release_all_held()`, `_update_status()`. На них опираются существующие тесты.
- Инварианты: любое ручное действие, Esc и новая кнопка «СТОП КАМЕРЫ» выключают слежение и останавливают камеру **до** следующей команды. Поток Qt не ждёт сеть: ATEM читается из снимка таймером.
- Окно ~1640×850: `MainWindow.minimumSizeHint().height() <= 760`, правая панель прокручивается.

**Токены (точные значения).**

| Токен | Значение | Назначение |
|---|---|---|
| `BG` | `#13161C` | фон окна |
| `SURFACE` | `#1B1F27` | панели, строка меню, статус |
| `RAISED` | `#252A34` | кнопки, поля |
| `RAISED_HI` | `#2E3440` | наведение, дорожка ползунка |
| `LINE` | `#333A47` | границы |
| `TEXT` / `TEXT_DIM` / `TEXT_FAINT` | `#E7EAF0` / `#A0A8B6` / `#6C7486` | текст |
| `ACCENT` | `#4C8DF6` | выбор, действие, ручка джойстика |
| `ON` | `#3D8BFF` | слежение ведёт; рамка цели «ВЕДУ» |
| `LOST` | `#F0A020` | цель потеряна |
| `REMOTE` | `#A78BFA` | пульт |
| `FAULT` | `#EF4444` | раскачка, стоп |
| `PGM` / `PVW` | `#E8202A` / `#1FB35A` | только Tally: рамка видео и чип ATEM |

Шрифт: `Segoe UI` (на Mac — системный). Размеры, px: подпись 12, текст 14, кнопки 15, слово состояния 24 (жирность 800). Отступы 4/8/12/16. Высоты: кнопка 34, кнопка выбора кадра 40, стоп 44, плитка состояния **118 (постоянная)**. Радиус: 6 кнопки, 10 панели; пресеты и джойстик круглые.

**Состояния слежения (плитка, ярлык на видео).** Подпись над словом всегда «Слежение», клавиша «Пробел».

| kind | Слово (24 px) | Цвет | Пояснение (ровно до 2 строк) | Ярлык на видео |
|---|---|---|---|---|
| `off` | ВЫКЛ | `TEXT_FAINT` | без причины: «Кликните по человеку на видео, затем нажмите Пробел.»; с причиной: «Выключено: {reason}» | ВЫКЛ |
| `on` | ВЕДУ ЦЕЛЬ | `ON` | «Камера ведёт цель №{id}. Ручное действие или Esc выключат слежение.» | СЛЕЖЕНИЕ |
| `lost` | ЦЕЛЬ ПОТЕРЯНА | `LOST`, лампа мигает 1 Гц | «Камера стоит и ждёт. Человек вернётся в кадр — слежение продолжится.» | ЦЕЛЬ ПОТЕРЯНА |
| `remote` | ПУЛЬТ | `REMOTE` | «Камерой управляют с пульта, слежение на паузе. Пробел — продолжить.» | ПУЛЬТ |
| `swing` | РАСКАЧКА | `FAULT` | «Слежение остановлено: камера быстрее, чем ожидалось.» + кнопка «Калибровка…» | РАСКАЧКА |

`remote` пока ничем не включается (обнаружение пульта — этап 4 основной ветки), но вид и функция готовы.

**Tally.** Чип в верхней строке: `ATEM · вход {n} · ЭФИР` (PGM, заливка `PGM`), `ATEM · вход {n} · превью` (PVW), `ATEM · вход {n}` (не в эфире), `ATEM · нет связи` (серый), `ATEM · вход не задан` (у камеры `atem_input == 0`). Если `atem_ip` пуст — чипа нет. Ярлык в правом верхнем углу видео: «ЭФИР · PGM» / «ПРЕВЬЮ · PVW», иначе скрыт. Рамка видео 4 px: `PGM` / `PVW` / `LINE`.

**Подпись** «by funpin.ru & Claude» — справа в строке состояния, цвет `TEXT_FAINT`.

**Язык.** Весь текст интерфейса — по-русски, простыми словами.

## Review Focus

1. Длинная причина выключения (`reason` из `Follower`, до ~90 символов) — плитка не растёт, текст обрезается многоточием на 2-й строке, полный текст в подсказке (tooltip). Тест в Task 3.
2. ATEM пропал из сети посреди эфира — через 2 с чип «ATEM · нет связи», рамка серая, клиент сам переподключается, когда ATEM вернётся. Тест в Task 8.
3. Мусорный UDP-пакет или обрезанная команда `TlIn` — клиент не падает и не теряет связь. Тест в Task 8.
4. Нажатие «СТОП КАМЕРЫ» мышью во время слежения — слежение выключено, ушёл `#PTS5050`, как от Esc. Тест в Task 6.
5. В старом `config.json` нет полей ATEM, или в `atem_input` строка/отрицательное число — программа стартует, вход считается «не задан». Тест в Task 8.

---

### Task 1: Тема — токены и общий стиль

**Files:**
- Modify: `src/ptztracker/ui/theme.py`
- Test: `tests/ui/test_theme.py`

**Interfaces:**
- Produces: класс-пространство `C` с атрибутами-строками hex из таблицы токенов (`C.BG`, `C.ON`, …); `qcolor(hex: str, alpha: int = 255) -> QColor`; константы `FONT_FAMILY`, `FS_CAPTION=12`, `FS_TEXT=14`, `FS_BUTTON=15`, `FS_STATE=24`, `H_BUTTON=34`, `H_CHOICE=40`, `H_STOP=44`, `H_STATE=118`, `SP=(4, 8, 12, 16)`, `R_BUTTON=6`, `R_PANEL=10`; `stylesheet() -> str`; `apply_dark_theme(app)` (имя сохраняется): Fusion + палитра из токенов + `app.setStyleSheet(stylesheet())` + шрифт приложения `FS_TEXT`.
- Варианты виджетов задаются динамическим свойством `variant`: `ghost` (кнопка без фона), `choice` (выбор кадра, отмеченная — заливка `ACCENT` 24 %), `stop` (рамка и текст `FAULT`), `section` (заголовок раздела, `TEXT_DIM`, 12.5 px, 600); чипы — свойство `kind` (см. Task 2). QSS выбирает их через `[variant="ghost"]`.

- [ ] **Step 1: тесты** `test_tokens_match_mockup` (`C.ON == "#3D8BFF"`, `C.PGM == "#E8202A"`, `C.PVW == "#1FB35A"`, `C.FAULT == "#EF4444"`), `test_stylesheet_uses_only_tokens` (каждый `#xxxxxx` в `stylesheet()` есть среди значений `C`), `test_apply_dark_theme_sets_palette` (после `apply_dark_theme(QApplication.instance())` `palette().window().color().name() == C.BG.lower()`; стиль непустой; в конце теста вернуть `app.setStyleSheet("")`).
- [ ] **Step 2:** `.venv/bin/pytest tests/ui/test_theme.py -q` — FAIL (нет `C`).
- [ ] **Step 3:** реализовать. QSS покрывает `QMainWindow, QWidget` фон, `QPushButton` (высота `H_BUTTON`, радиус, `:hover` `RAISED_HI`, `:checked`, `:disabled` `TEXT_FAINT`), `QComboBox`, `QLineEdit`, `QSpinBox`, `QCheckBox`, `QSlider` (дорожка 6 px `RAISED_HI`, ручка 18 px), `QScrollBar:vertical` (8 px), `QMenuBar`, `QMenu`, `QStatusBar`, `QToolTip`, `QGroupBox` (для диалогов: без рамки, заголовок как `section`), варианты `ghost/choice/stop/section`.
- [ ] **Step 4:** тест PASS; `.venv/bin/pytest -q -m "not slow" tests/ui` — PASS.
- [ ] **Step 5:** commit `feat(ui): theme tokens and global stylesheet`.

### Task 2: Компоненты

**Files:**
- Create: `src/ptztracker/ui/widgets.py`
- Test: `tests/ui/test_widgets.py`

**Interfaces:**
- Consumes: Task 1 (`C`, размеры, `qcolor`).
- Produces:
  - `paint_keycap(p: QPainter, right: float, cy: float, text: str) -> float` — рисует «клавишу» (рамка `LINE`, нижняя граница 2 px, текст `TEXT_DIM` 11 px, жирный) правым краем в `right`, возвращает её ширину.
  - `KeyButton(text: str, hotkey: str | None = None, variant: str | None = None)` — `QPushButton`; клавиша рисуется справа поверх `super().paintEvent`; `hotkey` доступен как свойство; `sizeHint` учитывает ширину клавиши.
  - `Chip(text: str = "", kind: str = "neutral")` — `QLabel` с точкой-лампой; `set(text: str, kind: str)`; `kind ∈ {"neutral","ok","accent","warn","fault","pgm","pvw"}` через свойство `kind` + `style().polish`; `kind()` возвращает текущий.
  - `SectionHeader(title: str, hint: str = "")` — строка: заголовок слева, подсказка `TEXT_FAINT` справа.
  - `StateTile()` — `QPushButton`, checkable, `NoFocus`, `setFixedHeight(H_STATE)`; дочерние надписи прозрачны для мыши; `set_view(kind: str, word: str, why: str, show_action: bool)`; атрибуты `word_label`, `why_label` (`QLabel`, перенос строк, высота 2 строки, обрезка многоточием через `QFontMetrics.elidedText` по 2-й строке, полный текст в `toolTip`), `action_btn` (`KeyButton("Калибровка…")`, скрыта, кроме `swing`); сигнал `actionClicked`. Цвет рамки, заливка (цвет состояния 16 % поверх `RAISED`), лампа (мигает 1 Гц таймером только в `lost`) — из таблицы состояний. Клавиша «Пробел» рисуется в строке подписи «Слежение».
  - `VideoFrame(inner: QWidget)` — `QFrame`, рамка 4 px; `set_tally(state: str | None)` (`"pgm"|"pvw"|None`), `set_tracking_tag(text: str, color: str)`; угловые ярлыки — дочерние `QLabel` `tracking_tag` (левый верх) и `onair_tag` (правый верх, скрыт без Tally), переставляются в `resizeEvent`; `tally()` возвращает текущее.

- [ ] **Step 1: тесты** `test_keybutton_reserves_room_for_keycap` (`KeyButton("Рост","4").sizeHint().width() > QPushButton("Рост").sizeHint().width()`), `test_chip_kind_switch` (`set("x","pgm")` → `kind()=="pgm"`), `test_state_tile_height_constant` (для всех 5 kinds из таблицы `height()==118`, и с причиной длиной 200 символов тоже; `why_label.toolTip()` содержит полный текст), `test_state_tile_action_only_for_swing` (`action_btn.isHidden()` везде, кроме `swing`), `test_state_tile_click_toggles` (`click()` → `isChecked()`), `test_video_frame_tally_tag` (`set_tally("pgm")` → `onair_tag.text()=="ЭФИР · PGM"` и видим; `set_tally(None)` → скрыт).
- [ ] **Step 2:** FAIL (нет модуля).
- [ ] **Step 3:** реализовать по интерфейсам.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(ui): widgets — keycap button, state tile, chip, video frame`.

### Task 3: Что показать — чистые функции состояния

**Files:**
- Create: `src/ptztracker/ui/state.py`
- Test: `tests/ui/test_state.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) TrackingView(kind: str, word: str, why: str, tag: str, color: str, show_calibrate: bool)`.
  - `tracking_view(enabled: bool, reason: str | None, target_id: int | None, target_state: str | None, diverged: bool = False, remote: bool = False) -> TrackingView` — правила по таблице состояний: `remote` важнее всего; `enabled and target_state == "lost"` → `lost`; `enabled` → `on`; не `enabled` и `diverged` → `swing`; иначе `off` (с причиной или без).
  - `tally_chip(atem_configured: bool, online: bool, atem_input: int, state: str | None) -> tuple[str, str] | None` — `(текст, kind чипа)` по таблице Tally или `None`, если ATEM не настроен.

- [ ] **Step 1: тесты** — по одному на строку таблицы состояний (`test_view_off_without_reason`, `test_view_off_with_reason`, `test_view_on`, `test_view_lost`, `test_view_swing_has_calibrate`, `test_view_remote_wins`) с точными словами и пояснениями из таблицы; `test_tally_chip_variants` — 5 строк таблицы Tally, плюс `None` без ATEM.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(ui): tracking and tally view rules`.

### Task 4: Панель слежения на новых компонентах

**Files:**
- Modify: `src/ptztracker/ui/follow_panel.py`
- Test: `tests/ui/test_follow_panel_view.py` (новый; старый `test_follow_panel.py` должен проходить без изменений)

**Interfaces:**
- Consumes: `StateTile`, `KeyButton`, `SectionHeader` (Task 2), `TrackingView`, `tracking_view` (Task 3).
- Produces: `FollowPanel` — `QWidget` (уже не `QGroupBox`); `follow_btn` — это `StateTile`; `status_label` — это `follow_btn.why_label`; кнопки кадра — `KeyButton(f"{comp.title}", str(i+1), variant="choice")`, сетка 2 колонки; раздел «Кадр» с ползунком «Мягко ↔ Резко»; `calib_label` — короткая строка `LOST`: «Камера не откалибрована: Сервис → Калибровка (2 мин)»; новый метод `set_view(v: TrackingView)`; новый сигнал `calibrateRequested` (от `action_btn`). `set_state(enabled, reason)` сохраняет поведение: строит вид через `tracking_view(enabled, reason, None, None)`. `show_hint(text)` пишет в `status_label`.
- Раздел «Цель» собирает `MainWindow` (Task 6), не панель.

- [ ] **Step 1: тесты** `test_set_view_updates_tile_and_keeps_height` (`set_view(tracking_view(True, None, 1, "lost"))` → `follow_btn.word_label.text()=="ЦЕЛЬ ПОТЕРЯНА"`, высота 118), `test_composition_buttons_show_digit` (`comp_buttons["full"].hotkey == "4"`), `test_calibrate_signal` (`set_view(... swing ...)`, клик по `action_btn` → `calibrateRequested` испущен).
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** новые и `tests/ui/test_follow_panel.py` PASS.
- [ ] **Step 5:** commit `feat(ui): follow panel on state tile`.

### Task 5: Джойстик, превью, ручное управление (механическая, Sonnet)

**Files:**
- Modify: `src/ptztracker/ui/joystick.py`, `src/ptztracker/ui/preview.py` (только цвета/шрифты), `src/ptztracker/ui/manual_controls.py`
- Test: `tests/ui/test_manual_controls.py` (дописать)

**Interfaces:**
- Consumes: Task 1, Task 2.
- Produces:
  - Джойстик: тот же API; рисунок из токенов: круг `RAISED`, обвод `TEXT_FAINT` 3 px, внутреннее кольцо 0,5 r и крест `LINE`, 24 риски по краю (каждая 6-я длиннее), ручка 13 px радиус: `ACCENT` при нажатии, `TEXT_DIM` в покое, светлый ободок. Размер 176×176.
  - `preview.py`: `TARGET_COLOR = qcolor(C.ON)`, `LOST_COLOR = qcolor(C.LOST)`, зоны `LOST`/`PVW`-зелёный заменить на `qcolor(C.LOST, 200)` и `qcolor(C.ON, 220)`, фон `qcolor(C.BG)`, шрифт подписей `FONT_FAMILY`. Логику не трогать.
  - `ManualControls`: разделы `SectionHeader("Ручное управление", "выключает слежение")`, джойстик и рядом клавиши ← → ↑ ↓ (нарисованные `paint_keycap` или `KeyButton` без действия) с подписью «поворот клавишами»; зум «− Шире ⟷ Ближе +»; новая `stop_btn = KeyButton("СТОП КАМЕРЫ", "Esc", variant="stop")`, высота `H_STOP`, сигнал `stopRequested` (сама кнопка камеру не трогает — это делает окно, Task 6); пресеты — 9 круглых кнопок в ряд (`preset_buttons` сохраняется), подсказка «Shift+клик — записать»; «Фокус и ирис» — один раздел. Все `QGroupBox` убрать. `near_btn`, `far_btn`, `zoom_slider`, `af_check`, `iris_*`, `release_all`, `bind` сохраняются.
- [ ] **Step 1: тест** `test_stop_button_emits` (клик `stop_btn` → `stopRequested`), `test_stop_button_shows_esc` (`stop_btn.hotkey == "Esc"`).
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** `.venv/bin/pytest -q tests/ui/test_manual_controls.py tests/ui/test_joystick.py tests/ui/test_preview_overlay.py` PASS.
- [ ] **Step 5:** commit `feat(ui): restyle joystick, preview colours, manual controls; stop button`.

### Task 6: Главное окно — новая раскладка

**Files:**
- Modify: `src/ptztracker/ui/main_window.py`
- Test: `tests/ui/test_main_window_layout.py` (новый)

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces:
  - Верхняя строка (`QToolBar`, не перемещаемая): `camera_combo` с подписью «Камера», `add_btn/edit_btn/del_btn` (`variant="ghost"`), `stream_combo` с подписью «Видео», растяжка, чипы `atem_chip` (скрыт до Task 9), `link_label`, `video_label`, `analysis_label` — все `Chip`.
  - Видео: `self.video_frame = VideoFrame(self.preview)`.
  - Правая панель (ширина 372, прокрутка): `follow_panel`; раздел «Цель»: `Chip` с номером цели (`target_label` — сохраняется как `QLabel`-текст «Цель: …», тексты те же, что сейчас), `reset_target_btn`, `skeleton_check` («Скелет»); `controls`.
  - Строка состояния: слева `showMessage`, справа клавиши (Пробел — слежение, 1–5 — кадр, ← → ↑ ↓ — поворот, + − — зум, Esc — стоп) и `credit_label` с текстом «by funpin.ru & Claude».
  - `emergency_stop()` — общий для Esc и `controls.stopRequested`: `release_all_held()`, затем `session.emergency_stop()`. Ветка Esc в `eventFilter` вызывает его.
  - `_sync_follow()` дополнительно читает `analyzer.latest()` (только снимок) и `getattr(follower.controller, "diverged", False)`, строит `tracking_view(...)` и вызывает `follow_panel.set_view(v)` и `video_frame.set_tracking_tag(v.tag, v.color)` — только при изменении `v`.
  - `follow_panel.calibrateRequested` → `_run_calibration`.
  - `resize(1640, 850)`.
- [ ] **Step 1: тесты** `test_credit_visible` (`"by funpin.ru & Claude"` в `w.credit_label.text()`), `test_stop_button_is_emergency_stop` (симулятор: цель выбрана, слежение включено, `w.controls.stop_btn.click()` → `not follower.enabled`, через 400 мс последний `#PTS…` == `#PTS5050`), `test_tracking_tag_follows_state` (после включения — `video_frame.tracking_tag.text()=="СЛЕЖЕНИЕ"`, после Пробела — «ВЫКЛ»), `test_tile_height_constant_in_window` (высота `follow_btn` 118 до и после включения), `test_window_fits_1640x850` (`minimumSizeHint().height() <= 760`, `width() <= 1640` после `show()`).
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** весь `tests/ui` PASS.
- [ ] **Step 5:** commit `feat(ui): main window layout — toolbar, video frame, stop button, credit`.

### Task 7: Диалоги и запрет ручных цветов (механическая, Sonnet)

**Files:**
- Modify: `src/ptztracker/ui/calibration_dialog.py`, `diagnostics_dialog.py`, `camera_dialog.py`
- Test: `tests/ui/test_no_hardcoded_colors.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: в диалогах нет `setStyleSheet("color: #…")` — вместо этого `variant`/`kind` или `C.*`; основная кнопка диалога (`Начать калибровку`, `Запустить`) — `setDefault(True)`; заголовки групп как `section`.
- [ ] **Step 1: тест** `test_only_theme_has_colors`: для каждого `src/ptztracker/ui/*.py`, кроме `theme.py`, нет совпадений с `#[0-9a-fA-F]{6}\b` и с `QColor\(\s*\d`.
- [ ] **Step 2:** FAIL (пока есть ручные цвета). **Step 3:** заменить. **Step 4:** PASS, весь `tests/ui` PASS.
- [ ] **Step 5:** commit `refactor(ui): dialogs use theme; guard against hardcoded colours`.

### Task 8: Клиент Tally ATEM (без Qt) и поля конфига

**Files:**
- Create: `src/ptztracker/tally/__init__.py`, `src/ptztracker/tally/atem_protocol.py`, `src/ptztracker/tally/atem.py`
- Modify: `src/ptztracker/config.py` — **только** дописать в конец `CameraConfig`: `atem_input: int = 0  # вход ATEM (1…), 0 — не задан`; в конец `AppConfig`: `atem_ip: str = ""  # адрес микшера ATEM, пусто — Tally выключен`.
- Test: `tests/tally/fake_atem.py`, `tests/tally/test_atem_protocol.py`, `tests/tally/test_atem_client.py`, `tests/test_config.py` (дописать)

**Протокол** (по открытым описаниям: Skaarhoj, libqatem, PyATEMMax; на живом ATEM не проверено — записать в BACKLOG):
- UDP, порт микшера 9910. Заголовок 12 байт: `byte0 = flags | (length >> 8 & 0x07)`, `byte1 = length & 0xFF` (длина всего пакета), `2–3` session id, `4–5` номер подтверждаемого пакета, `6–9` нули, `10–11` номер этого пакета. Флаги в `byte0`: `0x08` нужен ответ-подтверждение, `0x10` hello, `0x20` повтор, `0x80` подтверждение.
- Подключение: клиент шлёт hello 20 байт: `byte0=0x10`, длина 20, session `0x1337`, `byte12=0x01`, остальное нули. Микшер отвечает hello с `byte12=0x02`; клиент шлёт подтверждение (`0x80`, длина 12, номер 0). Далее микшер шлёт пакеты с флагом `0x08` и новым session id: на каждый клиент отвечает подтверждением с тем же session id и номером пакета в байтах 4–5. Пустые пакеты с `0x08` — keepalive.
- Команды в теле: `uint16 длина (с заголовком 8 байт)`, 2 байта пропуск, 4 байта имя ASCII, данные. `TlIn`: `uint16 N`, затем N байт; байт i — вход i+1: бит 0 — программа (PGM), бит 1 — превью (PVW). Оба бита — считать PGM.

**Interfaces:**
- Produces (`tally/atem_protocol.py`, чистые функции): `hello_packet(session: int = 0x1337) -> bytes`; `ack_packet(session: int, packet_id: int) -> bytes`; `@dataclass(frozen=True) Header(flags: int, length: int, session: int, ack_id: int, packet_id: int)`; `parse_header(data: bytes) -> Header | None` (None, если < 12 байт или длина не совпадает); `iter_commands(payload: bytes) -> Iterator[tuple[str, bytes]]` (обрезанную команду пропускает и останавливается); `parse_tlin(body: bytes) -> tuple[int, ...]` (флаги по входам; короткое тело → `()`).
- Produces (`tally/atem.py`): `TallyState = Literal["pgm", "pvw"] | None`; `@dataclass(frozen=True) TallySnapshot(online: bool, flags: tuple[int, ...])` с методом `state(input: int) -> TallyState`; `class AtemTally(host: str, port: int = 9910, timeout_s: float = 2.0, on_change: Callable[[TallySnapshot], None] | None = None)`: `start()`, `stop()` (не блокирует дольше 0,5 с), `snapshot() -> TallySnapshot`, `tally(input: int) -> TallyState`, свойство `online: bool`. Поток `daemon`, имя `atem-tally`. Нет пакетов дольше `timeout_s` → `online=False`, флаги очищены, hello каждые 1 с до ответа. `on_change` вызывается из потока клиента при смене снимка (исключения в нём логируются и глотаются). Без Qt-импортов.
- Produces: `atem_input_of(cam) -> int` в `tally/__init__.py`: `cam.atem_input`, если это `int` в 1…40, иначе 0.

- [ ] **Step 1: тесты протокола** `test_hello_bytes` (точные 20 байт), `test_ack_bytes`, `test_parse_header_roundtrip`, `test_parse_header_rejects_short`, `test_parse_tlin` (тело `00 03 01 02 03` → `(1, 2, 3)`), `test_iter_commands_skips_truncated`.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать `atem_protocol.py`. **Step 4:** PASS.
- [ ] **Step 5: фальшивый ATEM** `tests/tally/fake_atem.py`: `FakeAtem` на `127.0.0.1:0` в потоке: отвечает на hello, шлёт `TlIn` и keepalive каждые 0,2 с, считает подтверждения; методы `set_tally(flags: tuple[int, ...])`, `pause()`/`resume()` (перестаёт отвечать), `send_garbage()`; свойство `port`.
- [ ] **Step 6: тесты клиента** `test_connects_and_reads_tally` (`set_tally((0, 1, 2))` → в пределах 2 с `tally(2)=="pgm"`, `tally(3)=="pvw"`, `tally(1) is None`, `online`), `test_acks_server_packets` (у FakeAtem подтверждений ≥ 3), `test_goes_offline_and_reconnects` (`timeout_s=0.6`; `pause()` → в пределах 1,5 с `not online` и `tally(2) is None`; `resume()` → снова `online`), `test_survives_garbage` (`send_garbage()`, затем `online` и тally верны), `test_on_change_called`, `test_stop_is_fast` (`stop()` < 0,5 с), `test_no_qt_import` (в подпроцессе `import ptztracker.tally.atem`; `"PySide6" not in sys.modules`). В `tests/test_config.py`: `test_old_config_without_atem_fields` (JSON без полей → `atem_ip==""`, `atem_input==0`), `test_atem_input_of_rejects_garbage` (`"3"`, `-1`, `99` → 0; `3` → 3).
- [ ] **Step 7:** FAIL. **Step 8:** реализовать `atem.py`, поля конфига, `atem_input_of`. **Step 9:** PASS; `.venv/bin/pytest -q -m "not slow"` PASS.
- [ ] **Step 10:** commit `feat(tally): ATEM tally client (UDP 9910) and config fields`.

### Task 9: Tally в интерфейсе

**Files:**
- Create: `src/ptztracker/ui/atem_dialog.py`
- Modify: `src/ptztracker/ui/camera_dialog.py`, `src/ptztracker/ui/main_window.py`
- Test: `tests/ui/test_tally_ui.py`

**Interfaces:**
- Consumes: `AtemTally`, `TallySnapshot`, `atem_input_of` (Task 8); `tally_chip` (Task 3); `VideoFrame.set_tally`, `Chip.set` (Task 2).
- Produces:
  - `AtemDialog(ip: str, parent=None)`: поле «Адрес ATEM» (`ip_edit`, пусто — выключить Tally, проверка `is_valid_ip`), подсказка «ATEM должен быть в той же сети. Пусто — Tally выключен.»; `ip() -> str`.
  - `CameraDialog`: `atem_spin` (`QSpinBox` 0…40, 0 показывается как «нет»), подпись «Вход ATEM»; `result_config()` переносит `atem_input`.
  - `MainWindow`: пункт «Сервис → ATEM…» (`atem_action`); `self.atem: AtemTally | None` создаётся из `cfg.atem_ip` при старте и пересоздаётся после диалога (старый `stop()`); `_sync_tally()` на таймере 100 мс: читает `atem.snapshot()`, для текущей камеры берёт `atem_input_of(cam)`, вызывает `video_frame.set_tally(...)` и `atem_chip.set(...)` / прячет чип — только при изменении. `closeEvent` останавливает клиента. У камеры-симулятора вход тоже можно задать (для проверки на площадке с настоящим ATEM).
- [ ] **Step 1: тесты** с `FakeAtem` из `tests/tally/fake_atem.py`: `test_tally_frame_red_when_camera_in_pgm` (`cfg.atem_ip="127.0.0.1"`, порт FakeAtem — через параметр `MainWindow(..., atem_port=...)` только для тестов; у камеры-симулятора `atem_input=2`; `set_tally((0,1))` → в пределах 2 с `w.video_frame.tally()=="pgm"`, `"ЭФИР" in w.atem_chip.text()`), `test_no_atem_no_chip` (`atem_ip=""` → `atem_chip.isHidden()`, `video_frame.tally() is None`), `test_camera_dialog_keeps_atem_input` (`atem_spin.setValue(3)` → `result_config().atem_input == 3`), `test_atem_dialog_rejects_bad_ip`.
- [ ] **Step 2:** FAIL. **Step 3:** реализовать. **Step 4:** PASS; весь быстрый набор PASS.
- [ ] **Step 5:** commit `feat(ui): ATEM tally — frame, chip, settings`.

### Task 10: Документы, скриншоты, финальная проверка

**Files:**
- Modify: `docs/ARCHITECTURE.md` (строки `ui/` и новый `tally/` в карте модулей; поток ATEM → снимок → таймер UI), `docs/DECISIONS.md` (цвета состояний и почему «ведёт» синий; Tally — рамка видео), `docs/BACKLOG.md` (проверить протокол ATEM на живом микшере; Tally как защита эфира — основная ветка; обнаружение пульта включит состояние `remote`)

- [ ] **Step 1:** `.venv/bin/ruff check src tests` — без замечаний.
- [ ] **Step 2:** `.venv/bin/pytest -q -m "not slow"` — PASS.
- [ ] **Step 3:** скриншоты «после» на симуляторе 1640×850 в состояниях ВЫКЛ, ВЕДУ ЦЕЛЬ, ЦЕЛЬ ПОТЕРЯНА, РАСКАЧКА и с Tally PGM (через FakeAtem); сравнить с макетом v2.
- [ ] **Step 4:** commit `docs: UI theme and ATEM tally`.
- [ ] **Step 5:** независимое ревью всей ветки (`git diff main...HEAD`), исправить найденное, повторить Steps 1–2.
