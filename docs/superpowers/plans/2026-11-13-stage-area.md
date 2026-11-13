# Область сцены — план реализации

> Выполнение: `superpowers:executing-plans`, одна сессия, TDD по задачам, независимое ревью изменений в конце. Ничего не
> коммитить без согласия оператора: в рабочей копии незакоммиченные правки слежения от 2026-11-13.

**Цель:** оператор задаёт рамку сцены; программа сама не берёт в цель и не узнаёт потерянного в людях вне рамки.

**Спека:** `docs/superpowers/specs/2026-11-13-stage-area-design.md`. **Макет:** `docs/design/mockups/stage-area.html`.

## Общие ограничения

- Область не задана или выключена — ни один существующий тест не меняется, поведение прежнее.
- Клик оператора, `/target`, `--select` областью не ограничиваются.
- Настройка области не шлёт камере команд движения и не выключает слежение.
- Поток Qt не ждёт сеть и нейросеть; запись только наблюдает.
- Пороги существующих тестов не ослаблять. Числа — по записям и в `docs/DECISIONS.md`.
- Записи с кадрами (в них люди) в репозиторий не идут.
- Новых зависимостей нет.

## Review Focus

1. Вид камеры неизвестен (нет `#APC` дольше 3 с) — область не действует и не рисуется, а не «застывает» (`test_unknown_view_disables_area`).
2. Ведомая цель, вышедшая за рамку непрерывно, не теряется (`test_led_target_walks_out_of_area`).
3. Клик по человеку вне области выбирает его (`test_click_outside_area_selects`).
4. Старый `config.json` без `stage_area` читается; испорченное значение — как «не задана» (`test_bad_stage_area_ignored`).
5. Знаки осей: точка мира сохраняет градусы при повороте, наклоне и зуме симулятора (`test_world_point_is_stable_on_sim`).

---

### Task 1: Геометрия без камеры

**Files:** Create `src/ptztracker/control/stage_area.py`, `tests/control/test_stage_area.py`.

**Produces:** `CameraView(pan_deg, tilt_deg, hfov_deg)`; `view_from(apc: tuple[int, int], zoom_pos: int) -> CameraView`;
`to_world(view, x, y) -> (pan_deg, tilt_deg)` и `to_frame(view, pan_deg, tilt_deg) -> (x, y)` (x, y — доли кадра, проекция
через тангенс, 16:9); `StageArea(pan0, pan1, tilt0, tilt1, on=True)` с `contains(pan, tilt)`, `to_json()`,
`from_json(d) -> StageArea | None`, `frame_rect(view) -> (x0, y0, x1, y1)`; `head_point(person) -> (x_px, y_px)`.

- [ ] Тесты: `to_frame(to_world(x, y))` возвращает точку (широкий угол и теле, углы кадра); центр кадра = направление камеры; `contains` на границах; `from_json` мусора → `None`; `head_point` с носом и без.
- [ ] Реализация.

### Task 2: Настройка камеры

**Files:** Modify `src/ptztracker/config.py`; Test `tests/test_config.py`.

**Produces:** `CameraConfig.stage_area: dict | None = None`; чистка при загрузке через `StageArea.from_json`.

- [ ] Тесты: запись и чтение; старый конфиг без поля; `test_bad_stage_area_ignored`.
- [ ] Реализация.

### Task 3: Вид камеры на момент кадра

**Files:** Modify `src/ptztracker/control/follower.py`, `src/ptztracker/camera/session.py`; Tests `tests/control/test_follower_view.py`, `tests/camera/test_session_stage.py`.

**Consumes:** Task 1. **Produces:** `Follower.camera_view(t) -> CameraView | None` — последний `#APC` и зум, досчитанные
`picture_motion` до `t`; `None`, если ответу больше `VIEW_MAX_AGE_S = 3.0`. `CameraSession.set_stage_area(StageArea | None)`;
опрос `#APC` идёт при включённой области и без записи и слежения.

- [ ] Тесты: `test_unknown_view_disables_area`; вид между ответами `#APC` при движении; `test_world_point_is_stable_on_sim` (поворот, наклон, зум); опрос `#APC` при области без записи и слежения; режим «только запросы» не нарушен.
- [ ] Реализация.

### Task 4: Трекер — узнавание только на сцене

**Files:** Modify `src/ptztracker/vision/analyzer.py`, `src/ptztracker/vision/tracker.py`; Test `tests/vision/test_tracker_stage.py`.

**Consumes:** Task 1, 3. **Produces:** `Analyzer.stage: Callable[[Person, float], bool] | None` (ставит сессия);
`PersonTracker.update(..., on_stage: list[bool] | None = None)`; `Track.on_stage: bool = True`.

- [ ] Тесты: потерянная цель не узнаётся по месту и по внешности в треке вне области, узнаётся в треке на сцене; `on_stage=None` — прежнее поведение (существующие тесты трекера без правок).
- [ ] Реализация.

### Task 5: Ведомая цель и рамка вне области

**Files:** Modify `src/ptztracker/vision/tracker.py`; Tests `tests/vision/test_tracker_stage.py`; Modify `docs/DECISIONS.md`.

- [ ] Замер: `--replay --mode analysis` записей 19:44 и 20:22 с рабочего стола, область задана вручную по кадру; посчитать переходы цели на людей вне области и путь каждого (`_match_target` / перехват), смещение головы в долях высоты рамки. Цифры — в DECISIONS.
- [ ] Тесты: цель на сцене, кандидат вне области со скачком головы больше `STAGE_STEP` — не берётся (цель «потеряна»); `test_led_target_walks_out_of_area`; цель уже вне области — ведётся как раньше.
- [ ] Реализация `STAGE_STEP`; повтор записей — «после».

### Task 6: Выбор «сам»

**Files:** Modify `src/ptztracker/vision/analyzer.py` (`select_neighbor`), `src/ptztracker/ui/window_input.py` (`_on_mouse_left`); Tests `tests/vision/test_analyzer.py`, `tests/ui/test_mouse_mode.py`.

- [ ] Тесты: сосед и «ближе всех к центру» — среди людей на сцене; на сцене никого — как раньше; `test_click_outside_area_selects`.
- [ ] Реализация.

### Task 7: Запись, повтор, API

**Files:** Modify `src/ptztracker/recorder/` (meta, событие `analysis`, `replay.py`), `src/ptztracker/api/backend.py`; Tests `tests/recorder/`, `tests/api/`.

- [ ] Тесты: `stage_area` в meta; `stage` у треков; повтор распознавания применяет область; `/status` отдаёт `{"set", "on"}`; запроса на изменение нет.
- [ ] Реализация.

### Task 8: Показ на видео

**Files:** Modify `src/ptztracker/ui/preview.py`, `src/ptztracker/ui/theme.py`, `docs/BRANDBOOK.md`; Test `tests/ui/test_preview_stage.py`.

- [ ] Тесты: область в кадре — затемнение и линия по `frame_rect`; край вне кадра — линии нет; вид неизвестен — ничего не рисуется; людям вне области скобки не рисуются; области нет — снимок как раньше.
- [ ] Реализация.

### Task 9: Настройка мышью и пункт меню

**Files:** Modify `src/ptztracker/ui/preview.py`, меню камеры (файл найти по пункту «Диагностика камеры…»), `src/ptztracker/ui/main_window.py`; Test `tests/ui/test_stage_edit.py`.

- [ ] Тесты: пункт «Область сцены…» и три состояния; первый раз — низ на 70 % кадра, бока и верх за кадром; перетаскивание стороны меняет градусы; «Готово» сохраняет в конфиг и передаёт сессии; «Отмена» и Esc возвращают прежнюю; «Выключить область» — `on=False` без стирания; за всю настройку камере не ушло ни одной команды движения и слежение не выключилось; плашка «в области N из M».
- [ ] Реализация. Снимок «после» — в `docs/design/`.

### Task 10: Документы и полный прогон

- [ ] `docs/ARCHITECTURE.md` (трекер, единицы — градусы), `CHANGELOG.md` → `[Не выпущено]`, `docs/BACKLOG.md` (проверка на площадке; что не сделано), `docs/DECISIONS.md`.
- [ ] `ruff check src tests`; быстрый набор (~18 мин) и медленный (~30 мин) — предупредив оператора.
