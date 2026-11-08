# Запись видео и показ нагрузки — план

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** оператор одним действием пишет видео любой камеры в файл, видит запас места на диске выбранной папки и нагрузку на компьютер.

**Architecture:** новый пакет `videorec` — один рекордер на `CameraSession` со своим потоком и контроллер без Qt, который сводит состояние всех камер. `system_load.py` — фоновый снимок нагрузки. UI только читает снимки по таймеру.

**Tech Stack:** Python 3.12–3.13, PySide6, PyAV 19 (`libx264`, фрагментированный MP4), psutil, `ctypes` (PDH и тома Windows).

**Spec:** `docs/superpowers/specs/2026-11-08-video-recording-and-load-design.md`; макет `docs/design/mockups/recording-controls.html`.

## Global Constraints

- Запись не шлёт команд камере, не снимает флажки агента, не выключает слежение. Esc запись не трогает.
- Поток Qt не ждёт сеть, диск и подсчёт нагрузки.
- Пароли не попадают в логи, сообщения и имена файлов (`redact_url`).
- Цвета — только из `ui/theme.py` (`test_no_hardcoded_colors`). Новый токен `C.REC = "#FF3B30"` — и в `docs/BRANDBOOK.md`.
- Окно ~1640×850, правая панель без изменений, шапка клетки без изменений.
- Пороги: нагрузка > 85 % оранжевая, > 95 % красная; запас < 20 мин оранжевый, < 5 мин красный, < 1 мин автостоп. Части файла на FAT32 — 3 900 000 000 байт.
- После каждой задачи: `.venv/bin/pytest -q -m "not slow"` (выборочно, полный быстрый набор — перед коммитом UI-задач), `.venv/bin/ruff check src tests`.
- Новые ручные проверки на площадке — в `docs/LIVE_TEST_PROMPT.md` и `docs/BACKLOG.md` (задача 11).

## Review Focus

Для каждой строки тест добавлен в задачу-владельца.

1. Папка на вынутом диске (`E:\…`, `/Volumes/T7/…`): «не подключён», а не запас соседнего родительского тома (задача 3).
2. Имя камеры с `/ : ? * "` или точкой в конце, две камеры с одним именем: файлы создаются и не затирают друг друга (задача 4).
3. Папку нельзя создать (только чтение): сообщение, REC не загорается (задача 5).
4. Стоп раньше первого кадра и двойное быстрое R: нет пустого файла и второго рекордера (задачи 5, 7).
5. Клик по плашке записи не выбирает человека, не снимает флажки; в режиме «камера как курсор» плашка не перехватывает клик (задача 8).

---

### Задача 0: закоммитить мелочи

- [ ] Проверить, что `git status` содержит только правки мелочей, спеку, план и макет.
- [ ] Коммит:

```bash
git add -A && git commit -m "fix: thumbnails on Cyrillic paths, probe button never hangs, stops survive failing cleanup; recording spec and mockup"
```

### Задача 1: снимок нагрузки

**Files:** Create `src/ptztracker/system_load.py`, `tests/test_system_load.py`. Modify `pyproject.toml` (`psutil>=5.9`), `packaging/ptztracker.spec` (если сборка не находит psutil).

**Interfaces — Produces:**
- `Metric(total: float | None, own: float | None, detail: str = "")`, `LoadSnapshot(cpu: Metric, gpu: Metric, ram: Metric)` — frozen dataclass.
- `load_level(value: float | None) -> str` → `""`, `"warn"`, `"crit"`.
- `parse_ioreg(text: str) -> float | None`.
- `gpu_from_engines(samples: dict[str, float], pid: int) -> tuple[float | None, float | None]`.
- `SystemLoad(interval_s=1.0, gpu_reader: Callable[[], tuple[float | None, float | None]] | None = None)`: `.start()`, `.stop()`, `.snapshot() -> LoadSnapshot`.

- [ ] **Тесты (падают):**

```python
def test_levels():
    assert [load_level(v) for v in (None, 85, 85.1, 95, 95.1)] == ["", "", "warn", "warn", "crit"]

def test_ioreg():
    assert parse_ioreg('"PerformanceStatistics" = {"Device Utilization %"=64,"x"=1}') == 64.0
    assert parse_ioreg("") is None

def test_engines_busiest_type_and_own_pid():
    s = {"pid_10_luid_0x0_phys_0_eng_0_engtype_3D": 30.0,
         "pid_20_luid_0x0_phys_0_eng_0_engtype_3D": 25.0,
         "pid_10_luid_0x0_phys_0_eng_1_engtype_Compute": 10.0}
    assert gpu_from_engines(s, 10) == (55.0, 30.0)

def test_engines_cap_and_empty():
    assert gpu_from_engines({"pid_1_x_engtype_3D": 150.0}, 1) == (100.0, 100.0)
    assert gpu_from_engines({}, 1) == (None, None)

def test_snapshot_survives_gpu_failure():
    def boom(): raise RuntimeError
    s = SystemLoad(interval_s=0.05, gpu_reader=boom); s.start()
    # дождаться первого снимка
    assert wait_for(lambda: s.snapshot().cpu.total is not None)
    assert s.snapshot().gpu.total is None
    s.stop()

def test_own_cpu_is_share_of_whole_computer(monkeypatch):
    # Process.cpu_percent 400 при 8 ядрах → own 50
```

- [ ] Запустить `.venv/bin/pytest -q tests/test_system_load.py`, ожидается ImportError.
- [ ] **Реализация.** Поток daemon. CPU — `psutil.cpu_percent(None)`, `Process().cpu_percent(None) / cpu_count()`, min 100. RAM — `virtual_memory().percent`, `memory_info().rss / total * 100`, detail «6,6 из 16 ГБ · PTZ 1,4 ГБ». GPU по умолчанию: Windows — `_PdhGpu` (`ctypes.windll.pdh`, `PdhOpenQueryW`, `PdhAddEnglishCounterW` с `\GPU Engine(*)\Utilization Percentage`, `PdhCollectQueryData`, `PdhGetFormattedCounterArrayW`, `PDH_FMT_DOUBLE`; ошибка → `(None, None)`); Mac — `subprocess.run(["ioreg", "-r", "-c", "IOAccelerator", "-d", "1"], timeout=1)` раз в 2 с, own `None`; иначе `(None, None)`. Сгруппировать в `gpu_from_engines` по `engtype_<X>`, взять максимум по типам, ограничить 100.
- [ ] Добавить `@pytest.mark.skipif(sys.platform != "win32")` тест: настоящий `_PdhGpu` читается дважды без исключения.
- [ ] Тесты проходят; коммит `feat: system load sampler`.

### Задача 2: показ нагрузки в верхней строке

**Files:** Create `src/ptztracker/ui/load_meter.py`, `tests/ui/test_load_meter.py`. Modify `src/ptztracker/ui/main_window.py` (~:380 — перед `seg`; `_fit_top_bar`; `_update_status`; `_prepare_close`), `docs/BRANDBOOK.md` при новых элементах.

**Interfaces — Consumes:** `LoadSnapshot`, `load_level`. **Produces:** `LoadMeter(QWidget).set_snapshot(s: LoadSnapshot)`; `MainWindow.system_load: SystemLoad`.

- [ ] **Тесты (падают):**

```python
def test_two_lines_per_metric(qtbot):
    m = LoadMeter(); qtbot.addWidget(m)
    m.set_snapshot(LoadSnapshot(Metric(34, 12), Metric(52, 41), Metric(41, 9)))
    assert m.total_text("cpu") == "34 %" and m.own_text("cpu") == "PTZ 12 %"

def test_unknown_values_are_dashes(qtbot):
    m = LoadMeter(); qtbot.addWidget(m)
    m.set_snapshot(LoadSnapshot(Metric(34, 12), Metric(50, None), Metric(None, None)))
    assert m.own_text("gpu") == "PTZ —" and m.total_text("ram") == "—"

def test_levels_set_property(qtbot):
    # cpu 86 → level "warn", 96 → "crit", 50 → ""

def test_tooltip_table(qtbot):
    # "Всего", "PTZ Tracker", detail памяти есть в m.toolTip()

def test_window_has_meter_and_hides_when_narrow(qtbot, tmp_path):
    # w.load_meter — потомок w._top; при ширине 900 скрыт, TRACK виден; при 1640 виден
```

- [ ] **Реализация.** Подписи: CPU, GPU, RAM (11 px, `TEXT_FAINT`), значение 13 px, вторая строка 11 px. Табличные цифры. Цвет уровня — `C.LOST` / `C.FAULT`. Обновление — в `_update_status` раз в 2 тика (таймер 500 мс). `_fit_top_bar`: сначала ATEM, затем `load_meter.hide()`.
- [ ] Снимок: `python -m ptztracker --sim` сравнить с макетом, положить в `docs/design/load-meter-after.png`.
- [ ] Тесты и линтер; коммит `feat: CPU/GPU/RAM load in the top bar`.

### Задача 3: диск папки записи

**Files:** Create `src/ptztracker/videorec/__init__.py`, `src/ptztracker/videorec/disk.py`, `tests/videorec/test_disk.py`.

**Interfaces — Produces:**
- `DiskInfo(connected: bool, free: int, total: int, label: str, name: str, removable: bool | None, fs: str)`.
- `disk_info(folder: Path) -> DiskInfo` — без исключений.
- `is_fat32(info: DiskInfo) -> bool` (`fat32`, `vfat`, `msdos`; `exfat` — нет).
- `DiskWatch(folders: Callable[[], Path], interval_s=5.0)`: `.start()`, `.stop()`, `.snapshot() -> DiskInfo | None`.
- Константы `RATE_CAMERA_BPS = 11.5e9 / 3600`, `RATE_LIGHT_BPS = 1.4e9 / 3600`.

- [ ] **Тесты (падают):** существующая папка (`free`, `total` как у `shutil.disk_usage`); папки ещё нет, родитель есть — считается по родителю, `connected=True`; `/Volumes/NoSuch/Записи` на Mac и несуществующий корень диска — `connected=False`, `free=0` (Review Focus 1, для Windows — `monkeypatch` `Path.anchor`/`exists`, сверка с `_volume_root`); `is_fat32` на `"FAT32"`, `"msdos"`, `"exfat"`; `disk_usage` бросает `OSError` → `connected=False`.
- [ ] **Реализация.** `_volume_root(path)`: Windows — `path.anchor`; Mac — `/Volumes/<имя>`, если путь под `/Volumes`, иначе `/`. Корень отсутствует → не подключён. Иначе идти вверх до существующего родителя. `psutil.disk_partitions(all=False)` — самая длинная совпавшая точка монтирования даёт `fs`. Windows: `GetDriveTypeW` (2 — съёмный; USB-SSD может быть 3, тогда `removable=False` — допустимо), `GetVolumeInformationW` — имя. Mac: имя — `/Volumes/<имя>`, `removable = путь под /Volumes`. Нет данных — `name=""`, `removable=None`.
- [ ] Тесты; коммит `feat: disk info for the video folder`.

### Задача 4: файлы записи

**Files:** Create `src/ptztracker/videorec/writer.py`, `tests/videorec/conftest.py` (фикстура `h264_file`: 3 с, 320×240, `libx264`, `g=5`), `tests/videorec/test_writer.py`.

**Interfaces — Produces:**
- `file_stem(camera_name: str, when: datetime) -> str` — `<имя>_<ГГГГ-ММ-ДД_ЧЧ-ММ>`.
- `next_path(folder: Path, stem: str) -> Path` — первый свободный, суффиксы `_02`, `_03`.
- `CopyWriter(folder, stem, template, split_bytes=None)`: `.write_packet(packet)`, `.close()`, `.bytes_written: int`, `.paths: list[Path]`.
- `EncodeWriter(folder, stem, width, height, fps=25, bit_rate=None, split_bytes=None)`: `.write_frame(image_bgr, t_s: float)`, `.close()`, те же свойства.
- `FAT32_SPLIT_BYTES = 3_900_000_000`, `MOVFLAGS = "frag_keyframe+empty_moov+default_base_moof"`.

- [ ] **Тесты (падают):**

```python
def test_stem_is_safe_for_windows():
    s = file_stem('Сцена: 1/2 "A"?.', datetime(2026, 11, 8, 20, 35))
    assert not set('<>:"/\\|?*') & set(s) and s.endswith("_2026-11-08_20-35")
    assert not s.split("_2026")[0].endswith((".", " "))

def test_next_path_suffixes(tmp_path):
    (tmp_path / "a.mp4").touch()
    assert next_path(tmp_path, "a").name == "a_02.mp4"

def test_copy_is_readable_in_cyrillic_folder(h264_file, tmp_path):
    # папка "Запись камер"; весь демукс h264_file в CopyWriter; close; чтение av: кадров == исходных

def test_copy_splits_on_keyframe_without_loss(h264_file, tmp_path):
    # split_bytes небольшой → len(w.paths) >= 2; сумма кадров частей == исходных; каждая часть начинается с ключевого

def test_file_survives_kill(h264_file, tmp_path):
    # подпроцесс пишет CopyWriter и делает os._exit(9) без close; файл открывается, кадров > половины

def test_encode_writer_frames_and_bitrate(tmp_path):
    # 50 кадров 320×240, t_s шаг 0.04 → кадров 50, длительность ~2 с

def test_no_file_until_first_data(tmp_path):
    # создать CopyWriter/EncodeWriter и close() → list(tmp_path.iterdir()) == []
```

- [ ] **Реализация.** Файл открывается лениво, на первых данных; папка `mkdir(parents=True, exist_ok=True)` там же. Части: при `bytes_written_segment >= split_bytes` и `packet.is_keyframe` — закрыть, открыть `next_path`. Время каждой части от нуля: вычитать `dts` первого пакета части. Пакеты с `dts is None` — пропускать. Открытие: `av.open(str(path), "w", format="mp4", options={"movflags": MOVFLAGS})`. `EncodeWriter`: `add_stream("libx264", rate=fps)`, `pix_fmt=yuv420p`, `options={"preset": "veryfast", "g": str(fps)}`, `bit_rate` если задан; `pts` — от `t_s` при `time_base=1/1000`.
- [ ] Тесты; коммит `feat: fragmented MP4 writers with splitting`.

### Задача 5: рекордер (кадры симулятора)

**Files:** Create `src/ptztracker/videorec/recorder.py`, `tests/videorec/test_recorder.py`.

**Interfaces — Consumes:** писатели задачи 4; `FrameSource.latest() -> Frame | None`. **Produces:**
- `RecState(active: bool, signal: bool, elapsed_s: float, bytes_total: int, rate_bps: float, note: str | None, error: str | None, paths: tuple[Path, ...])`; `IDLE`.
- `VideoRecorder(camera_name, folder, mode="camera", *, url: Callable[[], str] | None = None, frames: FrameSource | None = None, open_input=default_open_input, split_bytes=None)`: `.start()`, `.stop()`, `.snapshot() -> RecState`, `.is_running: bool`. Ровно один из `url`/`frames`. `.stop()` не ждёт поток.
- `note` — разовое сообщение: `snapshot()` отдаёт, пока не прочитано контроллером через `.take_note() -> str | None`.

- [ ] **Тесты (падают), `FakeFrames` с растущим `seq`:** старт → файл читается, `bytes_total > 0`, `active`; `stop()` возвращается < 50 мс, поток завершается, файл закрыт; папка под файлом (`tmp/x` — обычный файл) → `active=False`, `error` с текстом «папк», файла нет, `start` не бросает (Review Focus 3); `stop()` до первого кадра → нет файла (Review Focus 4); `write_frame` бросает `OSError` → `active=False`, `error`, другой рекордер не затронут; папка удалена во время записи → стоп с `error`; сбой → пароль не в `error`.
- [ ] **Реализация.** Поток daemon `video-<name>`. Цикл: `latest()`, новый `seq` → `write_frame(image, t_capture)`. Проверка `folder.exists()` раз в 1 с. `rate_bps` — байты за последние 60 с (`deque`). `OSError` → стоп, `error = "Запись остановлена: …"`. Закрытие писателя в потоке рекордера. Режим `camera` — `bit_rate=None`, `light` — `3_000_000`.
- [ ] Тесты; коммит `feat: video recorder for frame sources`.

### Задача 6: рекордер (RTSP, копия, страховка, обрыв)

**Files:** Modify `src/ptztracker/videorec/recorder.py`, `tests/videorec/test_recorder.py`.

**Interfaces — Consumes:** `CopyWriter`, `EncodeWriter`, `redact_url`, `with_credentials`, `_options` из `video/rtsp.py` (`_options` переименовать в `stream_options`). **Produces:** `default_open_input(url: str) -> InputContainer`, `RETRY_DELAYS_S = (2, 5, 10)` (модульная, тесты подменяют).

- [ ] **Тесты (падают), `open_input` отдаёт `av.open(h264_file)`:**
  - копия: кадров в файле столько же, сколько в источнике, декодирования нет (`container.decode` не вызывается — обёртка считает);
  - `light`: файл 25 к/с при источнике 50 к/с (`h264_file_50`), кадров вдвое меньше ±2;
  - обрыв: первый `open_input` кончается исключением посреди, второй отдаёт файл → две части `_02`, `signal` на время паузы `False`, `active=True`, `take_note()` возвращает одно сообщение на обрыв;
  - отказ в подключении трижды → задержки по `RETRY_DELAYS_S`, потом успех;
  - `CopyWriter.write_packet` бросает `av.error.InvalidDataError` → запись продолжается через кодирование, `take_note()` содержит «лёгкий»;
  - `OSError` при записи не запускает страховку, а останавливает;
  - сообщение об ошибке с URL `rtsp://u:secret@h/x` не содержит `secret`.
- [ ] **Реализация.** Поток читает `url()` внутри (пароль из хранилища — не в потоке Qt). Порядок разбора исключений записи: сначала `OSError`, затем `av.error.FFmpegError` / `ValueError` → страховка. `light`: кадр берётся, если `pts_s >= next_slot`, `next_slot += 1/25`.
- [ ] Тесты; коммит `feat: RTSP copy with fallback and reconnect`.

### Задача 7: настройки, сессия, контроллер

**Files:** Modify `src/ptztracker/config.py`, `src/ptztracker/camera/session.py`, `tests/test_config.py`, `tests/camera/test_session.py`. Create `src/ptztracker/videorec/controller.py`, `tests/videorec/test_controller.py`.

**Interfaces — Consumes:** `VideoRecorder`, `DiskWatch`, `is_fat32`, `FAT32_SPLIT_BYTES`. **Produces:**
- `AppConfig.video_dir: str = ""`, `AppConfig.video_mode: str = "camera"` (неизвестное значение при загрузке → `camera`); `video_dir(cfg) -> Path` — «Записи камер» на рабочем столе по умолчанию (как `record_dir`).
- `CameraSession.start_video(folder: Path, mode: str, split_bytes: int | None) -> None`, `.stop_video()`, `.video_state() -> RecState`; `stop()` останавливает через `_guarded`.
- `RecRow(slot, name, active, signal, elapsed_s, bytes_total)`; `RecSummary(rows: list[RecRow], text: str, level: str, free_bytes: int)`.
- `VideoController(cfg, sessions: Callable[[], list[tuple[int, CameraSession]]], disk: DiskWatch)`: `.toggle(session) -> str | None` (текст отказа), `.stop_all()`, `.any_active() -> bool`, `.tick() -> tuple[RecSummary, list[str]]` (разовые сообщения), `.disk_snapshot()`.
- `WARN_MIN, CRIT_MIN, STOP_MIN = 20, 5, 1`.

- [ ] **Тесты (падают):** настройки сохраняются и читаются; `start_video` на симуляторе пишет файл и ни одной новой команды `#…` (сравнить `sim.received` до и после, за вычетом опросов и пульса); слежение и флажки не меняются; `stop()` останавливает запись; двойной `toggle` подряд → один рекордер, запись выключена, пустых файлов нет (Review Focus 4); `toggle` при `connected=False` → текст «не подключён», `active` нет; FAT32 → `split_bytes == FAT32_SPLIT_BYTES`; `exfat` → `None`; текст суммы `"Запись: 1, 3 · 12:34 · 4,8 ГБ · на E: свободно 412 ГБ (~18 ч)"` при подменённых состояниях; уровни 20/5 мин (`""`, `warn`, `crit`); < 1 мин — все остановлены, сообщение одно; камера без сигнала не считается в скорости; пока скорость не набрана — `RATE_*`; запись пропала из-за смены камеры в клетке — одно сообщение «Запись камеры … остановлена»; во время записи папку в настройках сменили — `DiskWatch` считает диск идущей записи.
- [ ] **Реализация.** Симулятор: `frames=self.frames`; живая камера: `url=` — `cfg.rtsp_url`, если он `rtsp://`, иначе `with_credentials(stream_url(ip, "h264_1"), …)`, пароль как в `start`. `VideoController` передаёт в `DiskWatch` папку идущих записей, а без них — `video_dir(cfg)`. Формат ГБ: одна цифра, запятая. Время: `~18 ч`, `~2 ч 5 мин`, `~18 мин`.
- [ ] Тесты; коммит `feat: session recording and video controller`.

### Задача 8: кнопка и плашка на видео, клавиша R

**Files:** Modify `src/ptztracker/ui/theme.py`, `docs/BRANDBOOK.md`, `src/ptztracker/ui/preview.py`, `src/ptztracker/ui/camera_cell.py`, `src/ptztracker/ui/camera_strip.py`, `src/ptztracker/ui/main_window.py` (~:2381 и `_update_status`), `src/ptztracker/ui/hotkeys_dialog.py`/`help_drawings.py` (R в справке F1). Test `tests/ui/test_preview_overlay.py`, `tests/ui/test_camera_strip.py`, новый `tests/ui/test_video_recording_window.py`.

**Interfaces — Consumes:** `RecState`, `VideoController.toggle`. **Produces:** `PreviewWidget.rec_clicked: Signal()`, `PreviewWidget.set_rec(state: RecState | None)` (`None` — камеры нет, кнопки нет), `StripItem.recording: bool`, `MainWindow.video: VideoController`.

- [ ] **Тесты (падают):**

```python
def test_start_chip_only_under_mouse(qtbot): ...       # без мыши rec_rect() None, под мышью есть
def test_click_chip_emits_and_does_not_select(qtbot):  # rec_clicked, цель не выбрана, click_guard не вызван
def test_mouse_mode_ignores_chip(qtbot):               # set_mouse_mode(True): клик не даёт rec_clicked
def test_rec_text_and_no_signal(qtbot):                # "REC 12:34"; signal=False → "REC · нет сигнала"
def test_r_toggles_selected_and_keeps_tracking(qtbot, tmp_path):
    # --sim, флажки агента стоят: R (и ord("К") — русская раскладка, `logical_key`) → запись идёт, флажки и слежение прежние; второе R — стоп
def test_esc_does_not_stop_recording(qtbot, tmp_path): ...
def test_strip_card_shows_dot(qtbot): ...
```

- [ ] **Реализация.** Рисовать в `_paint_overlay` тем же стилем, что `_label`; под мышью на активной плашке «■ Стоп»; мигание точки — по `_tick`. Клик по плашке проверять в начале `mousePressEvent` при выключенном режиме мыши. R — рядом с `Qt.Key_G`, без автоповтора, не вызывает путь «ручное действие». `_update_status` раздаёт `session.video_state()` клеткам.
- [ ] Быстрый набор тестов; снимок `docs/design/recording-controls-after.png`; коммит `feat: record button on video, REC plate and R key`.

### Задача 9: нижняя строка записи

**Files:** Create `src/ptztracker/ui/status_bar.py`, `src/ptztracker/ui/record_status.py`, `tests/ui/test_status_bar.py`, `tests/ui/test_record_status.py`. Modify `src/ptztracker/ui/main_window.py` (~:528, `_update_status`).

**Interfaces — Consumes:** `RecSummary`, `VideoController`. **Produces:** `MessageStatusBar(QStatusBar)`: `.showMessage(text, timeout=0)`, `.clearMessage()`, `.currentMessage()` — текст в своём `QLabel`, родной механизм Qt не задействован, поэтому виджеты слева не прячутся. `RecordPill(QPushButton)`: `.set_summary(s: RecSummary)`, сигналы `stop_requested(slot: int)`, `stop_all_requested()`, `open_folder_requested()`.

- [ ] **Тесты (падают):** `showMessage("x", 50)` → `currentMessage() == "x"`, через 50 мс пусто; виджет слева видим во время сообщения; `messageChanged` испускается; пилюля скрыта при пустом `rows`; `level` `warn`/`crit` меняет `property("level")`; меню содержит по строке на камеру, «Остановить все», «Открыть папку»; сообщения контроллера попадают в строку.
- [ ] **Реализация.** `self.setStatusBar(MessageStatusBar())` до строки ~:528. Пилюля — первая (`addWidget`), `msg_label` — правее, со stretch. Существующие проверки `statusBar().currentMessage()` должны пройти без правок.
- [ ] Быстрый набор тестов; снимок; коммит `feat: recording status in the bottom bar`.

### Задача 10: окно «Запись видео…»

**Files:** Create `src/ptztracker/ui/video_dialog.py`, `tests/ui/test_video_dialog.py`. Modify `src/ptztracker/ui/main_window.py` (~:555–605, меню ☰, группа «Журнал для разбора» или новая). `friendly_path` переиспользовать из `recording_dialog.py`.

**Interfaces — Consumes:** `disk_info`, `DiskInfo`, `is_fat32`, `video_dir`, `RATE_*`. **Produces:** `VideoDialog(cfg: AppConfig, parent=None)`, `.apply()`, `.choose_folder(path: Path)`.

- [ ] **Тесты (падают), `monkeypatch` `disk_info`:** внутренний диск — «Свободно 180 ГБ из 512 ГБ», «одна камера — 15 ч 39 мин», «четыре — 3 ч 54 мин»; 1 ТБ показывается как «1 ТБ»; `removable` и `fs` в подписи; FAT32 — предупреждение из макета; `connected=False` — красная карточка, текст «не подключён»; при смене папки карточка обновляется асинхронно (`waitUntil`); окно не ждёт `disk_info` (медленная подмена, `choose_folder` возвращается < 50 мс); `apply` пишет `video_dir`, `video_mode`; пример имени содержит название выбранной камеры.
- [ ] **Реализация.** `QThreadPool` + сигнал, ответ старой папки отбрасывается. Режимы — две карточки как в макете, «Отмена», «Сохранить». Идущие записи папку не меняют.
- [ ] Снимок; коммит `feat: video folder dialog with per-disk space`.

### Задача 11: выход, документы, сборка

**Files:** Modify `src/ptztracker/ui/main_window.py` (`closeEvent`), `CHANGELOG.md`, `docs/OPERATOR.md`, `docs/ARCHITECTURE.md`, `docs/BACKLOG.md`, `docs/LIVE_TEST_PROMPT.md`, `docs/DECISIONS.md`. Test `tests/ui/test_video_recording_window.py`.

- [ ] **Тесты (падают):** `closeEvent` при записи и ответе «Нет» → `event.isAccepted()` False, запись идёт, камера не остановлена; «Да» → окно закрывается, файл читается, ждёт не дольше ~2 с; без записи вопроса нет. Метод вопроса `_confirm_close_with_video() -> bool` подменяется.
- [ ] Записать: CHANGELOG языком оператора; раздел в OPERATOR; пакет `videorec` и `system_load` в ARCHITECTURE; в DECISIONS — MP4 фрагментами с цифрами опыта, пороги, своё RTSP-подключение; убрать из BACKLOG сделанное про запись; в BACKLOG и LIVE_TEST_PROMPT — проверки площадки из спеки.
- [ ] Полный прогон `.venv/bin/pytest -q`; `.venv/bin/ruff check src tests`.
- [ ] Сборка на Mac: `.venv/bin/pyinstaller packaging/ptztracker.spec --noconfirm`; запуск с `--lab record --ip sim`; `psutil` и запись работают в собранной программе.
- [ ] Независимое ревью всей ветки (модель sonnet), исправления.
- [ ] Коммит `docs: recording and system load`.
