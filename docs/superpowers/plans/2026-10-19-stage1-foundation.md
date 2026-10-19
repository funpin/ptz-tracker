# PTZ Tracker — Этап 1: каркас, связь, ручное управление, симулятор, диагностика

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Windows-программа (.exe), которая хранит список камер AW-HE42, показывает RTSP-превью и даёт полное ручное управление. Есть встроенная «Диагностика камеры», которая на площадке собирает отчёт о реальном поведении камеры. Есть симулятор PTZ-камеры, на котором всё это работает без живой камеры.

**Architecture:** Python-пакет `ptztracker` (src-layout). На каждую камеру создаётся `CameraSession`: HTTP-клиент Panasonic → `CommandScheduler` (интервал 130 мс, приоритеты, склейка) + `FrameSource` (RTSP или симулятор). UI на PySide6 работает только через `CameraSession`. Диагностика — отдельный пакет, который пользуется теми же клиентом, планировщиком и источником кадров. Тот же код без изменений проверяется против симулятора.

**Tech Stack:** Python 3.12, PySide6, PyAV, numpy, opencv-contrib-python-headless, httpx, keyring, platformdirs, pytest, pytest-qt, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-10-19-ptz-tracker-design.md` (§3, §5, §6, §7, §9 «Ручной перехват/Аварийный стоп», §10 ручная часть, §11, §12, §13 этап 1)

## Global Constraints

- Целевая ОС Windows 10 22H2; разработка и все тесты на macOS. Никаких Windows-only импортов вне `if sys.platform == "win32"`.
- Python 3.12. CUDA не требуется (ни на этом этапе, ни потом).
- Камера по умолчанию: имя «Камера 1», IP `10.0.0.12`. Камер может быть несколько, IP вводится вручную и запоминается.
- Формат команд: `http://<IP>/cgi-bin/aw_ptz?cmd=%23<CMD>&res=1`; команды камеры (без `#`) идут в `aw_cam`. Keep-Alive не использовать (`Connection: close`).
- Минимальный интервал между командами одной камере по умолчанию **0.130 с**, параметр настраиваемый.
- Команды, меняющие состояние, отправляются только при изменении значения.
- `#PTS`: коды 01–99, 50 = стоп; pan <50 влево, >50 вправо; tilt <50 вниз, >50 вверх. `#Z`: 01–99, 50 = стоп, <50 к широкому углу, >50 к теле.
- Интерфейс полностью на русском языке.
- Конфиг: `%APPDATA%\PTZTracker\config.json` (`platformdirs.user_config_dir("PTZTracker", appauthor=False, roaming=True)`). Пароли только в `keyring`, не в JSON.
- Пути пользователя могут содержать кириллицу (`C:\Users\Иван\…`).

## Review Focus

1. **Камера недоступна** (неверный IP, выключена, другая подсеть). UI не зависает, в статусе «нет связи», планировщик продолжает работать и восстанавливается сам. Тест в Task 3 и Task 4.
2. **RTSP-поток пропал или адрес неверный.** Превью показывает «Нет видео», источник переподключается, исключения не всплывают. Тест в Task 6.
3. **Камера остаётся в движении после отпускания управления, потери фокуса окна или закрытия программы.** Должен уйти `#PTS5050`/`#Z50`. Тест в Task 7 и Task 8.
4. **Повреждённый конфиг или неверный IP в диалоге.** Программа стартует с настройками по умолчанию, битый файл сохраняется как `.bak`, диалог не принимает неверный IP. Тест в Task 1 и Task 8.
5. **Камера отвечает ошибкой `er…`** (занята командами пульта или слишком частые команды). Команда не теряется молча: она повторяется на следующем окне, очередь не стоит. Тест в Task 4.

---

## Структура файлов

```
pyproject.toml
README.md                          — на русском: запуск, сборка, как прогнать диагностику
build.bat                          — сборка .exe на Windows
packaging/ptztracker.spec          — PyInstaller (onedir)
.github/workflows/windows-build.yml
src/ptztracker/
  __init__.py, __main__.py         — точка входа (GUI | --diagnose | --sim)
  logging_setup.py
  config.py                        — CameraConfig, AppConfig, ConfigStore, secrets
  camera/protocol.py               — построение и разбор команд Panasonic
  camera/client.py                 — HTTP-клиент
  camera/scheduler.py              — очередь команд
  camera/speed.py                  — скорость (-1..1) → код 01–99
  camera/session.py                — CameraSession
  video/frames.py                  — Frame, SourceStats, FrameSource (Protocol)
  video/rtsp.py                    — RtspSource, rtsp_candidates()
  sim/ptz_model.py                 — SimPtz (физика + обработка команд)
  sim/http_server.py               — SimHttpServer
  sim/scene.py                     — SyntheticStage, VideoScene
  sim/frame_source.py              — SimFrameSource
  diagnostics/motion.py            — сдвиг кадра, ожидание движения
  diagnostics/notify.py            — NotificationListener
  diagnostics/checks.py            — проверки
  diagnostics/runner.py            — прогон и архив отчёта
  ui/main_window.py, ui/preview.py, ui/camera_dialog.py, ui/manual_controls.py,
  ui/joystick.py, ui/diagnostics_dialog.py
tests/…                            — зеркально модулям
```

---

### Task 1: Каркас проекта и хранение настроек

**Files:**
- Create: `pyproject.toml`, `src/ptztracker/__init__.py`, `src/ptztracker/__main__.py` (заглушка `main()` → печатает версию), `src/ptztracker/logging_setup.py`, `src/ptztracker/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `@dataclass CameraConfig(id: str, name: str, ip: str, kind: Literal["panasonic","sim"]="panasonic", username: str|None=None, rtsp_url: str|None=None, min_interval: float=0.130)`
  - `@dataclass AppConfig(cameras: list[CameraConfig], last_camera_id: str|None=None, sim_scene_path: str|None=None)`
  - `default_config() -> AppConfig`: одна камера `name="Камера 1", ip="10.0.0.12"`.
  - `ConfigStore(path: Path)`: `.load() -> AppConfig`, `.save(cfg: AppConfig) -> None`; `default_config_path() -> Path`.
  - `is_valid_ip(s: str) -> bool` (IPv4 или hostname; `"sim"` допустим только при `kind="sim"`).
  - `get_password(camera_id: str) -> str|None`, `set_password(camera_id: str, pw: str|None) -> None` (обёртка над `keyring`, сервис `"PTZTracker"`; при ошибке backend возвращает `None`, логирует).
  - `setup_logging() -> Path`: ротация 5 × 1 МБ в `platformdirs.user_log_dir("PTZTracker", appauthor=False)`.

- [ ] **Step 1: `pyproject.toml`.** Пакет `ptztracker`, `requires-python = ">=3.12,<3.13"`, src-layout, зависимости: `PySide6>=6.7`, `av>=12`, `numpy>=1.26`, `opencv-contrib-python-headless>=4.9`, `httpx>=0.27`, `keyring>=25`, `platformdirs>=4`. Extras `dev = [pytest>=8, pytest-qt>=4.4]`, `build = [pyinstaller>=6.6]`. Script `ptztracker = "ptztracker.__main__:main"`. Создать venv: `python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"`. Проверка: `.venv/bin/python -m ptztracker` печатает версию.

- [ ] **Step 2: Write failing tests** `tests/test_config.py`:
```python
def test_missing_file_gives_default(tmp_path):
    cfg = ConfigStore(tmp_path / "config.json").load()
    assert [c.ip for c in cfg.cameras] == ["10.0.0.12"]
    assert cfg.cameras[0].name == "Камера 1"

def test_roundtrip_cyrillic_path(tmp_path):
    p = tmp_path / "Пользователь" / "config.json"
    store = ConfigStore(p)
    cfg = default_config()
    cfg.cameras.append(CameraConfig(id="b", name="Сцена-2", ip="10.0.0.13"))
    store.save(cfg)
    assert store.load() == cfg

def test_corrupt_file_backed_up_and_defaults(tmp_path):
    p = tmp_path / "config.json"; p.write_text("{not json", encoding="utf-8")
    cfg = ConfigStore(p).load()
    assert cfg == default_config()
    assert (tmp_path / "config.json.bak").read_text(encoding="utf-8") == "{not json"

def test_save_is_atomic(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path / "config.json"); store.save(default_config())
    before = (tmp_path / "config.json").read_text(encoding="utf-8")
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    cfg = default_config(); cfg.cameras[0].name = "Другое"
    with pytest.raises(OSError): store.save(cfg)
    assert (tmp_path / "config.json").read_text(encoding="utf-8") == before

def test_password_not_in_json(tmp_path):
    cfg = default_config(); ConfigStore(tmp_path/"c.json").save(cfg)
    assert "password" not in (tmp_path/"c.json").read_text(encoding="utf-8")

@pytest.mark.parametrize("s,ok", [("10.0.0.12",True),("cam-1.local",True),("10.0.0",False),("999.1.1.1",False),("",False),("10.0.0.12 ",False)])
def test_is_valid_ip(s, ok): assert is_valid_ip(s) is ok
```
`default_config()` выдаёт детерминированный `id` (`"cam-1"`), чтобы сравнение работало.

- [ ] **Step 3: Run** `.venv/bin/pytest tests/test_config.py -v`. Ожидается FAIL (ImportError).
- [ ] **Step 4: Implement** `config.py` и `logging_setup.py`. JSON в UTF-8, `ensure_ascii=False`. Запись через временный файл в той же папке и `os.replace`. Неизвестные поля игнорировать, отсутствующие брать по умолчанию.
- [ ] **Step 5: Run** тесты: PASS.
- [ ] **Step 6: Commit** `feat: project skeleton and config store`

---

### Task 2: Протокол Panasonic — построение и разбор команд

**Files:**
- Create: `src/ptztracker/camera/__init__.py`, `src/ptztracker/camera/protocol.py`
- Test: `tests/camera/test_protocol.py`

**Interfaces:**
- Produces (все функции чистые; неверный аргумент → `ValueError`):
  - `pan_tilt_speed(pan: int, tilt: int) -> str` → `"#PTS%02d%02d"` (1..99)
  - `zoom_speed(code: int) -> str` → `"#Z%02d"`
  - `zoom_abs(pos: int) -> str` → `"#AXZ%03X"` (0x555..0xFFF)
  - `pan_tilt_abs(pan: int, tilt: int) -> str` → `"#APC%04X%04X"` (0..0xFFFF)
  - `focus_auto(on: bool) -> str` → `"#D11"`/`"#D10"`; `focus_speed(code: int) -> str` → `"#F%02d"`; `focus_push_auto() -> str` → `"OSE:69:1"` (aw_cam) ⚠
  - `iris_auto(on: bool) -> str` → `"#D31"`/`"#D30"`; `iris_abs(pos: int) -> str` → `"#AXI%03X"` (0x555..0xFFF)
  - `preset_recall(n: int) -> str` → `"#R%02d"`, `preset_store(n: int) -> str` → `"#M%02d"`; n = 0..99 (в UI показывается n+1)
  - Константы: `STOP_PT = "#PTS5050"`, `STOP_ZOOM = "#Z50"`, `Q_POWER = "#O"`, `Q_PAN_TILT = "#APC"`, `Q_ZOOM = "#GZ"`, `Q_FOCUS = "#GF"`, `Q_IRIS = "#GI"`, `Q_MODEL = "QID"`
  - `cgi_for(cmd: str) -> Literal["aw_ptz","aw_cam"]`: `#…` → `aw_ptz`, иначе `aw_cam`
  - `build_url(host: str, cmd: str) -> str` (кодирует `#` как `%23`, добавляет `&res=1`)
  - `class CameraError(Exception)` с полем `code: str` (`"er1"|"er2"|"er3"|…`)
  - `check_response(body: str) -> str`: возвращает тело; если оно начинается с `er` (без учёта регистра) → `CameraError`
  - `parse_pan_tilt(body: str) -> tuple[int,int]` (`"aPC80008000"` → `(0x8000, 0x8000)`), `parse_zoom(body: str) -> int` (`"gz555"` → `0x555`), `parse_model(body: str) -> str` (`"OID:AW-HE42"` → `"AW-HE42"`)

- [ ] **Step 1: Failing tests:**
```python
def test_pts(): assert pan_tilt_speed(50,50) == "#PTS5050"; assert pan_tilt_speed(1,99) == "#PTS0199"
@pytest.mark.parametrize("a",[0,100,-1])
def test_pts_range(a):
    with pytest.raises(ValueError): pan_tilt_speed(a,50)
def test_zoom(): assert zoom_speed(75)=="#Z75"; assert zoom_abs(0x555)=="#AXZ555"; assert zoom_abs(0xFFF)=="#AXZFFF"
def test_zoom_abs_range():
    with pytest.raises(ValueError): zoom_abs(0x554)
def test_apc(): assert pan_tilt_abs(0x8000,0x8000)=="#APC80008000"
def test_presets(): assert preset_recall(0)=="#R00"; assert preset_store(9)=="#M09"
def test_url(): assert build_url("10.0.0.12","#PTS5050")=="http://10.0.0.12/cgi-bin/aw_ptz?cmd=%23PTS5050&res=1"
def test_url_cam(): assert build_url("10.0.0.12","QID")=="http://10.0.0.12/cgi-bin/aw_cam?cmd=QID&res=1"
def test_error():
    with pytest.raises(CameraError) as e: check_response("eR2:PTS")
    assert e.value.code == "er2"
def test_parse(): assert parse_pan_tilt("aPC80008000")==(0x8000,0x8000); assert parse_zoom("gz6A0")==0x6A0; assert parse_model("OID:AW-HE42")=="AW-HE42"
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Commit** `feat: Panasonic command protocol`

---

### Task 3: HTTP-клиент камеры

**Files:**
- Create: `src/ptztracker/camera/client.py`
- Test: `tests/camera/test_client.py`

**Interfaces:**
- Consumes: `build_url`, `check_response`, `CameraError` (Task 2).
- Produces:
  - `class CameraUnreachable(Exception)`, `class AuthRequired(Exception)`
  - `CameraClient(host: str, username: str|None=None, password: str|None=None, timeout: float=1.0, transport: httpx.BaseTransport|None=None)`
  - `.send(cmd: str) -> str`: GET `build_url`, заголовок `Connection: close`, при наличии логина `httpx.DigestAuth`. Таймаут или ошибка соединения → `CameraUnreachable`; 401 → `AuthRequired`; тело проходит через `check_response`.
  - `.close() -> None`

- [ ] **Step 1: Failing tests** (используется `httpx.MockTransport`):
```python
def test_send_ok():  # transport возвращает 200 "pTS5050"; проверить URL и заголовок Connection: close
    assert client.send("#PTS5050") == "pTS5050"
def test_timeout_raises_unreachable_fast():
    # transport бросает httpx.ConnectTimeout
    t0=time.monotonic()
    with pytest.raises(CameraUnreachable): client.send("#O")
    assert time.monotonic()-t0 < 1.5
def test_401_auth_required(): ...  # 401 → AuthRequired
def test_camera_error_passthrough(): ...  # 200 "eR1:XYZ" → CameraError code "er1"
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Commit** `feat: camera HTTP client`

---

### Task 4: Планировщик команд

**Files:**
- Create: `src/ptztracker/camera/scheduler.py`
- Test: `tests/camera/test_scheduler.py`

**Interfaces:**
- Consumes: `CameraError`, `CameraUnreachable`, `AuthRequired`; `STOP_PT`, `STOP_ZOOM`.
- Produces:
  - `class Priority(IntEnum): EMERGENCY=0, MANUAL=1, TRACKING=2, QUERY=3`
  - `CommandScheduler(send: Callable[[str], str], min_interval: float=0.130, clock: Callable[[], float]=time.monotonic)`
  - `.submit(key: str, cmd: str, priority: Priority, only_if_changed: bool=True) -> None`: кладёт команду в ячейку `key` и заменяет ещё не отправленную команду с тем же ключом (склейка). Если `only_if_changed` и `cmd` совпадает с последней *успешно* отправленной по этому ключу, команда не ставится.
  - `.query(cmd: str, timeout: float=2.0) -> str`: блокирующий запрос через очередь с приоритетом QUERY (через `concurrent.futures.Future`); ошибки пробрасываются вызывающему.
  - `.emergency_stop() -> None`: очищает все ячейки и ставит `STOP_PT` и `STOP_ZOOM` с приоритетом EMERGENCY и `only_if_changed=False`.
  - `.pump() -> str|None`: детерминированное ядро без потоков. Если с прошлой отправки прошло меньше `min_interval`, возвращает `None`; иначе отправляет одну команду с наивысшим приоритетом (при равенстве — самую старую) и возвращает её.
  - `.start()` / `.stop()`: фоновый поток, который вызывает `pump()` и спит до следующего окна.
  - `.online: bool`, `.last_error: str|None`, `on_state_change: Callable[[bool], None]|None`
  - Ключи, которые используют остальные модули: `"pt"`, `"zoom"`, `"focus"`, `"focus_mode"`, `"iris"`, `"iris_mode"`, `"preset"`.
- Политика ошибок:
  - `CameraError` или `CameraUnreachable` на команде с ключом: команда возвращается в ячейку (если её не заменили новой), `last_sent` не обновляется, `online=False` только при `CameraUnreachable`.
  - Повтор не чаще `min_interval`; после 3 подряд `CameraUnreachable` следующий повтор не раньше чем через 1 с.
  - Первый успешный ответ ставит `online=True`.

- [ ] **Step 1: Failing tests** (поддельные часы `FakeClock`, `send` пишет в список):
```python
def test_respects_min_interval():
    s.submit("pt","#PTS6050",Priority.MANUAL); assert s.pump()=="#PTS6050"
    s.submit("zoom","#Z60",Priority.MANUAL); clock.t += 0.10; assert s.pump() is None
    clock.t += 0.031; assert s.pump()=="#Z60"
def test_coalesces_same_key():
    s.submit("pt","#PTS6050",Priority.TRACKING); s.submit("pt","#PTS7050",Priority.TRACKING)
    assert s.pump()=="#PTS7050"; clock.t+=1; assert s.pump() is None
def test_only_if_changed():
    s.submit("pt","#PTS6050",Priority.TRACKING); s.pump(); clock.t+=1
    s.submit("pt","#PTS6050",Priority.TRACKING); assert s.pump() is None
def test_priority_order():
    s.submit("zoom","#Z60",Priority.TRACKING); s.submit("pt","#PTS4050",Priority.MANUAL)
    assert s.pump()=="#PTS4050"
def test_emergency_stop_preempts_and_clears():
    s.submit("pt","#PTS9950",Priority.MANUAL); s.emergency_stop()
    first = s.pump(); clock.t += 1; second = s.pump(); clock.t += 1
    assert {first, second} == {"#PTS5050","#Z50"}
    assert s.pump() is None          # #PTS9950 выброшена
def test_camera_error_retried_not_lost():   # Review Focus 5
    # send: первый вызов бросает CameraError("er2"), дальше возвращает "pTS6050"
    s.submit("pt","#PTS6050",Priority.TRACKING); s.pump(); clock.t+=0.131
    assert s.pump()=="#PTS6050"; assert sent_ok == ["#PTS6050"]
def test_unreachable_sets_offline_and_recovers():   # Review Focus 1
    # send: первый вызов CameraUnreachable, второй успешен
    s.submit("pt","#PTS6050",Priority.MANUAL); s.pump(); assert s.online is False
    clock.t += 0.131; s.pump(); assert s.online is True; assert changes == [False, True]
def test_query_returns_response_threaded():
    s.start(); assert s.query("#O") == "p1"; s.stop()
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Commit** `feat: command scheduler with interval, priorities, coalescing`

---

### Task 5: Симулятор — модель PTZ и HTTP-сервер

**Files:**
- Create: `src/ptztracker/sim/__init__.py`, `sim/ptz_model.py`, `sim/http_server.py`
- Test: `tests/sim/test_ptz_model.py`, `tests/sim/test_http_server.py`

**Interfaces:**
- Consumes: функции построения и разбора команд из Task 2 (для тестов).
- Produces:
  - `SimPtz(speed_with_zoom: bool=True, min_interval: float|None=None, clock=time.monotonic)` с полями `pan_deg`, `tilt_deg`, `zoom` (0..1), `focus_auto`, `iris_auto`, `presets: dict[int, tuple]`
  - `.handle(cmd: str) -> str`: поддерживает `#PTS #P #T #Z #APC(+запрос) #AXZ #GZ #D1x #D3x #AXI #GI #F #GF #Rxx #Mxx #O QID`, отвечает как камера (`pTS…`, `zS…`, `aPC…`, `gz…`, `p1`, `OID:AW-HE42`). Неизвестная команда → `"eR1:<cmd>"`. Если задан `min_interval` и команда пришла раньше чем `min_interval - 0.005` с → `"eR2:<cmd>"`. Поле `received: list[str]` хранит последние 200 команд. На неизвестные пути HTTP-сервер отвечает 404.
  - `.update(dt: float) -> None`: скорость стремится к заданной с постоянной времени 0.12 с, затем интегрируется положение; ограничения pan ±175°, tilt −30…+90°.
  - `.hfov_deg() -> float`: 62.1° на широком угле до 3.3° на теле, интерполяция по `zoom` логарифмическая (зум 20×).
  - Модель скорости (только для симулятора; реальную даст диагностика ⚠): `n=|code-50|`, `deg_s = 90*(n/49)**2`; если `speed_with_zoom`, ещё делится на `zoom_ratio=20**zoom`. Зум: `dzoom/dt = sign*0.4*(n/49)**1.5`.
  - Шкала APC (только симулятор): `0x8000` = 0°, 1 единица = 0.0075°.
  - `SimHttpServer(sim: SimPtz, host="127.0.0.1", port=0)`: `.start() -> int` (возвращает порт), `.stop()`, `.url_host -> str` (`"127.0.0.1:<port>"`). Обслуживает `/cgi-bin/aw_ptz` и `/cgi-bin/aw_cam`. Свой поток вызывает `sim.update` с частотой 100 Гц.

- [ ] **Step 1: Failing tests:**
```python
def test_pan_right_moves_right():
    s=SimPtz(); s.handle("#PTS9950"); [s.update(0.01) for _ in range(100)]
    assert s.pan_deg > 50
def test_stop_decelerates_smoothly():
    s=SimPtz(); s.handle("#PTS9950"); [s.update(0.01) for _ in range(100)]
    s.handle("#PTS5050"); [s.update(0.01) for _ in range(60)]
    p=s.pan_deg; s.update(0.01); assert abs(s.pan_deg-p) < 0.01
def test_slower_at_tele_when_speed_with_zoom():
    a=SimPtz(); a.zoom=0.0; b=SimPtz(); b.zoom=1.0
    for s in (a,b): s.handle("#PTS7050"); [s.update(0.01) for _ in range(100)]
    assert b.pan_deg < a.pan_deg / 10
def test_apc_query_roundtrip():
    s=SimPtz(); assert s.handle("#APC")=="aPC80008000"; s.handle("#APC90008000"); assert parse_pan_tilt(s.handle("#APC"))[0]==0x9000
def test_too_fast_gives_er2():
    s=SimPtz(min_interval=0.13, clock=fake); s.handle("#O"); fake.t+=0.05; assert s.handle("#O").lower().startswith("er2")
def test_unknown(): assert SimPtz().handle("#XYZ").lower().startswith("er1")
def test_http_server_with_real_client():
    srv=SimHttpServer(SimPtz()); srv.start()
    assert CameraClient(srv.url_host).send("#O")=="p1"; assert CameraClient(srv.url_host).send("QID")=="OID:AW-HE42"
    srv.stop()
```
`CameraClient` принимает `host` с портом (`"127.0.0.1:54321"`), и `build_url` это поддерживает.
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Commit** `feat: PTZ simulator model and HTTP server`

---

### Task 6: Источники видео (RTSP и симулятор) и синтетическая сцена

**Files:**
- Create: `src/ptztracker/video/__init__.py`, `video/frames.py`, `video/rtsp.py`, `sim/scene.py`, `sim/frame_source.py`
- Test: `tests/video/test_rtsp.py`, `tests/sim/test_frame_source.py`

**Interfaces:**
- Consumes: `SimPtz` (Task 5).
- Produces:
  - `@dataclass(frozen=True) Frame(image: np.ndarray  # BGR uint8, t_capture: float  # time.monotonic, seq: int)`
  - `@dataclass SourceStats(connected: bool, fps: float, width: int, height: int, error: str|None, url: str)`
  - `class FrameSource(Protocol)`: `start()`, `stop()`, `latest() -> Frame|None`, `stats() -> SourceStats`
  - `RtspSource(url: str, open_timeout_s: float=5.0)`: поток с PyAV, опции `{"rtsp_transport":"tcp","fflags":"nobuffer","flags":"low_delay","max_delay":"0","timeout":"5000000"}` (для `http://` без `rtsp_transport`). Хранится только последний кадр. При ошибке: `stats().connected=False`, `error` — текст на русском, переподключение с паузой 1→2→5 с. Исключения наружу не выходят. `stop()` завершает поток за ≤ 2 с.
  - `rtsp_candidates(host: str, username: str|None, password: str|None) -> list[str]`, в таком порядке (⚠, проверяется диагностикой): `rtsp://{auth}{host}/MediaInput/h264`, `rtsp://{auth}{host}/MediaInput/h264/stream_1`, `rtsp://{auth}{host}:554/MediaInput/h264/stream_1`, `rtsp://{auth}{host}/MediaInput/stream_1`, `http://{auth}{host}/cgi-bin/mjpeg?resolution=640x360&framerate=30`. `auth` пустой без логина; логин и пароль URL-кодируются.
  - `SyntheticStage(width=7680, height=2560, seed=0)`, `.frame_at(t: float) -> np.ndarray`: сцена (фон, экран, кафедра) и одна фигура человека (голова, торс, ноги, высота ≈ 18% высоты сцены). Фигура ходит по сценарию: стоит у кафедры 6 с, идёт вправо 4 с, стоит 5 с, идёт влево 6 с, цикл. Вся геометрия детерминирована по `seed`.
  - `VideoScene(path: str)`, `.frame_at(t) -> np.ndarray`: зацикленное видео через `cv2.VideoCapture`.
  - `SimFrameSource(sim: SimPtz, scene, out_size=(1280,720), fps=25.0, latency_s=0.30, pan_span_deg=120.0, tilt_span_deg=40.0)`: сцена покрывает pan ±60°, tilt ±20°. Вырез шириной `W*hfov/pan_span_deg` центрируется по pan/tilt, масштабируется в `out_size`, область за пределами сцены чёрная. `latest()` отдаёт самый свежий кадр, отрисованный не позже чем `now - latency_s`, и `t_capture` = время отрисовки.

- [ ] **Step 1: Failing tests:**
```python
def test_rtsp_bad_url_reports_error_no_exception():    # Review Focus 2
    src=RtspSource("rtsp://127.0.0.1:9/nothing", open_timeout_s=0.5); src.start(); time.sleep(1.5)
    st=src.stats(); assert st.connected is False and st.error; assert src.latest() is None
    t0=time.monotonic(); src.stop(); assert time.monotonic()-t0 < 2.5
def test_rtsp_reads_local_file_as_stream(tmp_path):
    # сгенерировать 2-секундный mp4 через PyAV, открыть путь как url → latest() не None, width/height верные
def test_candidates_with_auth():
    c=rtsp_candidates("10.0.0.12","admin","p@ss")
    assert c[0]=="rtsp://admin:p%40ss@10.0.0.12/MediaInput/h264" and c[-1].startswith("http://")
def test_sim_frame_moves_with_pan():
    sim=SimPtz(); fs=SimFrameSource(sim,SyntheticStage(),latency_s=0.0); fs.start(); time.sleep(0.3); a=fs.latest().image
    sim.pan_deg=10; time.sleep(0.3); b=fs.latest().image; fs.stop()
    (dx,_),_=cv2.phaseCorrelate(np.float32(cv2.cvtColor(a,cv2.COLOR_BGR2GRAY)), np.float32(cv2.cvtColor(b,cv2.COLOR_BGR2GRAY)))
    assert dx < -20      # картинка уехала влево при повороте вправо
def test_sim_latency():
    fs=SimFrameSource(SimPtz(),SyntheticStage(),latency_s=0.3); fs.start(); time.sleep(0.8)
    assert fs.latest().t_capture <= time.monotonic()-0.3+0.05; fs.stop()
def test_synthetic_stage_deterministic():
    assert np.array_equal(SyntheticStage(seed=1).frame_at(3.0), SyntheticStage(seed=1).frame_at(3.0))
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Commit** `feat: RTSP and simulated frame sources, synthetic stage`

---

### Task 7: CameraSession и перевод скорости в коды

**Files:**
- Create: `src/ptztracker/camera/speed.py`, `src/ptztracker/camera/session.py`
- Test: `tests/camera/test_speed.py`, `tests/camera/test_session.py`

**Interfaces:**
- Consumes: `CameraConfig`, `get_password` (Task 1); построение команд (Task 2); `CameraClient` (Task 3); `CommandScheduler`, `Priority` (Task 4); `SimPtz`, `SimHttpServer` (Task 5); `FrameSource`, `RtspSource`, `rtsp_candidates`, `SimFrameSource`, `SyntheticStage`, `VideoScene` (Task 6).
- Produces:
  - `velocity_to_code(v: float, dead: float=0.02) -> int`: `|v|<dead` → 50; иначе `50 + sign(v)*(1+round(48*min(|v|,1)))`. Результат 1..99.
  - `CameraSession(cfg: CameraConfig, scene_path: str|None=None)`:
    - `.start()`. Для `kind="sim"` поднимает `SimPtz(min_interval=cfg.min_interval)`, `SimHttpServer` и `SimFrameSource`. Для `"panasonic"` создаёт клиента на `cfg.ip` и источник `RtspSource(cfg.rtsp_url or rtsp_candidates(...)[0])`.
    - `.stop()`: сначала `emergency_stop()` и до 0.5 с ожидания отправки, затем остановка источника, планировщика и сервера.
    - `.frames: FrameSource`, `.scheduler: CommandScheduler`, `.client: CameraClient`, `.sim: SimPtz|None`
    - `.set_pan_tilt_velocity(vx: float, vy: float)` → `submit("pt", pan_tilt_speed(velocity_to_code(vx), velocity_to_code(vy)), MANUAL)`; vx>0 = вправо, vy>0 = вверх
    - `.set_zoom_velocity(v: float)` → ключ `"zoom"`, v>0 = к теле
    - `.set_focus_auto(on)`, `.push_focus()`, `.set_focus_velocity(v)`, `.set_iris_auto(on)`, `.set_iris(pos01: float)` (0..1 → 0x555..0xFFF), `.recall_preset(n)`, `.store_preset(n)`
    - `.emergency_stop()`
    - `.online -> bool`

- [ ] **Step 1: Failing tests:**
```python
@pytest.mark.parametrize("v,c",[(0,50),(0.01,50),(1,99),(-1,1),(0.5,75),(-0.5,25),(2,99)])
def test_velocity_to_code(v,c): assert velocity_to_code(v)==c
def test_sim_session_manual_pan():
    s=CameraSession(CameraConfig(id="s",name="Сим",ip="sim",kind="sim")); s.start()
    s.set_pan_tilt_velocity(1,0); time.sleep(0.6); s.set_pan_tilt_velocity(0,0); time.sleep(0.6)
    assert s.sim.pan_deg > 5; p=s.sim.pan_deg; time.sleep(0.3); assert abs(s.sim.pan_deg-p) < 0.2; s.stop()
def test_stop_sends_emergency_stop():     # Review Focus 3
    s=CameraSession(CameraConfig(id="s",name="Сим",ip="sim",kind="sim")); s.start()
    s.set_pan_tilt_velocity(1,0); time.sleep(0.3); s.stop()
    pts=[c for c in s.sim.received if c.startswith("#PTS")]; assert pts[-1]=="#PTS5050"
    assert "#Z50" in s.sim.received
def test_unreachable_camera_session_does_not_block():   # Review Focus 1
    s=CameraSession(CameraConfig(id="x",name="X",ip="10.255.255.1")); t0=time.monotonic(); s.start()
    s.set_pan_tilt_velocity(1,0); assert time.monotonic()-t0 < 0.5; time.sleep(1.5); assert s.online is False; s.stop()
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Commit** `feat: camera session with manual control`

---

### Task 8: Интерфейс — главное окно, превью, камеры, ручное управление

**Files:**
- Create: `src/ptztracker/ui/__init__.py`, `ui/main_window.py`, `ui/preview.py`, `ui/camera_dialog.py`, `ui/manual_controls.py`, `ui/joystick.py`
- Modify: `src/ptztracker/__main__.py`: без аргументов запускает GUI; `--sim` добавляет в список камеру «Симулятор» (kind sim) и выбирает её.
- Test: `tests/ui/test_joystick.py`, `tests/ui/test_camera_dialog.py`, `tests/ui/test_main_window.py` (pytest-qt; на Mac `QT_QPA_PLATFORM=offscreen` в `conftest.py`)

**Interfaces:**
- Consumes: `ConfigStore`, `AppConfig`, `CameraConfig`, `is_valid_ip`, `get/set_password` (Task 1); `CameraSession` (Task 7); `Frame`, `SourceStats` (Task 6).
- Produces:
  - `JoystickWidget(QWidget)`, сигнал `moved(float, float)` (x вправо, y вверх, −1..1, круговое ограничение). При отпускании мыши и при `focusOutEvent` пружинит в (0,0) и испускает `moved(0.0, 0.0)`.
  - `CameraDialog(QDialog)`: поля «Название», «IP-адрес», «Логин», «Пароль», «RTSP-адрес (необязательно)», «Мин. интервал команд, мс» (130), кнопка «Проверить связь» (через `CameraClient.send(Q_MODEL)` в `QThreadPool`, результат текстом). `.result_config() -> CameraConfig`, `.password() -> str|None`. Кнопка OK неактивна, пока `is_valid_ip` ложно.
  - `PreviewWidget(QWidget)`: `.set_source(FrameSource|None)`, перерисовка по таймеру 33 мс, кадр вписан с сохранением пропорций. Без кадра — тёмный фон и надпись «Нет видео» плюс `stats().error`.
  - `ManualControls(QWidget)` с привязкой `.bind(session: CameraSession|None)`: джойстик → `set_pan_tilt_velocity`; ползунок зума с пружиной → `set_zoom_velocity`; фокус: флажок «Автофокус», кнопка «Разовый AF», удерживаемые кнопки «Ближе/Дальше»; ирис: флажок «Авто», ползунок 0..100; пресеты: сетка 1–9, клик — вызов, Shift+клик — запись с подтверждением «Записать текущее положение в пресет N?».
  - `MainWindow(QMainWindow)`:
    - слева превью; справа выбор камеры (`QComboBox` + «Добавить», «Изменить», «Удалить») и `ManualControls`;
    - строка статуса: «● подключено / ○ нет связи», fps потока, размер кадра;
    - меню «Сервис → Диагностика камеры…» (подключается в Task 10);
    - при смене камеры старая сессия останавливается (`stop()`), новая запускается, конфиг сохраняется (`last_camera_id`).
  - Горячие клавиши (окно в фокусе): стрелки — pan/tilt со скоростью 0.4, пока клавиша нажата; `+`/`-` — зум 0.5, пока нажата; `Esc` — `emergency_stop()`. Отпускание клавиши даёт скорость 0. При `changeEvent` с потерей активности окна все удерживаемые скорости сбрасываются в 0. `closeEvent` вызывает `session.stop()` и сохраняет конфиг.

- [ ] **Step 1: Failing tests:**
```python
def test_joystick_spring_back(qtbot):
    j=JoystickWidget(); qtbot.addWidget(j); j.resize(200,200); got=[]
    j.moved.connect(lambda x,y: got.append((x,y)))
    qtbot.mousePress(j, Qt.LeftButton, pos=QPoint(100,100)); qtbot.mouseMove(j, QPoint(190,100))
    assert got[-1][0] > 0.8; qtbot.mouseRelease(j, Qt.LeftButton, pos=QPoint(190,100)); assert got[-1]==(0.0,0.0)
def test_dialog_rejects_bad_ip(qtbot):     # Review Focus 4
    d=CameraDialog(); d.ip_edit.setText("10.0.0"); assert not d.ok_button.isEnabled()
    d.ip_edit.setText("10.0.0.14"); d.name_edit.setText("Сцена-3"); assert d.ok_button.isEnabled()
def test_main_window_sim_preview_and_arrow(qtbot, tmp_path):
    w=MainWindow(ConfigStore(tmp_path/"c.json"), use_sim=True); qtbot.addWidget(w); w.show()
    qtbot.waitUntil(lambda: w.preview.has_frame(), timeout=3000)
    qtbot.keyPress(w, Qt.Key_Right); qtbot.wait(500); qtbot.keyRelease(w, Qt.Key_Right); qtbot.wait(400)
    assert w.session.sim.pan_deg > 1; assert w.session.sim.received[-1].startswith("#PTS50")
def test_close_stops_camera(qtbot, tmp_path):     # Review Focus 3
    w=MainWindow(ConfigStore(tmp_path/"c.json"), use_sim=True); qtbot.addWidget(w); w.show(); sim=w.session.sim
    qtbot.keyPress(w, Qt.Key_Right); qtbot.wait(400); w.close()
    assert [c for c in sim.received if c.startswith("#PTS")][-1]=="#PTS5050"
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Ручная проверка на Mac:** `.venv/bin/python -m ptztracker --sim`. Видно сцену, джойстик и стрелки двигают кадр, Esc останавливает, закрытие окна останавливает камеру.
- [ ] **Step 6: Commit** `feat: main window with preview, camera list and manual controls`

---

### Task 9: Диагностика — измерения, проверки, отчёт

**Files:**
- Create: `src/ptztracker/diagnostics/__init__.py`, `diagnostics/motion.py`, `diagnostics/notify.py`, `diagnostics/checks.py`, `diagnostics/runner.py`
- Test: `tests/diagnostics/test_motion.py`, `tests/diagnostics/test_runner_sim.py`

**Interfaces:**
- Consumes: `CameraClient`, `CommandScheduler`, построение команд и `parse_*`, `RtspSource`, `rtsp_candidates`, `CameraSession` (sim), `SimFrameSource`.
- Produces:
  - `measure_shift(a: np.ndarray, b: np.ndarray, work_width: int=480) -> tuple[float,float]`: сдвиг b относительно a в пикселях **исходного** размера (`cv2.phaseCorrelate` по серым кадрам с окном Ханнинга).
  - `wait_for_motion(source: FrameSource, since: float, threshold_px: float=1.5, timeout: float=3.0) -> float|None`: время (monotonic) первого кадра с `t_capture > since`, где сдвиг относительно опорного кадра больше порога.
  - `NotificationListener(port: int=31004)`: `.start()`, `.stop()`, `.port -> int`, `.events() -> list[tuple[float, bytes]]`; принимает TCP-подключения камеры и пишет сырые байты.
  - `@dataclass CheckResult(id: str, title: str, status: Literal["ok","warn","fail","skip"], summary: str, details: dict, log: list[str])` (`summary` на русском)
  - `@dataclass DiagContext(host: str, username: str|None, password: str|None, allow_move: bool, ask_user: Callable[[str, float], None], progress: Callable[[str], None], out_dir: Path, rtsp_override: str|None=None, notify_port: int=31004, notify_wait_s: float=15.0)`. Тесты передают `notify_port=0` (свободный порт, `NotificationListener.port` сообщает фактический) и `notify_wait_s=1.0`.
  - Проверки (каждая `def check_x(ctx, state: dict) -> CheckResult`, исключения ловятся раннером и превращаются в `fail`):
    1. `reachability`: `#O` → ожидается `p1`; `QID` → модель. 401 → `fail` с подсказкой «включена авторизация — укажите логин/пароль».
    2. `rtsp`: перебрать `ctx.rtsp_override` и `rtsp_candidates`; для каждого 4 с — открылся ли поток, разрешение, fps; первый кадр сохранить `frames/<n>.jpg`. В `state["source"]` положить лучший источник.
    3. `queries`: `#APC`, `#GZ`, `#GF`, `#GI` — сырые ответы и результат разбора.
    4. `min_interval`: интервалы 0.05/0.08/0.10/0.13 с, по 10 запросов `#O` через `CameraClient` напрямую; число `er`/таймаутов на каждом. Рекомендуется минимальный интервал без ошибок.
    5. `command_latency` (только при `allow_move`): 3 раза: `#PTS6050` → `wait_for_motion` → стоп → пауза 1 с. Медиана (команда → видимое движение), мс.
    6. `speed_probe` (только при `allow_move`): коды pan 55, 60, 70, 80 в обе стороны при текущем зуме. Каждый держится, пока суммарный сдвиг не превысит 25% ширины кадра или не пройдёт 1.5 с. Записываются px/с, время разгона до 90% скорости и время торможения после стопа. После всех проходов возврат в исходное положение (`#APC` с сохранёнными координатами, если запрос положения работает; иначе обратные движения с теми же кодами и длительностями).
    7. `zoom_probe` (только при `allow_move`): `#GZ` → `#Z75` на 0.7 с → стоп → `#GZ`; масштаб между кадрами (через `cv2.estimateAffinePartial2D` по ORB); возврат `#AXZ` к исходному значению.
    8. `notifications`: `NotificationListener(31004)` + регистрация `http://{host}/cgi-bin/event?connect=start&my_port=31004&uid=0` ⚠. Затем `ctx.ask_user("Подвигайте камеру ПУЛЬТОМ 10 секунд (влево-вправо и зум), затем отпустите", ctx.notify_wait_s)`. Параллельно каждые 0.5 с опрашиваются `#APC` и `#GZ`. Записываются пришедшие уведомления (сырые) и изменения положения по опросу. В конце отправить `connect=stop`.
  - `run_diagnostics(ctx: DiagContext) -> Path`: выполняет проверки по порядку (5–7 пропускаются, если `allow_move=False`), пишет `report.json`, `report.txt` (русский: таблица проверок + рекомендации), `diag.log`, `frames/*.jpg`, упаковывает в `ptz-diag-<host>-<YYYYmmdd-HHMM>.zip` в `ctx.out_dir` и возвращает путь. Любая проверка, двигавшая камеру, в `finally` шлёт стоп.

- [ ] **Step 1: Failing tests:**
```python
def test_measure_shift_known_translation():
    img=SyntheticStage().frame_at(0)[:720,:1280]; M=np.float32([[1,0,-37],[0,1,12]])
    b=cv2.warpAffine(img,M,(1280,720)); dx,dy=measure_shift(img,b); assert abs(dx+37)<1.5 and abs(dy-12)<1.5
def test_full_diagnostics_against_sim(tmp_path):
    # поднять sim-сессию (SimPtz(min_interval=0.10), SimHttpServer, SimFrameSource(latency_s=0.25))
    # раннеру подставить фабрику источника → SimFrameSource вместо RTSP (параметр ctx.rtsp_override="sim://")
    zip_path=run_diagnostics(ctx_with(allow_move=True, ask_user=lambda m,t: time.sleep(0.1)))
    rep=json.loads(zipfile.ZipFile(zip_path).read("report.json"))
    st={r["id"]:r["status"] for r in rep["checks"]}
    assert st["reachability"]=="ok" and st["rtsp"]=="ok" and st["queries"]=="ok"
    assert 150 <= rep_check(rep,"command_latency")["details"]["median_ms"] <= 600
    assert rep_check(rep,"min_interval")["details"]["recommended_s"]==0.10
    assert abs(sim.pan_deg) < 0.5          # камера вернулась
    assert st["notifications"] in ("warn","fail")   # симулятор уведомления не шлёт — без исключения
    assert "Камера" in zipfile.ZipFile(zip_path).read("report.txt").decode("utf-8")
def test_no_move_skips_motion_checks(tmp_path):
    rep=… run_diagnostics(ctx_with(allow_move=False)) …
    assert all(st[i]=="skip" for i in ("command_latency","speed_probe","zoom_probe"))
    moving=[c for c in sim.received if (c.startswith("#PTS") and c!="#PTS5050") or (c.startswith("#Z") and c!="#Z50")
            or c.startswith("#AXZ") or (c.startswith("#APC") and len(c)>4)]
    assert moving == []
def test_unreachable_host_report_still_written(tmp_path):
    ctx host="10.255.255.1" → zip exists, reachability "fail", остальные "skip"/"fail", выполнение < 30 с
```
`rtsp_override="sim://"` даёт фабрике источников `SimFrameSource` из переданного `state["sim"]`. Это нужно только тестам, в UI и CLI этого режима нет.
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Commit** `feat: camera diagnostics with report archive`

---

### Task 10: Запуск диагностики из CLI и из интерфейса

**Files:**
- Modify: `src/ptztracker/__main__.py`
- Create: `src/ptztracker/ui/diagnostics_dialog.py`
- Modify: `src/ptztracker/ui/main_window.py` (меню «Сервис → Диагностика камеры…»)
- Test: `tests/test_cli.py`, `tests/ui/test_diagnostics_dialog.py`

**Interfaces:**
- Consumes: `run_diagnostics`, `DiagContext` (Task 9); `MainWindow` (Task 8).
- Produces:
  - CLI: `ptztracker --diagnose --ip HOST [--user U] [--password P] [--rtsp URL] [--no-move] [--out DIR]`. По умолчанию `--out` — рабочий стол пользователя (`platformdirs.user_desktop_dir()`). Перед движением спрашивает «Камера будет немного двигаться. Убедитесь, что она не в эфире. Продолжить? [д/н]». `ask_user` печатает текст и ждёт указанное время с обратным отсчётом. В конце печатает путь к архиву. Код возврата 0, даже если проверки провалились; 2 — при ошибке аргументов.
  - `DiagnosticsDialog(QDialog)`: флажок «Разрешить движение камеры» (по умолчанию выкл) и предупреждение «камера не должна быть в эфире». Кнопка «Запустить», журнал прогресса, большая надпись для `ask_user` с обратным отсчётом, по завершении кнопка «Открыть папку с отчётом». Диагностика работает в `QThread`. Текущая сессия на время диагностики останавливается (`session.stop()`), после — запускается снова.

- [ ] **Step 1: Failing tests:**
```python
def test_cli_requires_ip(capsys): assert main(["--diagnose"])==2
def test_cli_against_sim(tmp_path, monkeypatch):
    # monkeypatch input → "д"; поднять SimHttpServer; запустить main(["--diagnose","--ip",srv.url_host,"--no-move","--out",str(tmp_path)])
    assert main(...)==0 and list(tmp_path.glob("ptz-diag-*.zip"))
def test_dialog_runs_and_shows_path(qtbot, tmp_path): … with sim host, allow_move unchecked → finished signal, path label exists
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Commit** `feat: diagnostics CLI and dialog`

---

### Task 11: Сборка .exe и инструкция

**Files:**
- Create: `packaging/ptztracker.spec`, `build.bat`, `.github/workflows/windows-build.yml`, `README.md`
- Test: `tests/test_packaging.py`

**Interfaces:**
- Consumes: пакет целиком.
- Produces:
  - `packaging/ptztracker.spec`: onedir, имя `PTZTracker`, `console=False` для GUI. Второй EXE `PTZTracker-diag` с `console=True` вызывает тот же `main` с `--diagnose` впереди аргументов. В сборку собираются данные PySide6 и PyAV (`collect_all("av")`).
  - `build.bat`: проверяет `py -3.12`, создаёт `.venv`, ставит `.[build]`, запускает `pyinstaller packaging\ptztracker.spec --noconfirm`, упаковывает `dist\PTZTracker` в `dist\PTZTracker-win64.zip`.
  - `.github/workflows/windows-build.yml`: на `push` и `workflow_dispatch`, `windows-2022`, Python 3.12, `pip install -e .[dev,build]`, `pytest -q -m "not slow"`, затем сборка и артефакт `PTZTracker-win64.zip`.
  - `README.md` (на русском):
    - что это;
    - запуск из исходников и `--sim`;
    - сборка (`build.bat` или GitHub Actions после подключения репозитория);
    - **раздел «Диагностика камеры на площадке»**: пошагово, какой файл запустить (`PTZTracker-diag.exe --ip 10.0.0.12`), что делать при просьбе подвигать пультом и какой архив прислать разработчику.

- [ ] **Step 1: Failing test** `tests/test_packaging.py`: spec-файл парсится как Python (`compile`), в нём есть `"PTZTracker"` и `"PTZTracker-diag"`; `build.bat` содержит `py -3.12` и `pyinstaller`; workflow — валидный YAML (`yaml` берётся из зависимостей pytest или читается простой проверкой ключей `jobs:` и `windows-2022`).
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS, плюс весь набор `.venv/bin/pytest -q`: всё зелёное.
- [ ] **Step 5: Сборка на Mac для проверки spec** (не для поставки): `.venv/bin/pip install -e ".[build]" && .venv/bin/pyinstaller packaging/ptztracker.spec --noconfirm`. Сборка проходит без ошибок, а `dist/PTZTracker/PTZTracker --sim` запускается и показывает окно. Windows-сборку выполняет пользователь (`build.bat`) или CI.
- [ ] **Step 6: Commit** `build: PyInstaller spec, Windows build script, CI, README`

---

## После этапа 1

1. Пользователь собирает `.exe` (через `build.bat` на любом Windows-ПК с Python 3.12 или через GitHub Actions) и запускает `PTZTracker-diag.exe --ip 10.0.0.12` на площадке. Сначала без движения, затем с движением, когда камера не в эфире.
2. По архиву отчёта обновляются ⚠-пункты в §7 дизайна: RTSP-адрес, запрос положения, реальный интервал, уведомления пульта, таблица скоростей.
3. Пишется план этапа 2 (видение: детектор позы, трекинг, клик по человеку).
