# Стоп камеры не теряется — план исправления

> **Для агента:** выполнять задачами через superpowers:subagent-driven-development или superpowers:executing-plans. Шаги — чекбоксы `- [ ]`.

**Цель:** ни одна команда стопа (`#PTS5050`, `#Z50`) — ручная, при выключении слежения или аварийная — не теряется, даже если её отправили, пока предыдущая команда движения ещё «в пути».

**Устройство:** правка в `CommandScheduler` (сравнение «уже отправлено» учитывает команду в пути и неизвестный исход; стопы не отбрасываются по счётчику ошибок; пауза после обрывов не мешает аварийным стопам) и защита аварийного пути в `CameraSession`, `CameraGrid`, `MainWindow` от сбоя шагов перед стопом.

**Технологии:** Python 3.12–3.13, pytest, pytest-qt, симулятор `sim/`.

**Основание:** анализ проекта 2026-11-07 (этот план — сам себе спека; проблема ниже воспроизведена).

## Что сломано (воспроизведено 2026-11-07)

Планировщик не шлёт команду, если она равна последней **подтверждённой** камерой в этой ячейке (`only_if_changed`, [scheduler.py:140](../../../src/ptztracker/camera/scheduler.py)). Команду, которая сейчас в сети (`_inflight`), он не учитывает:

1. Камера стоит (`last_sent["pt"] = #PTS5050`). Оператор нажал стрелку → `#PTS6050` ушёл в сеть. Отпустил до ответа камеры → `#PTS5050` равен `last_sent` → не ставится. Ответ на `#PTS6050` приходит — камера едет, пока её не тронут. Итог прогона: `['#PTS5050', '#PTS6050']`, очередь пуста.
2. То же с Esc: Esc ставит аварийные стопы, отпускание клавиши шлёт `#PTS5050` → он **выбрасывает** ожидающий аварийный стоп поворота. Итог: `['#PTS5050', '#PTS6050', '#Z50', '#F50']` — поворот не остановлен.
3. Команда не дошла по таймауту (камера могла её выполнить), `last_sent` остаётся старым стопом → следующий стоп «уже отправлен» и выкидывает повтор движения из ячейки.

Рядом (по чтению кода):
4. Стопы `Follower.disable` и ручные стопы — `Priority.MANUAL`: после 3 ошибок (не er2) отбрасываются ([scheduler.py:376](../../../src/ptztracker/camera/scheduler.py)). Инвариант «стопы никогда не теряются» нарушен.
5. Esc сбрасывает паузу после обрывов (`_backoff_until = 0`), а команда в пути, упав третьей подряд, ставит паузу 1 с заново — аварийные стопы ждут.
6. Шаги перед стопом без защиты: `MainWindow.emergency_stop` (`exit_mouse_mode`, `revoke_all`, `_pad_stop_motion`, `release_all_held`), `CameraGrid.emergency_stop_all` (`gate.revoke`, `mixer.clear_all` по всем клеткам), `CameraSession.emergency_stop` (`follower.note_manual`, `follower.disable`). Исключение в любом — стопы не уходят (в окне и сетке — ни у одной камеры).

## Общие ограничения

- Инварианты AGENTS.md не ослабляются: слежение выключается **до** стопа; `queries_only` (`--lab record`) по-прежнему не шлёт ни движений, ни стопов; стоп фокуса с er3 по-прежнему не повторяется (`test_focus_stop_er3_not_retried_but_pan_zoom_stops_still_are`).
- Повтор того же кода слежения каждый такт по-прежнему не шлётся второй раз (нагрузка на камеру ~7 команд/с).
- Порядок «сначала поворот» и окно зума (`ZOOM_WINDOW_AFTER_S`) не меняются.
- Каждое изменение — строка в `CHANGELOG.md` → `[Не выпущено]`, языком оператора. Версию и тег не трогать (релиз — с согласия пользователя).

## На что смотреть ревью

1. Слежение шлёт один и тот же код, пока такой же в пути, — второй раз не уходит (задача 1, `test_same_cmd_as_inflight_not_resent`).
2. Зум ведёт себя так же, как поворот (задача 1, параметризация по `pt`/`zoom`).
3. Камера в ожидании (`standby`) при Esc — ветка `drop_pending` не меняется (существующие тесты `test_standby_*` в `tests/camera/test_session.py`).
4. Связь пропала совсем: аварийные стопы повторяются, но не чаще `min_interval` и таймаута клиента — без паузы 1 с они не забивают процессор (задача 2, `test_esc_during_outage_not_delayed_by_backoff` проверяет только отсутствие паузы; частоту держит `_wait_s`).
5. Ручная команда в ту же ячейку после Esc (оператор снова поехал) по-прежнему заменяет аварийный стоп — это его действие (`test_emergency_stop_preempts_and_clears` и существующее поведение «ручная команда в ту же ячейку их заменяет», ARCHITECTURE, инвариант 1).

---

### Задача 1: повтор сравнивается с командой в пути; ожидающий аварийный стоп не выкидывается

**Файлы:**
- Изменить: `src/ptztracker/camera/scheduler.py` (`submit`, новый `_last_known`)
- Тесты: `tests/camera/test_scheduler.py`
- Документы: `CHANGELOG.md`, `docs/ARCHITECTURE.md` (инвариант 2)

**Интерфейсы:**
- Даёт: `CommandScheduler._last_known(key: str) -> str | None` — код в пути по этой ячейке, если он есть, иначе `last_sent[key]`; зовётся под `self._cond`.
- Даёт (для тестов задач 1–2): в `tests/camera/test_scheduler.py` у `Recorder` поле `during: dict[str, Callable[[], None]]` — вызывается в `__call__` сразу после `calls.append(cmd)` и до `fail_next`/ответа, когда отправляется команда-ключ (имитирует действия оператора, пока команда в сети; `send` вызывается вне замка планировщика, поэтому `submit`/`emergency_stop` из него безопасны).

- [ ] **Шаг 1: падающие тесты**

```python
@pytest.mark.parametrize("key, move, stop", [("pt", "#PTS6050", "#PTS5050"), ("zoom", "#Z70", "#Z50")])
def test_release_while_move_in_flight_still_stops(env, key, move, stop):
    s, clock, send = env
    s.submit(key, stop, Priority.MANUAL); s.pump(); clock.t += 1          # камера стоит
    send.during[move] = lambda: s.submit(key, stop, Priority.MANUAL)      # отпустил, пока движение в сети
    s.submit(key, move, Priority.MANUAL); s.pump(); clock.t += 1
    assert s.pump() == stop
    assert send.ok[-1] == stop


def test_esc_then_release_while_move_in_flight_keeps_emergency_stop(env):
    s, clock, send = env
    s.submit("pt", "#PTS5050", Priority.MANUAL); s.pump(); clock.t += 1
    def esc_then_release():
        s.emergency_stop()
        s.submit("pt", "#PTS5050", Priority.MANUAL)
    send.during["#PTS6050"] = esc_then_release
    s.submit("pt", "#PTS6050", Priority.MANUAL); s.pump()
    for _ in range(4):
        clock.t += 0.131
        s.pump()
    assert send.ok.index("#PTS5050", 1) > send.ok.index("#PTS6050")
    assert {"#Z50", "#F50"} <= set(send.ok)


def test_same_cmd_as_inflight_not_resent(env):
    s, clock, send = env
    send.during["#PTS6050"] = lambda: s.submit("pt", "#PTS6050", Priority.TRACKING)
    s.submit("pt", "#PTS6050", Priority.TRACKING); s.pump(); clock.t += 1
    assert s.pump() is None
    assert send.calls == ["#PTS6050"]
```

- [ ] **Шаг 2: убедиться, что падают**

Run: `.venv/bin/pytest -q tests/camera/test_scheduler.py -k "in_flight or inflight"`
Ожидается: 4 FAIL. Три — потерянный стоп (`assert None == '#PTS5050'` и т.п.). `test_same_cmd_as_inflight_not_resent` сейчас тоже падает: тот же код, поставленный, пока он в сети, уходит второй раз. После правки он не повторяется — у слежения станет чуть меньше лишних команд; если команда в пути упадёт, `_on_failure` и так вернёт её в ячейку.

- [ ] **Шаг 3: правка `submit`**

Сравнивать новую команду не с `last_sent.get(key)`, а с `_last_known(key)`. Если в ячейке уже ждёт `Priority.EMERGENCY` с тем же `cmd` — ничего не менять (не выкидывать и не понижать приоритет). Ветка «вернулись к уже отправленному — отменить ожидающую» остаётся, но срабатывает только когда `cmd == _last_known(key)`.

- [ ] **Шаг 4: тесты проходят, остальное не сломано**

Run: `.venv/bin/pytest -q tests/camera/test_scheduler.py tests/camera/test_session.py`
Ожидается: все PASS (в т.ч. `test_return_to_last_sent_cancels_pending`, `test_only_if_changed`).

- [ ] **Шаг 5: документы**

- `docs/ARCHITECTURE.md`, инвариант 2: дописать «Повтор того же кода сравнивается и с командой, которая сейчас в сети: стоп, нажатый до ответа камеры, уходит всегда».
- `CHANGELOG.md` → `[Не выпущено]` → `### Исправлено`: «**Камера могла продолжить ехать после того, как отпустили стрелку или стик** — если отпустить быстрее, чем камера ответила на команду движения (особенно по медленной сети или Wi-Fi). То же с Esc: поворот мог не остановиться. Теперь стоп уходит всегда.»

- [ ] **Шаг 6: коммит**

```bash
git add src/ptztracker/camera/scheduler.py tests/camera/test_scheduler.py CHANGELOG.md docs/ARCHITECTURE.md
git commit -m "fix(camera): stop sent while a move is in flight is never deduplicated away"
```

---

### Задача 2: неизвестный исход, стопы без счётчика ошибок, пауза не держит аварийный стоп

**Файлы:**
- Изменить: `src/ptztracker/camera/scheduler.py` (`_on_failure`; константа `_NEVER_DROP = frozenset({STOP_PT, STOP_ZOOM})`)
- Тесты: `tests/camera/test_scheduler.py`

**Интерфейсы:**
- Использует: `Recorder.during` из задачи 1.

- [ ] **Шаг 1: падающие тесты**

```python
def test_timeout_forgets_last_sent_so_stop_goes_out(env):
    s, clock, send = env
    s.submit("pt", "#PTS5050", Priority.MANUAL); s.pump(); clock.t += 1
    send.fail_next = [CameraUnreachable("timeout")]          # камера могла выполнить, ответ потерян
    s.submit("pt", "#PTS6050", Priority.MANUAL); s.pump(); clock.t += 1
    s.submit("pt", "#PTS5050", Priority.MANUAL)
    assert s.pump() == "#PTS5050"


@pytest.mark.parametrize("stop", ["#PTS5050", "#Z50"])
def test_manual_stop_never_dropped_on_errors(env, stop):
    s, clock, send = env
    send.fail_next = [CameraError("er1", "eR1:X")] * 5
    s.submit("pt" if stop.startswith("#PTS") else "zoom", stop, Priority.MANUAL, only_if_changed=False)
    for _ in range(8):
        s.pump(); clock.t += 0.131
    assert stop in send.ok


def test_esc_during_outage_not_delayed_by_backoff(env):
    s, clock, send = env
    send.fail_next = [CameraUnreachable("x")] * 3
    send.during["#PTS6050"] = lambda: s.emergency_stop() if len(send.calls) == 3 else None
    s.submit("pt", "#PTS6050", Priority.MANUAL)
    for _ in range(3):
        s.pump(); clock.t += 0.131
    assert s.pump() == "#PTS5050"                            # без паузы 1 с
```

`test_persistent_camera_error_dropped_after_3` (команда `#X`) остаётся как есть: обычные команды по-прежнему отбрасываются после 3 ошибок.

- [ ] **Шаг 2: убедиться, что падают**

Run: `.venv/bin/pytest -q tests/camera/test_scheduler.py -k "forgets or never_dropped_on_errors or outage"`
Ожидается: 4 FAIL.

- [ ] **Шаг 3: правка `_on_failure`**

- При любой ошибке команды с ключом — `self._st.last_sent.pop(item.key, None)` (исход неизвестен; лишний повтор безвреден, потерянный стоп — нет).
- Счётчик `item.errors` не растёт, если `item.priority == Priority.EMERGENCY` **или** `item.cmd in _NEVER_DROP`. Исключение для `STOP_FOCUS` + er3 остаётся выше по коду без изменений.
- Пауза `_backoff_until` после `UNREACHABLE_BACKOFF_AFTER` обрывов не ставится, если в `slots` есть элемент `Priority.EMERGENCY`.

- [ ] **Шаг 4: тесты проходят**

Run: `.venv/bin/pytest -q tests/camera/`
Ожидается: все PASS (в т.ч. `test_backoff_after_three_unreachable`, `test_focus_stop_er3_not_retried_but_pan_zoom_stops_still_are`).

- [ ] **Шаг 5: коммит**

```bash
git add src/ptztracker/camera/scheduler.py tests/camera/test_scheduler.py
git commit -m "fix(camera): stops are never dropped by error count; unknown outcome forgets last_sent; backoff never delays emergency stops"
```

---

### Задача 3: сбой шага перед стопом не отменяет стоп

**Файлы:**
- Изменить: `src/ptztracker/camera/session.py:841` (`emergency_stop`), `src/ptztracker/multicam/grid.py:108` (`emergency_stop_all`), `src/ptztracker/ui/main_window.py:2506` (`emergency_stop`)
- Тесты: `tests/camera/test_session.py`, `tests/multicam/test_grid.py`, `tests/ui/test_main_window.py`
- Документы: `docs/BACKLOG.md`

**Интерфейсы:** новых нет. Подход везде один: каждый подготовительный шаг — в своём `try/except Exception: log.exception(...)`, затем стоп. Порядок «выключить слежение → стоп» сохраняется.

- [ ] **Шаг 1: падающие тесты**

```python
# tests/camera/test_session.py
def test_emergency_stop_sent_even_if_follower_raises():
    s = CameraSession(sim_cfg(), analysis=False)
    s.start()
    try:
        s.set_pan_tilt_velocity(1, 0)
        assert _wait(lambda: any(c.startswith("#PTS") and c != "#PTS5050" for c in s.sim.received))
        class Boom:
            enabled = True
            def note_manual(self): raise RuntimeError("boom")
            def disable(self, reason): raise RuntimeError("boom")
        s.follower = Boom()
        s.emergency_stop()
        assert _wait(lambda: [c for c in s.sim.received if c.startswith("#PTS")][-1] == "#PTS5050")
    finally:
        s.follower = None
        s.stop()

# tests/multicam/test_grid.py
def test_emergency_stop_all_survives_failing_gate(grid, monkeypatch):
    a = grid.open(0, sim_cfg(0), False)
    b = grid.open(1, sim_cfg(1), False)
    for c in (a, b):
        c.mixer.set("pad", pan=1.0)
    assert wait_for(lambda: pts(a) and pts(b))
    monkeypatch.setattr(a.gate, "revoke", boom)              # boom(*a, **k): raise RuntimeError("boom") — в тестовом модуле
    grid.emergency_stop_all()
    assert wait_for(lambda: pts(a)[-1] == "#PTS5050" and pts(b)[-1] == "#PTS5050")

# tests/ui/test_main_window.py
def test_escape_stops_even_if_pre_step_fails(qtbot, tmp_path, monkeypatch):
    w = make(qtbot, tmp_path)
    sim = w.session.sim
    w.session.set_pan_tilt_velocity(1, 0)
    qtbot.wait(300)
    monkeypatch.setattr(w, "exit_mouse_mode", boom)
    w.emergency_stop()                                        # не бросает
    qtbot.wait(400)
    assert [c for c in sim.received if c.startswith("#PTS")][-1] == "#PTS5050"
    w.close()
```

Если `mixer.set` с источником `"pad"` в сетке шлёт команды не сразу — взять тот же приём, что в `test_open_two_sims_independent`.

- [ ] **Шаг 2: убедиться, что падают**

Run: `.venv/bin/pytest -q tests/camera/test_session.py::test_emergency_stop_sent_even_if_follower_raises tests/multicam/test_grid.py::test_emergency_stop_all_survives_failing_gate tests/ui/test_main_window.py::test_escape_stops_even_if_pre_step_fails`
Ожидается: 3 FAIL (`RuntimeError: boom`).

- [ ] **Шаг 3: правка трёх методов**

- `CameraSession.emergency_stop`: `note_manual` и `disable` — в `try/except`, с `log.exception("…")`; ветка `standby/switching` и `scheduler.emergency_stop()` — как были.
- `CameraGrid.emergency_stop_all`: `gate.revoke()` и `mixer.clear_all()` каждой клетки — в `try/except` по отдельности; затем `_emergency_one` по всем, как сейчас.
- `MainWindow.emergency_stop`: `exit_mouse_mode`, `agent_panel.revoke_all`, `_pad_stop_motion`, `release_all_held` — по одному в `try/except` с `log.exception`; затем `grid.emergency_stop_all()`; `camera_menu.close_menu()` — после стопа, тоже в `try/except`.

- [ ] **Шаг 4: тесты проходят**

Run: `.venv/bin/pytest -q tests/camera tests/multicam tests/ui/test_main_window.py tests/ui/test_multi_camera_window.py`
Ожидается: все PASS.

- [ ] **Шаг 5: BACKLOG — что осталось за рамками**

В `docs/BACKLOG.md` → «Отложенные мелочи» → «Камера и сеть» добавить:
- Аварийный стоп ждёт команду в пути: таймаут клиента 1 с на фазу, с Digest — два запроса; при зависшей камере стоп уходит через несколько секунд. Нужен короткий отдельный путь для стопов.
- При переоткрытии той же камеры в клетке стопы старой сессии (фон, до 1,5 с) могут прийти после первых команд новой.
- Аварийный стоп, который камера отвергает всегда (не er3 фокуса), повторяется бесконечно и держит очередь — так задумано (`test_emergency_never_dropped_even_on_er1`); на HE40/42 не встречалось.
- Запросы-ячейки `Priority.QUERY` (пульс, `#APC`/`#GZ` для окна, gain/WB) не стареют, как `query()` (`QUERY_MAX_WAIT_S`): при слежении могут ждать долго.

- [ ] **Шаг 6: коммит**

```bash
git add src/ptztracker/camera/session.py src/ptztracker/multicam/grid.py src/ptztracker/ui/main_window.py tests/camera/test_session.py tests/multicam/test_grid.py tests/ui/test_main_window.py docs/BACKLOG.md
git commit -m "fix: a failing pre-step never cancels the emergency stop (window, grid, session)"
```

---

### Завершение

- [ ] `.venv/bin/ruff check src tests` — чисто.
- [ ] `.venv/bin/pytest -q` — полный набор (~9 мин; медленные тесты под нагрузкой иногда нестабильны — см. BACKLOG, повторить упавший отдельно).
- [ ] Независимое ревью всей ветки (Sonnet), особенно «На что смотреть ревью».
- [ ] Пользователю: что изменилось простыми словами; на площадке проверить быстрые касания стрелок и стика по Wi-Fi — камера останавливается каждый раз.
