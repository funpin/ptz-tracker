# Videohub вслед за выбранной камерой — план реализации

> Выполнение: `superpowers:executing-plans`, одна сессия, TDD по задачам, независимое ревью изменений в конце. Ничего не коммитить без согласия оператора: в рабочей копии чужие незакоммиченные правки.

**Цель:** смена выбранной камеры в PTZ Tracker подаёт на заданный выход Blackmagic Videohub вход этой камеры; хаб не мешает управлению камерами.

**Спека:** `docs/superpowers/specs/2026-11-10-videohub-follow-design.md`. **Макет:** `docs/design/mockups/videohub.html`.

## Общие ограничения

- Поток Qt не ждёт сеть: клиент — фоновый поток, окно читает снимок по таймеру (образец `tally/atem.py`).
- Живой хаб не трогаем: только фальшивый хаб на 127.0.0.1.
- Нумерация с 1 везде, кроме текста протокола.
- Новых зависимостей нет (`socket`, `threading`).
- В общих файлах (`main_window.py`, `config.py`, `window_status.py`, `CHANGELOG.md`, `DECISIONS.md`, `ARCHITECTURE.md`, `BACKLOG.md`) — только свои строки.

## Review Focus

1. При запуске окна и после восстановления связи хаб не получает ни одной маршрутизации (`test_no_route_on_startup`, `test_no_route_on_reconnect`).
2. Недоступный хаб не задерживает `select_slot` и стопы (`test_unreachable_hub_does_not_block`).
3. Команда при обрыве не «догоняет» позже (`test_route_while_offline_is_dropped`).
4. Чужая блокировка `L` — команды нет (`test_locked_output_skipped`).

---

### Task 1: Протокол без сети

**Files:** Create `src/ptztracker/videohub/__init__.py`, `protocol.py`, `tests/videohub/test_protocol.py`.

**Produces:** `BlockReader().feed(bytes) -> list[Block]` (`Block(header: str, lines: tuple[str, ...])`); `HubState` (неизменяемый) и `apply_block(state, block) -> HubState`; `route_command(output, input) -> bytes` (1-based → 0-based); `PING`; `MAX_PORTS = 288`; `hub_input_of(cam)`, `hub_output_of(cfg)`.

- [ ] Тесты: полный дамп Smart Videohub 20×20 → модель, 20/20, подписи, маршруты и блокировки с 1; кусками по 1 байту; `\r\n`; неизвестный блок и `END PRELUDE:` пропускаются; `Device present: false`; обновление одной строки маршрута; `ACK`/`NAK` как блоки без строк; `route_command(5, 3) == b"VIDEO OUTPUT ROUTING:\n4 2\n\n"`; мусорные строки не роняют разбор.
- [ ] Реализация.

### Task 2: Фальшивый хаб для тестов

**Files:** Create `tests/videohub/__init__.py`, `tests/videohub/fake_hub.py`, `tests/videohub/test_fake_hub.py`.

**Produces:** `FakeHub(inputs=20, outputs=20, port=0)`: TCP-сервер на 127.0.0.1 в потоке; при подключении шлёт дамп как настоящий; на маршрутизацию — `ACK` + обновление всем клиентам; индекс вне диапазона — `NAK`; выход `L` — `ACK` без изменения; `PING` — `ACK`. Ручки: `routes_received`, `routing`, `set_lock(out, "L")`, `nak_next`, `silent` (принимает, не отвечает), `drop_clients()`, `stop()/restart()` на том же порту. Байты собраны вручную, независимо от `protocol.py`.

- [ ] Короткий тест на голом сокете: дамп приходит, маршрут меняется.

### Task 3: Клиент

**Files:** Create `src/ptztracker/videohub/client.py`, `tests/videohub/test_client.py`.

**Produces:** `VideohubClient(host, port=9990, on_change=None)`, `start()`, `stop()` (≤ 0,5 с), `snapshot() -> HubSnapshot`, `route(output, input) -> bool`.

- [ ] Тесты: на связи и снимок с фальшивого хаба; `route` меняет маршрут; `route` без связи → `False`, < 5 мс, позже не отправлено (`test_route_while_offline_is_dropped`); подряд 1→2→1→2 → итог 2; хаб перезапущен → снова на связи, маршрутов при этом нет (`test_no_route_on_reconnect`); хаб молчит → через ~6 с офлайн и переподключение (`slow`); NAK → `last_error == "nak"`, следующий успешный сбрасывает; адрес, который не отвечает, и закрытый порт — `start/stop/route` быстрые; пустой адрес — поток не стартует.
- [ ] Реализация.

### Task 4: Решение «слать или нет»

**Files:** Create `src/ptztracker/videohub/follow.py`, `tests/videohub/test_follow.py`.

**Produces:** `HubFollower.selected(camera_id, hub_input, output, enabled, snap) -> FollowResult(action: "route"|"skip", reason: str)`; причины: `first`, `same`, `off`, `no_output`, `no_input`, `offline`, `locked`.

- [ ] Тесты на каждую причину; первый вызов только запоминает; `None` (пустая клетка) запоминается без команды.
- [ ] Реализация.

### Task 5: Конфиг

**Files:** Modify `src/ptztracker/config.py`, `tests/test_config.py` (или существующий тест конфига).

- [ ] Тесты: поля по умолчанию; сохранение/чтение; мусор (`"abc"`, `-1`, `True`, `999`) → 0/`""`/`True`; старый конфиг без полей читается.
- [ ] `AppConfig.videohub_ip/videohub_output/videohub_follow`, `CameraConfig.videohub_input`, `_fix_videohub_settings`.

### Task 6: Окно — привязка к выбору камеры

**Files:** Modify `src/ptztracker/ui/main_window.py` (параметр `videohub_port`, создание/остановка клиента), `src/ptztracker/ui/window_cells.py` (вызов `_hub_follow()` в `_select_effects`), Create `src/ptztracker/ui/window_videohub.py` (миксин: `_restart_videohub`, `_hub_follow`, `_sync_hub`, `_videohub_menu_status`, `_edit_videohub`); `tests/ui/test_videohub_window.py`.

- [ ] Тесты (окно `--sim`, две камеры, фальшивый хаб): клик/клавиша/Create главного геймпада по клетке 2 → выход = вход камеры 2; `test_no_route_on_startup`; камера без входа — без команды; переключение выключено — без команды; `test_locked_output_skipped`; `test_unreachable_hub_does_not_block` (select_slot < 50 мс, стоп камеры в очереди); закрытие окна быстрое.
- [ ] Реализация.

### Task 7: Значок, меню, окно «Videohub»

**Files:** Create `src/ptztracker/ui/videohub_dialog.py`, `src/ptztracker/ui/icons/hub.svg`; Modify `src/ptztracker/ui/state.py` (`hub_pill`), `main_window.py` (значок, пункт меню); `tests/ui/test_videohub_dialog.py`, тест `hub_pill` в `tests/ui/test_state*.py`.

- [ ] Тесты `hub_pill`: не настроен → нет значка; нет связи → fault «Хаб: нет связи»; занят → warn; NAK → warn «хаб отказал»; на связи → ok «Хаб: вых. 5 ← Сцена» (имя камеры по входу, иначе «вход 7»).
- [ ] Тесты окна: поля из конфига; списки с подписями хаба при связи и номерами без связи; «не переключать» → 0; «Выключить хаб» → пустой адрес; неверный адрес — ошибка, «Сохранить» не закрывает.
- [ ] Реализация по макету, значения из `theme.py`; снимок «после» в `docs/design/videohub-after.png`.

### Task 8: Документы и проверка

- [ ] `CHANGELOG.md` → «Не выпущено»; `docs/DECISIONS.md` (не трогать хаб при запуске/обрыве, без очереди, таймауты 1/2/3/6 с); `docs/ARCHITECTURE.md` (пакет `videohub/`); `docs/BACKLOG.md` (несколько выходов, API агента, чёрный ящик, проверка на живом хабе).
- [ ] `.venv/bin/pytest -q -m "not slow"`, тесты `tests/videohub` целиком, `.venv/bin/ruff check src tests`.
- [ ] Независимое ревью (Sonnet) всех своих изменений.
- [ ] Сообщение оператору: что настроить и как проверить на площадке.
