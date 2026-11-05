# Новый стиль окна (брендбук) — план

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести окно PTZ Tracker на брендбук (графит + лайм, Inter, знак со скобками) и разложить кнопки по клеткам камер, как в `docs/design/mockups/full-window-v2.html`; в конце — README с картинкой-обзором.

**Architecture:** Этап 1 — всё, что не трогает `main_window.py` и `manual_controls.py`: токены и QSS в `theme.py`, общие виджеты в `widgets.py` (TRACK, значок геймпада, плашки), рисованные служебные клетки, живая справка, иконка. Этап 2 — после сообщения главной сессии «чувствительность в main»: влить `main`, затем раскладка окна. Этап 3 — README. Этап 2 и 3 детализируются отдельным дополнением к плану перед началом, по коду, который будет в `main` на тот момент.

**Tech Stack:** Python 3.12–3.13, PySide6 (QPainter, QSS, QtSvg), pytest-qt, ruff.

**Spec:** `docs/superpowers/specs/2026-11-05-brand-and-window-redesign-design.md`, `docs/BRANDBOOK.md`.

## Global Constraints

- Цвета только в `src/ptztracker/ui/theme.py` (тест `tests/ui/test_no_hardcoded_colors.py`).
- Значения из брендбука: BG `#17191D`, SURFACE `#1E2126`, RAISED `#272B31`, RAISED_HI `#30353C`, LINE `#33373E`, TEXT `#F5F5F2`, TEXT_DIM `#B4B8BD`, TEXT_FAINT `#858A91`, GREY `#777C83`, LIME `#B7F34A`, LOST `#F0A020`, FAULT `#EF4444`, PGM `#E8202A`, PVW `#2FB65E`. Синего нет.
- Отступы 4/8/12/16/24; линии 1 px; скругления: панели 10, кнопки 8, плашки 5.
- Окно 1640×850, правая колонка без прокрутки, запас под шрифт Windows (`test_side_panel_fits_without_scroll`).
- Инварианты `AGENTS.md`: ручное движение выключает слежение и флажки агента до команды; Esc — стоп всех; поток Qt не ждёт сеть.
- Новые зависимости и файлы — только с лицензией, совместимой с AGPL (Inter — OFL).
- До «чувствительность в main» не трогать `ui/main_window.py` и `ui/manual_controls.py`.
- Проверка после каждой задачи: `PYTHONPATH=src ../../../.venv/bin/pytest -q -m "not slow"` и `../../../.venv/bin/ruff check src tests` (в worktree нет своего `.venv`).
- Каждое заметное изменение — строка в `CHANGELOG.md` → `[Не выпущено]` языком оператора.

## Review Focus

1. Состояния, которые редко видны: TRACK при «ИЩУ», при камере без анализа и при 3–4 камерах с анализом — квадратики не обрезаются и читаются. Тест в задаче 3.
2. Заряд геймпада 0, 100, «неизвестно» (None) и «заряжается» — батарейка рисует число, не вылезает и не падает на None. Тест в задаче 3.
3. «Положение камер» без положения (камера не ответила, `ptz is None`) и у предела ±175° — без исключений, подпись «нет данных» / «у предела поворота». Тест в задаче 4.
4. Справка при наведении на элемент, у которого нет пары (неиспользуемая клавиша) — ничего не подсвечивается, без исключения. Тест в задаче 5.
5. Светлые надписи на лаймовой заливке (TRACK ON, ВЕДУ) — текст графитовый, а не белый. Тест в задаче 3.

---

## Этап 1

### Task 1: Токены брендбука и QSS

**Files:**
- Modify: `src/ptztracker/ui/theme.py`
- Test: `tests/ui/test_theme.py`

**Interfaces:**
- Produces: `C.LIME`, `C.GREY` (новые); `C.ACCENT = C.LIME`, `C.ON = C.LIME`, `C.OK = C.TEXT`, `C.SELECT = C.TEXT`, `C.REMOTE = C.TEXT` (старые имена остаются, чтобы не трогать `main_window.py`); `R_BUTTON = 8`, `R_PANEL = 10`, `R_TAG = 5`; `H_STOP = 42`; `FS_TEXT = 13`; `SP = (4, 8, 12, 16, 24)`.

- [ ] **Step 1: Тест токенов** — заменить `test_tokens_match_mockup` на `test_tokens_match_brandbook`: все значения из Global Constraints, плюс `C.ON == C.LIME`, `C.SELECT == C.TEXT`, и что ни один токен не синий: `all(not (int(v[5:7],16) > int(v[1:3],16) + 40) for v in hex_tokens)` (синий канал не доминирует).
- [ ] **Step 2: Тест QSS** — `test_checked_controls_use_lime`: в `stylesheet()` у `QCheckBox::indicator:checked` и `QSlider::sub-page:horizontal` стоит `C.LIME`; `QPushButton:checked` — рамка `C.TEXT`; у `QComboBox QAbstractItemView` `selection-background-color` — `C.RAISED_HI`.
- [ ] **Step 3:** запустить `tests/ui/test_theme.py` — падает.
- [ ] **Step 4:** поменять значения в `C`, размеры и QSS по брендбуку (ползунок: дорожка 3 px, ручка 16 px; флажок 15 px, радиус 4; меню радиус 10; фокус поля — рамка `TEXT_DIM`). `SLOT_COLORS` пока `(C.TEXT, C.LIME, C.LOST, C.TEXT_DIM)` — задача 4 уберёт цвет как признак камеры.
- [ ] **Step 5:** быстрый набор и ruff зелёные; особенно `test_side_panel_fits_without_scroll`.
- [ ] **Step 6:** коммит `feat(ui): brand book colors and sizes`.

### Task 2: Шрифт Inter в программе

**Files:**
- Create: `src/ptztracker/ui/fonts/Inter.ttf` (переменный, OFL), `src/ptztracker/ui/fonts/OFL.txt`
- Modify: `src/ptztracker/ui/theme.py` (`load_fonts()`, `FONT_FAMILY = "Inter"`), `packaging/ptztracker.spec` (datas), `src/ptztracker/__main__.py` или место создания `QApplication` — вызвать `load_fonts()` перед `apply_dark_theme`
- Test: `tests/ui/test_theme.py`

**Interfaces:**
- Produces: `load_fonts() -> str` — регистрирует файлы из `ui/fonts` через `QFontDatabase.addApplicationFont`, возвращает имя семейства; повторный вызов не дублирует.

- [ ] **Step 1:** скачать Inter с официального релиза `github.com/rsms/inter` — **только с разрешения пользователя** (имя файла, источник, размер — сказать заранее).
- [ ] **Step 2: Тест** `test_inter_is_loaded`: `load_fonts() == "Inter"` и `"Inter" in QFontDatabase.families()`; `apply_dark_theme` даёт `app.font().family() == "Inter"`.
- [ ] **Step 3:** тест падает → реализация → тест проходит; spec PyInstaller кладёт `ptztracker/ui/fonts`.
- [ ] **Step 4:** быстрый набор; правая панель помещается (Inter шире Segoe UI на ~3 %).
- [ ] **Step 5:** коммит `feat(ui): bundle Inter font (OFL)`.

### Task 3: Общие виджеты — TRACK, значок геймпада, плашка состояния, знак, рамка цели

**Files:**
- Modify: `src/ptztracker/ui/widgets.py`, `src/ptztracker/ui/preview.py` (рамка цели — две скобки)
- Test: `tests/ui/test_widgets.py`, `tests/ui/test_preview_overlay.py`

**Interfaces:**
- Produces:
  - `TrackButton.set_view(kind, word, why, show_action)` — прежняя; новые слова: `on` → «TRACK ON» (заливка LIME, текст BG), `lost` → «ИЩУ ЦЕЛЬ» (заливка LOST, текст BG), `off` → «TRACK OFF», `none` → «TRACK —» (бледная).
  - `TrackButton.set_cams(cams: list[tuple[int, str, bool]])` — (номер 1–4, вид `"on"|"lost"|"off"`, выбрана); рисует квадратики 22×22 справа внутри кнопки; сигнал `camToggled(int)` по клику на квадратик (номер камеры).
  - `PadBadge(QWidget)`: `set(number: int, percent: int | None, charging: bool)`; рисует значок геймпада, номер, батарейку 34×17 с числом внутри; цвет заливки: ≤10 FAULT, ≤20 LOST, заряжается LIME, иначе TEXT; число графитовое при заливке ≥ 50 %, иначе TEXT; `None` — «—». `sizeHint` фиксированный, чтобы шапки не прыгали.
  - `StateTag(QLabel)`: `set(kind)` для `"on"` ВЕДУ, `"lost"` ИЩУ, `"off"` ВЫКЛ, `"standby"` ОЖИДАНИЕ, `"no_analysis"` БЕЗ АНАЛИЗА, `"pgm"` PGM, `"pvw"` PVW, `"sim"` СИМУЛЯТОР; стиль — брендбук §6.
  - `BrandMark(QWidget)`: знак (скобки + лаймовая точка), `size` в конструкторе; при размере ≤ 24 — толщина 11/120.
- Consumes: токены задачи 1.

- [ ] **Step 1: Тесты**
  - `test_track_on_text_is_dark`: после `set_view("on", …)` цвет текста в стиле — `C.BG`, фон — `C.LIME`.
  - `test_track_cams_fit`: `set_cams([(1,"on",True),(2,"off",False),(3,"lost",False),(4,"on",False)])` — `sizeHint().width()` вмещает текст и 4 квадратика; клик в центр квадратика 3 испускает `camToggled(3)`.
  - `test_pad_badge_values`: для `(1, 0, False)`, `(2, 100, False)`, `(3, None, False)`, `(4, 46, True)` — `grab()` не падает, `badge.text_for_test()` → `"0"`, `"100"`, `"—"`, `"46"`; ширина одинакова для всех.
  - `test_state_tag_kinds`: каждый вид даёт свой текст; у `"pvw"` нет заливки (`background` прозрачный), у `"pgm"` есть.
  - `test_target_box_is_brackets` (preview): рамка цели рисуется как два угла — пиксель в середине верхней стороны рамки не лаймовый, в углу — лаймовый.
- [ ] **Step 2:** тесты падают.
- [ ] **Step 3:** реализация в `widgets.py` (рисование QPainter, цвета из `C`); `preview.py` — `TARGET_COLOR = qcolor(C.LIME)`, рамка цели — две скобки длиной 30 % стороны, скругление 5.
- [ ] **Step 4:** быстрый набор и ruff.
- [ ] **Step 5:** коммит `feat(ui): brand widgets — track with camera squares, gamepad badge, state tags`.

### Task 4: Служебные клетки — «Сводка» и «Положение камер»

**Files:**
- Modify: `src/ptztracker/ui/service_cell.py`
- Test: `tests/ui/test_service_cell.py`

**Interfaces:**
- Consumes: `ServiceRow` (добавить поля `tally: str | None`, `track: str` из `"on"|"lost"|"off"|"standby"|"no_analysis"`, `battery: dict[int, tuple[int | None, bool]]` — заряд по номеру геймпада, `calibrated: bool`, `video_fps: float | None`, `preset_pans: list[float]`); все поля с умолчаниями, чтобы `main_window.py` продолжал работать без изменений.
- Produces: `position_geometry(row, rect) -> dict` (чистая функция для тестов: центр, угол луча, полуширина конуса `he42.hfov_deg(zoom_frac)/2`, `near_limit: bool` при `abs(pan) > 160`).

- [ ] **Step 1: Тесты**
  - `test_cone_width_follows_zoom`: zoom_frac 0 → полуширина ≈ 31,05°, 1 → ≈ 1,65°.
  - `test_near_limit`: pan 165 → `near_limit`, pan 100 → нет.
  - `test_position_without_ptz`: строка с `ptz=None` — `grab()` не падает, на карточке «нет данных».
  - `test_summary_draws_rows`: 2 строки — `grab()` без ошибок; в тексте отрисовки (через `ServiceCell.summary_lines()` — список строк, который и рисуется) есть «ВЕДУ» и «нет калибровки».
  - `test_layout_cards`: 1–2 камеры — карточки в ряд, 3–4 — 2×2 (`card_rects(n, rect)`).
- [ ] **Step 2:** тесты падают.
- [ ] **Step 3:** «Положение» — компас на карточку (дуга ±175°, засечка 0° сверху, точки пресетов, конус: LIME если `track == "on"`, иначе TEXT; выбранная — рамка TEXT; шкалы наклона −30…+90 и зума; подписи P/T/кратность/угол). «Сводка» — строки: имя, PGM/PVW, состояние, геймпады с зарядом, к/с, калибровка; под таблицей одна строка общего. Цвет камеры как признак убрать: везде номер.
- [ ] **Step 4:** быстрый набор и ruff.
- [ ] **Step 5:** коммит `feat(ui): summary and per-camera compass service cells`.

### Task 5: Живая справка F1

**Files:**
- Create: `src/ptztracker/ui/help_drawings.py` (рисунки геймпада, клавиатуры, мыши как QWidget с областями-кнопками)
- Modify: `src/ptztracker/ui/hotkeys_dialog.py` (класс `HotkeysDialog` остаётся — его создаёт `main_window.py`)
- Test: `tests/ui/test_hotkeys_dialog.py`

**Interfaces:**
- Consumes: `GAMEPAD_HELP` (как есть) и `_HOTKEYS` из `main_window` (поздний импорт, как сейчас).
- Produces:
  - `HELP_GROUPS: dict[str, list[tuple[str, str, str]]]` — по вкладке список (группа, ключ-элемента, подпись); ключи элементов геймпада: `lstick`, `rstick`, `l3`, `r3`, `l2`, `r2`, `l1`, `r1`, `cross`, `circle`, `square`, `triangle`, `dpad_lr`, `dpad_ud`, `touchpad`, `options`, `create`.
  - `HotkeysDialog.highlight(key: str | None)` — подсвечивает элемент на рисунке и строку списка; `HotkeysDialog.tabs` — QTabBar «Геймпад / Клавиатура / Мышь».
  - Рисунок: `PadDrawing.element_at(QPointF) -> str | None`, `PadDrawing.set_highlight(key)`, анимация стика по `QTimer` (круг для `lstick`, влево-вправо для `rstick`, «нажатие» для кнопок).

- [ ] **Step 1: Тесты**
  - `test_every_gamepad_row_has_element`: каждая строка `GAMEPAD_HELP` сопоставлена хотя бы одному ключу рисунка.
  - `test_hover_row_highlights_drawing`: наведение на строку «Левый стик» → `drawing.highlighted == "lstick"` и таймер анимации запущен.
  - `test_hover_drawing_highlights_row`: `highlight("touchpad")` → выделена строка «СТОП КАМЕРЫ».
  - `test_unused_key_no_highlight`: наведение на неиспользуемую клавишу → `highlighted is None`, без исключения.
  - `test_tabs`: три вкладки; на «Клавиатуре» есть строки из `_HOTKEYS` и «F1 — эта справка».
- [ ] **Step 2:** тесты падают.
- [ ] **Step 3:** реализация; окно ~1040×520, список справа 360 px, подсказка «что сделает камера» снизу слева.
- [ ] **Step 4:** быстрый набор и ruff; скриншот окна справки пользователю.
- [ ] **Step 5:** коммит `feat(ui): live F1 help with gamepad, keyboard and mouse drawings`.

### Task 6: Иконка приложения, логотип, окна в общем стиле

**Files:**
- Create: `docs/assets/logo-mark-dark.svg`, `docs/assets/logo-mark-light.svg` (заменить старые новым знаком), `docs/assets/logo-lockup-dark.svg`, `docs/assets/logo-lockup-light.svg`, `scripts/make_icons.py`
- Modify: `packaging/icons/ptztracker.ico`, `ptztracker.icns`, `ptztracker-1024.png`; `src/ptztracker/ui/calibration_dialog.py`, `camera_menu.py`, `joystick.py`, `state.py` — убрать синие/фиолетовые акценты (через токены)
- Test: `tests/ui/test_theme.py` (`test_icon_files_exist_and_square`)

- [ ] **Step 1:** тест: три файла иконки есть, png 1024×1024.
- [ ] **Step 2:** `scripts/make_icons.py`: SVG знака на скруглённом квадрате BG (радиус 22 %) → PNG через `QSvgRenderer`, ICO через `QImageWriter` (16–256), ICNS через `iconutil` на Mac.
- [ ] **Step 3:** окна: `camera_menu` выбранный пункт — рамка TEXT; `joystick` активная ручка — TEXT; графики калибровки — P TEXT, T LOST, Z LIME.
- [ ] **Step 4:** быстрый набор, ruff; скриншоты окна и диалогов пользователю.
- [ ] **Step 5:** `CHANGELOG.md`: «Новый внешний вид: графит и лайм, шрифт Inter, новый значок программы; живая справка F1». Коммит `feat(ui): new app icon and brand colors in dialogs`.

## Этап 2 (после «чувствительность в main») — задачи, детализировать перед началом

7. Верхняя строка: BrandMark, ATEM, значки геймпадов (`PadBadge` для каждого), журнал, AGENT, ☰ с лаймовой точкой обновления, TRACK с квадратиками (`set_cams`, `camToggled` → тот же путь, что Пробел для той камеры).
8. Шапка клетки: номер, имя ▾ с «…», `StateTag` (PGM/PVW, состояние, СИМУЛЯТОР), `PadBadge` геймпадов клетки (>2 — «+N»), ⏻ (вопрос, предупреждение для PGM), ⋯ (OSD, поток, калибровка, диагностика, изменить, убрать, ожидание); разрешение и к/с в углу видео.
9. ☰ — только общее; калибровка, диагностика, ожидание и поток уходят в ⋯.
10. Правая колонка: заголовок выбранной камеры; Кадр — три силуэта и ползунок на трёх отметках, клавиши 1/2/3 (4 и 5 больше ничего не делают); сетка «подпись 52 px + управление»; фокус отдельной строкой.
11. Нижняя строка: нагрузка анализа слева, «Пресеты» (P), «Справка» (F1), версия; без подписей клавиш.
12. Выдвижная панель пресетов: строка выбранной камеры, 8 карточек, прокрутка вбок, тянется вверх за другими камерами; миниатюры (папка данных, по id камеры, + поворот для компаса); клик по пресету другой камеры — выбрать, выключить слежение и флажки агента, вызвать.
13. Пустые клетки: при 3 камерах «Сводка», при 2 — «Сводка» и «Положение», при 0 — приветствие.
14. Тесты «ничего не обрезано» при 1640×850 и шрифте ×1,25; `docs/OPERATOR.md` под новое окно; CHANGELOG (в т.ч. «пояс слева/справа временно убран, вернётся в 1.2»).

## Этап 3 — README

15. `scripts/make_overview.py`: снимает окно на симуляторе (2 камеры, пресеты открыты) и дорисовывает лаймовые номера; `docs/assets/overview.png`. README: новый логотип (тёмный/светлый), картинка-обзор, под ней пронумерованные пояснения (как `docs/design/mockups/layout.html`), быстрый старт, ссылки на документацию.
