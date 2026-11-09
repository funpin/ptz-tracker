# Человечное слежение — план реализации

> Выполнение: `superpowers:executing-plans`, одна сессия (задачи регулятора сильно связаны), независимое ревью всей ветки в конце.

**Цель:** камера ведёт докладчика плавно и по центру, держит середину переминания, быстро приходит в себя после остановки, зум и поворот работают одновременно.

**Архитектура:** отдельная оценка человека на ось (`PersonEstimator`: положение, скорость, центр переминания, вес «идёт» 0…1) → цель-смесь → одна гладкая кривая «ошибка → скорость» → единый S-образный разгон. Регулятор остаётся чистой функцией шага.

**Спека:** `docs/superpowers/specs/2026-11-09-human-like-tracking-design.md` (цели приёмки — оттуда).

## Общие ограничения

- Инварианты `AGENTS.md`: стопы не теряются, ручное действие выключает слежение, поток Qt не ждёт.
- Поток `FramingController.step(FramingInput) -> FramingOutput` не меняет сигнатур; `SwingWatch`, модель камеры, `speed_adapt` остаются.
- Ключевые точки — numpy float32: перед арифметикой приводить к `float`.
- Новые зависимости не добавляются. Интерфейс не меняется.
- Изменение порога — строка с цифрами в `docs/DECISIONS.md`.

## Review Focus

1. Камера быстрее таблицы ×2 при новых законах — без раскачки (`test_new_law_stable_with_speed_error`).
2. Человек стоит сбоку от метки и переминается — камера всё-таки приходит к метке (`test_off_center_swayer_gets_centered`).
3. Шаг и сразу обратно (подошёл к экрану на 0,5 с) — не два рывка (`test_short_step_and_back_one_motion`).
4. Цель пропала на ходу и вернулась — вес и центр не дают рывка (`test_reacquire_after_loss_no_jump`).
5. Наезд на ходу при неверной таблице зума ×0,7…×1,5 — без раскачки и потери (`test_zoom_while_walking_with_zoom_table_error`).

---

### Task 1: Стенд — реалистичный человек, общий канал, метрики

**Files:** Create `src/ptztracker/sim/person_motion.py`, `tests/control/metrics.py`, `tests/control/test_tracking_goals.py`; Modify `src/ptztracker/sim/scene.py`, `tests/control/loop.py`.

**Produces:**
- `person_motion.sway(t, amp, seed=0.0) -> float` — сумма синусов 0,31/0,53/1,2 Гц (доли 0,6/0,3/0,1).
- `person_motion.walk_position(t, t0, t1, v, ramp=0.5) -> float` — смещение при трапеции скорости.
- `person_motion.walk_bob(t) -> float` — боковое качание шага, ±1 (умножается на амплитуду).
- `loop.run(..., noise: float = 0.0, seed: int = 0, channel: bool = False)`: `noise` — гауссов шум замера (доли кадра), `channel=True` — команды и запросы #APC/#GZ идут через настоящий `CommandScheduler` с виртуальными часами.
- `metrics`: `reversals(values, eps=0.03)`, `overshoot_after(tr, t_stop, direction)`, `settle_time(tr, t_stop, tol=0.05, anchor=0.5)`, `moving_share(tr, t0, v_eps=0.01)`, `mean_error(tr, t0, t1, anchor=0.5)`, `peak_error(...)`, `jerk_p99(tr)`.
- `SyntheticStage`: переминание на стояниях (± 10 см), сценарий `"shuffle"` (стоит, ±18 см), трапеция скорости ходьбы 0,5 с, боковое качание ±2,5 см и вертикальное ±1,5 см на ходу.

- [ ] Тесты метрик и сцены; `test_tracking_goals.py` — цели приёмки из спеки, по одному тесту на строку таблицы (пока падают).
- [ ] Реализация, базовая линия записана в план.
- [ ] Коммит.

### Task 2: `PersonEstimator`

**Files:** Create `src/ptztracker/control/person_estimator.py`, `tests/control/test_person_estimator.py`.

**Produces:** `PersonEstimator()`, `.reset()`, `.update(t: float, z: float, k: float) -> None` (мировая координата и масштаб), поля в мировых единицах `pos`, `vel`, `center`, `sway` и `walk` (0…1), `.t`.

Вес: `walk → speed_gate · max(excursion_gate, smoothstep(walk, 0,1…0,5))`; центр — фильтр с постоянной от ~2,5 с (`walk`=0) до ~0,15 с (`walk`=1); размах растёт медленно (~1,5 с), спадает ~5 с. Положение — фильтр 1€.

- [ ] Тесты: при переминании ±0,05…±0,12 `walk` < 0,3 почти всегда; ходьба 0,31/с — `walk` > 0,8 не позже чем через 0,6 с; после остановки `walk` < 0,3 не позже 0,4 с; центр держится в ±0,03 от середины; шум 0,004 не поднимает `walk`.
- [ ] Реализация, коммит.

### Task 3: Цель и кривая скорости, единый разгон

**Files:** Create `src/ptztracker/control/motion_law.py`, `tests/control/test_motion_law.py`; Modify `src/ptztracker/control/framing.py`, `effort.py`.

**Produces:** `motion_law.smoothstep(x, a, b)`, `motion_law.speed_for_error(e, dead, soft, gain, v_cap) -> float`: нечётная, гладкая, монотонная: `gain·(√(e²+dead²)−dead)·(1+(e/soft)²)` с мягким насыщением `v_cap·tanh(v/v_cap)`.

`_Axis` использует `PersonEstimator`; цель `(1−walk)·center + walk·прогноз`; убираются `mode`-гейт на движение, `walking`, `EDGE_GAIN`, `SETTLE_V`, `RETURN_*`, `returning`. Предел ускорения плавно растёт с ошибкой (вместо переключателя `CATCH_UP`).

- [ ] Тесты кривой (непрерывность, монотонность, ≈0 у метки), цели приёмки по переминанию, `test_off_center_swayer_gets_centered`, `test_new_law_stable_with_speed_error`.
- [ ] Реализация, коммит.

### Task 4: Остановка, возврат, упреждение

**Files:** Modify `framing.py`, `effort.py`.

Упреждение `FF_GAIN` ≈ 0,85 × `walk`; прогноз ошибки с тормозным путём; ранний догон при уходе.

- [ ] Цели приёмки по остановке и быстрой ходьбе, `test_short_step_and_back_one_motion`, `test_reacquire_after_loss_no_jump`.
- [ ] Реализация, коммит.

### Task 5: Зум на ходу и планировщик

**Files:** Modify `framing.py`, `camera/scheduler.py`, `tests/camera/test_scheduler.py`.

`_zoom`: без запрета на ходу, множитель наезда — плавная функция запаса до края и запаса скорости поворота. Планировщик: когда ждут и поворот, и зум, окна чередуются (зум ждёт не больше одного окна поворота); только `TRACKING`/`TRACKING_ZOOM`.

- [ ] Цели приёмки по наезду, `test_zoom_while_walking_with_zoom_table_error`, тест чередования планировщика.
- [ ] Реализация, коммит.

### Task 6: Коды камеры

**Files:** Modify `camera_model.py` при необходимости, `tests/control/test_camera_model.py`.

- [ ] Метрика смен кода на стенде; если коды прыгают при плавной скорости — гистерезис/ограничение; тест; коммит.

### Task 7: Тесты движения, эпизоды

**Files:** `tests/control/test_framing.py`, `tests/control/test_closed_loop.py`, `tests/control/test_follower_zoom_model.py`, `tests/recorder/*`, `tests/data/episodes/sim_walk_30s.zip`.

- [ ] Быстрый набор, полный прогон: старые ожидания поведения заменить новыми; безопасность не ослаблять. Перезаписать `sim_walk_30s.zip`. Коммит.

### Task 8: Документы и ревью

- [ ] `docs/DECISIONS.md`, `docs/ARCHITECTURE.md`, `CHANGELOG.md`, `docs/LIVE_TEST_PROMPT.md` (что проверить), версия `1.4.0` — только при выпуске.
- [ ] Независимое ревью, правки, полный прогон, коммит.
