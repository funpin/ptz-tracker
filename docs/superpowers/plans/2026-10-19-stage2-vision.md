# PTZ Tracker — Этап 2: распознавание людей и выбор цели кликом

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** На превью вокруг каждого человека появляется рамка (по желанию и скелет). Оператор кликает по человеку, и программа устойчиво держит его как «цель»: не путает с проходящими мимо и узнаёт после короткого перекрытия. В строке статуса видно, сколько ресурсов уходит на распознавание. Камеру на этом этапе программа сама ещё не двигает, это этап 3.

**Architecture:**
- **Распознавание.** `Detector` (интерфейс) находит людей с 17 ключевыми точками (COCO). Есть две реализации:
  - `PoseDetector` — нейросеть YOLO-pose в ONNX через onnxruntime (DirectML на Windows, CPU на Mac);
  - `SimDetector` — точная разметка от симулятора, чтобы тестировать логику без нейросети.
- **Сопровождение.** `PersonTracker` связывает людей между кадрами: присваивает каждому ID и держит выбранную цель, при коротких перекрытиях узнаёт её по внешности.
- **Фоновый анализ.** `Analyzer` — отдельный поток внутри `CameraSession`. Он берёт свежий кадр, распознаёт людей, обновляет сопровождение и публикует `AnalysisResult`.
- **Интерфейс.** Только читает `AnalysisResult` и передаёт клики в `Analyzer`.

**Tech Stack:** как на этапе 1, плюс `onnxruntime-directml` (Windows) / `onnxruntime` (macOS). Для одноразового экспорта модели — `ultralytics` во временном окружении; в зависимости программы он не входит.

**Spec:** `docs/superpowers/specs/2026-10-19-ptz-tracker-design.md` (§5 «Нейросеть», «Модель», «Трекинг ID»; §6 `vision/*`; §10 превью с рамками и клик; §13 этап 2; §14 лицензия модели; §7 «Реальная сцена»)

## Global Constraints

- Всё из этапа 1 остаётся в силе: Windows 10, Python 3.12 (разработка на 3.13), русский интерфейс, CUDA не нужна.
- Модель: **YOLO11n-pose**, вход 640×640, ONNX, лежит в пакете: `src/ptztracker/models/yolo11n-pose.onnx`. Лицензия AGPL-3.0 — для внутреннего использования (§14 спеки). Детектор спрятан за интерфейсом и заменяем.
- Провайдеры onnxruntime по порядку: `DmlExecutionProvider` → `CPUExecutionProvider`. Если GPU недоступен, работаем на CPU, а не падаем.
- Частота анализа — не больше **15 Гц** (параметр). Плавность даёт регулятор (этап 3), а не частота детекции. Анализ работает в своём потоке; UI-поток инференс не выполняет никогда.
- Пороги уверенности (ByteTrack-подход, по реальному кадру с площадки — докладчик за кафедрой на общем плане даёт 0.29): детектор отдаёт всё от **0.10**; новый трек создаётся от **0.25**; существующий трек продолжается детекциями от 0.10. NMS IoU 0.5.
- Ключевые точки — COCO-17: 0 нос, 5/6 плечи, 11/12 бёдра, 15/16 щиколотки.
- Реальная сцена (§7 спеки): тёмная сцена, яркий экран, кафедра, головы зрителей внизу кадра. Люди внизу не должны мешать выбору цели.
- **Основная рабочая крупность — крупно, по грудь / по пояс** (§4 спеки): человек занимает 80–90% высоты кадра, бёдер не видно. Всё в этом этапе (детекция, рамка, внешность, узнавание) обязано работать без бёдер и ног. Основа — голова и плечи.

## Review Focus

1. **Модель не загрузилась**: нет файла, сбой DirectML на старом драйвере. Ручное управление и видео работают как раньше, в статусе — причина («Распознавание недоступно: …»). При сбое DirectML автоматически пробуется CPU. Тест в Task 2 и Task 5.
2. **Анализ медленнее видео** (только CPU, 150+ мс на кадр). Окно не подтормаживает, анализ пропускает кадры, рамки обновляются с той частотой, какая получается. Тест в Task 5.
3. **Людей нет или их много** (зрители внизу). Ничего не падает; клик по пустому месту цель не выбирает и не сбрасывает; клик выбирает самую маленькую рамку под курсором. Тест в Task 4.
4. **Цель ненадолго перекрыта** (кафедра, кто-то прошёл мимо) — в том числе на рабочей крупности, где человек обрезан по пояс и занимает почти весь кадр. ID держится до 2 с. Цель не перескакивает на другого человека рядом с непохожей внешностью; на похожего по внешности — переносится. Тест в Task 4.
5. **Смена камеры или закрытие во время анализа.** `Analyzer` останавливается без исключений, новая сессия запускает свой анализ. Тест в Task 5.

---

## Структура файлов

```
tools/export_pose_model.py                — одноразовый экспорт YOLO11n-pose → ONNX (не входит в программу)
src/ptztracker/models/yolo11n-pose.onnx   — модель (в git, ~12 МБ)
src/ptztracker/vision/__init__.py
src/ptztracker/vision/types.py            — Person, Detector (Protocol), KP_* константы
src/ptztracker/vision/pose_detector.py    — PoseDetector, letterbox, decode_yolo_pose, choose_providers
src/ptztracker/vision/sim_detector.py     — SimDetector (истина из Frame.meta)
src/ptztracker/vision/tracker.py          — Track, PersonTracker, torso_histogram
src/ptztracker/vision/analyzer.py         — AnalysisResult, Analyzer
tests/fixtures/people.jpg                 — фото с несколькими людьми (из пакета ultralytics, bus.jpg)
tests/vision/…
```
Изменяются: `video/frames.py` (поле `Frame.meta`), `sim/scene.py` и `sim/frame_source.py` (истинные ключевые точки), `camera/session.py` (анализ), `ui/preview.py` (рамки, клик), `ui/main_window.py` (панель цели, статус), `pyproject.toml`, `packaging/ptztracker.spec`, `.gitignore`, `README.md`.

---

### Task 0: Выбор потока видео и задержка по каждому потоку (добавлено по просьбе пользователя)

Пользователь включил в камере два потока: H.264(1) 1280×720p25 4 Мбит/с и H.264(2) 640×360p25 1 Мбит/с. Нужно выбирать поток в программе и измерить, у какого меньше задержка.

**Files:**
- Modify: `src/ptztracker/config.py`, `src/ptztracker/video/rtsp.py`, `src/ptztracker/camera/session.py`, `src/ptztracker/ui/main_window.py`, `src/ptztracker/diagnostics/checks.py`, `src/ptztracker/diagnostics/runner.py`
- Test: `tests/video/test_rtsp.py`, `tests/test_config.py`, `tests/ui/test_main_window.py`, `tests/diagnostics/test_runner_sim.py`

**Interfaces:**
- `CameraConfig.stream: str = "auto"`, одно из `"auto" | "h264_1" | "h264_2" | "mjpeg"`. Неизвестное значение при загрузке заменяется на `"auto"`.
- `STREAM_CHOICES: dict[str, str]` = {"auto": "Авто", "h264_1": "H.264 (1)", "h264_2": "H.264 (2)", "mjpeg": "JPEG (MJPEG)"}.
- `stream_url(host, stream) -> str` для конкретного потока:
  - `h264_1` → `rtsp://{host}/MediaInput/h264/stream_1`;
  - `h264_2` → `rtsp://{host}/MediaInput/h264/stream_2`;
  - `mjpeg` → `http://{host}/cgi-bin/mjpeg?resolution=640x360&framerate=25`.
- `stream_urls(host, stream, username, password, saved_url) -> list[str]`:
  - `auto` — как сейчас: сначала сохранённый адрес, затем кандидаты;
  - конкретный поток — ровно один адрес, без перебора. Иначе выбор оператора молча подменялся бы другим потоком.
- `RtspSource.urls -> list[str]` (только чтение).
- `CameraSession` использует `stream_urls`. Найденный адрес запоминается только в режиме `auto`.
- `MainWindow`: в группе «Камера» выпадающий список «Видео» (`self.stream_combo`) с вариантами `STREAM_CHOICES`. Смена значения сохраняется в камеру и перезапускает сессию. Для симулятора список неактивен.
- Диагностика:
  - `check_rtsp` пробует также `stream_url(host, "h264_2")` и оставляет открытыми до трёх работающих потоков в `st["sources"]` (список `(url, source)`); `st["source"]` — первый из них.
  - `check_command_latency` меряет задержку на каждом открытом потоке (по 3 замера). `details.per_stream = [{url, width, height, median_ms, samples_ms}]`, `details.median_ms` — по первому потоку (совместимость), `details.fastest` — url с минимальной медианой.
  - Порог движения масштабируется по ширине кадра: `1.5 * width / 1280` px.
  - В report.txt — строка на каждый поток: «Задержка: <url> (<w>×<h>) ~N мс», плюс «Быстрее всего: …».

- [ ] **Step 1: Failing tests:**
```python
def test_stream_urls_specific_and_auto():
    assert stream_urls("h", "h264_2", "admin", "pw", None) == ["rtsp://admin:pw@h/MediaInput/h264/stream_2"]
    assert stream_urls("h", "mjpeg", None, None, None) == ["http://h/cgi-bin/mjpeg?resolution=640x360&framerate=25"]
    a = stream_urls("h", "auto", None, None, "rtsp://h/saved"); assert a[0] == "rtsp://h/saved" and len(a) > 1
def test_config_stream_roundtrip_and_invalid(tmp_path): ...  # "h264_2" сохраняется; "zzz" → "auto"
def test_stream_combo_saves_and_restarts(qtbot, tmp_path):
    # MainWindow без симулятора; выбрать «H.264 (2)» → config stream == "h264_2", session.frames.urls == [".../stream_2"]
def test_diagnostics_latency_per_stream(tmp_path, sim_env):
    # per_stream ≥ 2 записей, у каждой median_ms; fastest — один из url; report.txt содержит «Быстрее всего»
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS + весь набор.
- [ ] **Step 5: Commit** `feat: choose camera video stream; diagnostics measure latency per stream`

---

### Task 1: Модель позы в ONNX и зависимости

**Files:**
- Create: `tools/export_pose_model.py`, `src/ptztracker/models/__init__.py`, `src/ptztracker/models/yolo11n-pose.onnx`, `tests/fixtures/people.jpg`
- Modify: `pyproject.toml`, `.gitignore`
- Test: `tests/vision/test_model_file.py`

**Interfaces:**
- Produces: `ptztracker.models.POSE_MODEL_PATH: Path`, путь к ONNX внутри пакета (в PyInstaller-сборке — внутри `_internal`).

- [ ] **Step 1: Failing test** `tests/vision/test_model_file.py`:
```python
def test_model_present_and_shapes():
    import onnxruntime as ort
    from ptztracker.models import POSE_MODEL_PATH
    s = ort.InferenceSession(str(POSE_MODEL_PATH), providers=["CPUExecutionProvider"])
    assert s.get_inputs()[0].shape == [1, 3, 640, 640]
    assert s.get_outputs()[0].shape == [1, 56, 8400]
```
- [ ] **Step 2: Run** `.venv/bin/pytest tests/vision/test_model_file.py -q`. Ожидается FAIL (нет onnxruntime и модели).
- [ ] **Step 3:**
  - В `pyproject.toml` добавить в зависимости `onnxruntime-directml>=1.20; sys_platform == "win32"` и `onnxruntime>=1.20; sys_platform != "win32"`, а также package-data `ptztracker = ["models/*.onnx"]`.
  - Из `.gitignore` убрать строку `models/*.onnx`.
  - Написать `tools/export_pose_model.py`: во временном venv (`pip install ultralytics==8.3.*`) выполняется `YOLO("yolo11n-pose.pt").export(format="onnx", imgsz=640, opset=17, simplify=True, dynamic=False)`, результат копируется в `src/ptztracker/models/`. Там же из `ultralytics/assets/bus.jpg` копируется `tests/fixtures/people.jpg`.
  - Запустить скрипт.
  - `models/__init__.py`: `POSE_MODEL_PATH = Path(__file__).with_name("yolo11n-pose.onnx")`.
  - `pip install -e ".[dev]"`.
- [ ] **Step 4: Run.** PASS.
- [ ] **Step 5: Commit** `feat: bundle YOLO11n-pose ONNX model and onnxruntime`

---

### Task 2: PoseDetector

**Files:**
- Create: `src/ptztracker/vision/__init__.py`, `vision/types.py`, `vision/pose_detector.py`
- Test: `tests/vision/test_pose_detector.py`

**Interfaces:**
- Consumes: `POSE_MODEL_PATH` (Task 1), `Frame` (этап 1).
- Produces:
  - `@dataclass(frozen=True) Person(box: tuple[float,float,float,float], score: float, keypoints: np.ndarray)`: `box` — x1,y1,x2,y2 в пикселях кадра, `keypoints` — `(17,3)` x,y,conf в пикселях кадра.
  - Константы `KP_NOSE=0, KP_L_SHOULDER=5, KP_R_SHOULDER=6, KP_L_HIP=11, KP_R_HIP=12, KP_L_ANKLE=15, KP_R_ANKLE=16`.
  - `class Detector(Protocol)`: `name: str`, `device: str`, `detect(frame: Frame) -> list[Person]`.
  - `choose_providers(available: list[str]) -> list[str]`: `["DmlExecutionProvider","CPUExecutionProvider"]`, урезанный до того, что есть в `available`; CPU всегда последний.
  - `letterbox(img, size=640) -> tuple[np.ndarray, float, tuple[int,int]]`: возвращает (тензор `1×3×640×640` float32 0..1 RGB, масштаб, (pad_x, pad_y)); заполнение 114.
  - `decode_yolo_pose(out: np.ndarray, scale: float, pad: tuple[int,int], conf: float=0.35, iou: float=0.5) -> list[Person]`: на входе `out` формы `(1,56,N)`; строки 0–3 — cx,cy,w,h, строка 4 — score, строки 5–55 — 17×(x,y,v). Применяется `cv2.dnn.NMSBoxes`, координаты переводятся в пиксели кадра; результат сортируется по score по убыванию.
  - `PoseDetector(model_path: Path = POSE_MODEL_PATH, conf=0.10, iou=0.5, providers: list[str]|None=None)`: `.name = "Нейросеть"`; `.device` — `"DirectML"` или `"CPU"` (что фактически получила сессия). Если создание сессии с DML бросает исключение, повторить только с CPU. Если и это не удалось — `RuntimeError` с понятным текстом.
  - `PoseDetector.detect(frame) -> list[Person]`.
- [ ] **Step 1: Failing tests:**
```python
def test_choose_providers():
    assert choose_providers(["CPUExecutionProvider","DmlExecutionProvider"]) == ["DmlExecutionProvider","CPUExecutionProvider"]
    assert choose_providers(["CPUExecutionProvider"]) == ["CPUExecutionProvider"]
def test_letterbox_shape_and_scale():
    t, s, (px, py) = letterbox(np.zeros((720,1280,3),np.uint8))
    assert t.shape == (1,3,640,640) and t.dtype == np.float32 and abs(s - 0.5) < 1e-6 and (px, py) == (0, 140)
def test_decode_maps_back_to_frame():
    out = np.zeros((1,56,3),np.float32)
    out[0,:5,0] = [320, 320, 100, 200, 0.9]           # в letterbox-координатах
    out[0,5+5*3:5+5*3+3,0] = [300, 260, 0.9]           # левое плечо
    p = decode_yolo_pose(out, scale=0.5, pad=(0,140))
    assert len(p) == 1
    x1,y1,x2,y2 = p[0].box
    assert (round(x1), round(y1), round(x2), round(y2)) == (540, 160, 740, 560)
    assert tuple(np.round(p[0].keypoints[5,:2])) == (600, 240)
def test_decode_nms_and_threshold():
    out = np.zeros((1,56,3),np.float32)
    out[0,:5,0] = [320,320,100,200,0.9]; out[0,:5,1] = [322,321,100,200,0.8]; out[0,:5,2] = [100,100,50,50,0.2]
    p = decode_yolo_pose(out, 1.0, (0,0))
    assert len(p) == 1 and abs(p[0].score - 0.9) < 1e-6
def test_detects_people_on_photo():
    img = cv2.imread("tests/fixtures/people.jpg"); d = PoseDetector()
    ppl = d.detect(Frame(img, 0.0, 1))
    assert len(ppl) >= 3 and all(p.keypoints.shape == (17,3) for p in ppl)
    h, w = img.shape[:2]; assert all(0 <= p.box[0] < p.box[2] <= w + 1 and 0 <= p.box[1] < p.box[3] <= h + 1 for p in ppl)
def test_detects_person_cut_at_waist():   # рабочая крупность: обрезан по пояс, занимает почти весь кадр
    img = cv2.imread("tests/fixtures/people.jpg"); ppl = PoseDetector().detect(Frame(img, 0.0, 1))
    x1,y1,x2,y2 = map(int, max(ppl, key=lambda p: (p.box[2]-p.box[0])*(p.box[3]-p.box[1])).box)
    mid = y1 + int((y2 - y1) * 0.55)                    # отрезать ниже пояса
    crop = cv2.resize(img[max(0,y1-20):mid, max(0,x1-60):min(img.shape[1],x2+60)], None, fx=2.5, fy=2.5)
    got = PoseDetector().detect(Frame(crop, 0.0, 1))
    assert got and got[0].keypoints[KP_L_SHOULDER,2] > 0.3 and got[0].keypoints[KP_R_SHOULDER,2] > 0.3
def test_no_people_on_blank():
    assert PoseDetector().detect(Frame(np.zeros((720,1280,3),np.uint8), 0.0, 1)) == []
def test_falls_back_to_cpu_when_dml_fails(monkeypatch):   # Review Focus 1
    # monkeypatch ort.InferenceSession: при providers с Dml бросить RuntimeError, иначе — настоящая сессия
    assert PoseDetector(providers=["DmlExecutionProvider","CPUExecutionProvider"]).device == "CPU"
def test_missing_model_clear_error(tmp_path):
    with pytest.raises(RuntimeError, match="модел"): PoseDetector(model_path=tmp_path/"nope.onnx")
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Commit** `feat: YOLO pose detector with DirectML/CPU fallback`

---

### Task 3: Истинная разметка в симуляторе и SimDetector

**Files:**
- Modify: `src/ptztracker/video/frames.py`, `src/ptztracker/sim/scene.py`, `src/ptztracker/sim/frame_source.py`
- Create: `src/ptztracker/vision/sim_detector.py`
- Test: `tests/vision/test_sim_detector.py`

**Interfaces:**
- Consumes: `Person`, `KP_*` (Task 2).
- Produces:
  - `Frame.meta: dict | None = None` — новое последнее поле со значением по умолчанию, существующие вызовы не меняются.
  - `SyntheticStage.render(...)` по-прежнему возвращает кадр. Новое: `SyntheticStage.person_keypoints(t: float, m: np.ndarray) -> np.ndarray` — `(17,3)` в координатах кадра для матрицы сцена→кадр `m`; заполнены нос, плечи, бёдра, колени, щиколотки (conf 1.0), остальные точки с conf 0.
  - `warp_view` уже возвращает `m` — `SimFrameSource` кладёт в `Frame.meta = {"people": [kp_array]}`, если у сцены есть `person_keypoints`, иначе `meta=None`.
  - `SimDetector()`: `.name = "Симулятор"`, `.device = "—"`, `detect(frame)` строит `Person` из `frame.meta["people"]`: рамка — по видимым точкам с полями 15% по бокам и 10% по высоте, обрезается кадром; если человек целиком вне кадра — его нет.
- [ ] **Step 1: Failing tests:**
```python
def test_sim_frame_has_truth_and_detector_finds_person():
    sim = SimPtz(); sim.tilt_deg = -8.0
    fs = SimFrameSource(sim, SyntheticStage(), latency_s=0.0); fs.start(); time.sleep(0.3)
    fr = fs.latest(); fs.stop()
    ppl = SimDetector().detect(fr)
    assert len(ppl) == 1
    kp = ppl[0].keypoints
    assert kp[KP_L_SHOULDER,1] < kp[KP_L_HIP,1] < kp[KP_L_ANKLE,1]     # плечи выше бёдер выше щиколоток
    x1,y1,x2,y2 = ppl[0].box; assert x1 < kp[KP_NOSE,0] < x2
def test_sim_person_out_of_view_not_detected():
    sim = SimPtz(); sim.pan_deg = -50  # смотрим далеко влево от докладчика
    … → SimDetector().detect(fr) == []
def test_frame_meta_default_none():
    assert Frame(np.zeros((2,2,3),np.uint8), 0.0, 1).meta is None
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS, плюс весь набор этапа 1 зелёный.
- [ ] **Step 5: Commit** `feat: ground-truth keypoints in simulator and SimDetector`

---

### Task 4: PersonTracker — ID, выбор цели, узнавание по внешности

**Files:**
- Create: `src/ptztracker/vision/tracker.py`
- Test: `tests/vision/test_tracker.py`

**Interfaces:**
- Consumes: `Person`, `KP_*`.
- Produces:
  - `torso_histogram(image: np.ndarray, p: Person) -> np.ndarray | None`: HSV-гистограмма H16×S8 (нормированная, float32) области торса.
    - Плечи и бёдра видны (conf ≥ 0.3): четырёхугольник плечи–бёдра.
    - Видны только плечи (рабочая крупность): прямоугольник от линии плеч вниз на 1,2 ширины плеч, обрезанный рамкой и кадром.
    - Иначе: средняя треть рамки по ширине и 20–55% по высоте.
    - Если область меньше 8×8 px — `None`.
  - `appearance_distance(a, b) -> float`: `cv2.compareHist(..., HISTCMP_BHATTACHARYYA)`; 0 — одинаковые, 1 — разные.
  - `@dataclass Track(id: int, box, score, keypoints, last_seen: float, hits: int, misses: int, velocity: tuple[float,float], appearance: np.ndarray|None)`.
  - `PersonTracker(match_iou=0.3, max_missing_s=2.0, reid_max_distance=0.35, appearance_alpha=0.1, new_track_score=0.25)`:
    - `.update(people: list[Person], t: float, image: np.ndarray|None) -> list[Track]`: возвращает треки, увиденные в этом кадре.
    - `.tracks -> list[Track]` — все живые треки, включая временно невидимые.
    - `.select_at(x: float, y: float) -> int|None`: из треков, увиденных в последнем кадре, выбирает самый маленький по площади, чья рамка содержит точку. Цель меняется только при попадании; клик мимо ничего не меняет и возвращает `None`.
    - `.select(track_id: int) -> bool`, `.clear_target() -> None`.
    - `.target_id: int|None`, `.target -> Track|None`, `.target_state -> Literal["none","tracking","lost"]`: `lost`, если цель не видна в последнем кадре.
  - Алгоритм:
    1. Прогноз рамки по `velocity` (EMA сдвига центра, α=0.5).
    2. Жадное сопоставление пар (трек, детекция) по убыванию IoU (≥ `match_iou`). Если в паре участвует **цель** и у трека есть `appearance`, а у детекции — гистограмма с расстоянием > `reid_max_distance`, пара отклоняется.
    3. Несопоставленные детекции со score ≥ `new_track_score` становятся новыми треками; более слабые только продолжают существующие треки (шаг 2 сопоставляет детекции любого score ≥ 0.10).
    4. Несопоставленные треки получают `misses += 1`.
    5. Трек удаляется, если `t - last_seen > max_missing_s`.
    6. **Узнавание цели.** Если цель не сопоставлена в этом кадре, а среди *новых* треков есть такой, чей `appearance` ближе всех к цели и не дальше `reid_max_distance`, цель переносится на него: `target_id` = новый id, старый трек удаляется.
    7. Если трек цели удалён по таймауту, `target_id` остаётся, `target_state="lost"`, а узнавание продолжает работать на каждом кадре. Окончательную потерю и поиск решает этап 4.
    8. `appearance` трека обновляется EMA с `appearance_alpha` только тогда, когда его рамка не пересекается с другими (IoU < 0.1), чтобы не «впитывать» соседа.
- [ ] **Step 1: Failing tests** (люди — синтетические `Person` с цветными «рубашками» на сгенерированном кадре):
```python
def test_ids_persist_while_moving(): # человек идёт 30 кадров по 8 px → один и тот же id
def test_two_people_get_distinct_ids():
def test_select_at_smallest_containing_box_and_miss_keeps_target():  # Review Focus 3
    … tr.select_at(*center_of_small) == small.id; tr.select_at(5, 5) is None; tr.target_id == small.id
def test_brief_occlusion_keeps_id():  # 1.5 с без детекции → тот же id, target_state "lost" → снова "tracking"
def test_target_not_stolen_by_different_looking_neighbour():  # Review Focus 4
    # цель (красная) пропадает, рядом в той же области появляется синий → цель не переносится, state "lost"
def test_target_reacquired_by_appearance_after_timeout():
    # красная цель пропала на 3 с (трек удалён), появилась в другом месте → target_id указывает на новый трек, state "tracking"
def test_torso_histogram_without_hips():   # рабочая крупность
    # Person с плечами (conf 0.9) и бёдрами conf 0 на кадре, где ниже плеч — красная «рубашка» → гистограмма есть
    # и близка к гистограмме чисто красного патча (distance < 0.2)
def test_no_people_no_crash(): assert tr.update([], 0.0, img) == [] and tr.target_state == "none"
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Commit** `feat: person tracker with click selection and appearance re-identification`

---

### Task 5: Analyzer и подключение к CameraSession

**Files:**
- Create: `src/ptztracker/vision/analyzer.py`
- Modify: `src/ptztracker/camera/session.py`
- Test: `tests/vision/test_analyzer.py`, дополнение `tests/camera/test_session.py`

**Interfaces:**
- Consumes: `FrameSource`, `Detector`, `PoseDetector`, `SimDetector`, `PersonTracker`.
- Produces:
  - `@dataclass(frozen=True) AnalysisResult(seq: int, t_capture: float, frame_size: tuple[int,int], tracks: tuple[Track, ...], target_id: int|None, target_state: str, infer_ms: float, fps: float, detector: str, device: str)`. `tracks` — копии, UI может читать их без блокировок.
  - `Analyzer(source: FrameSource, detector_factory: Callable[[], Detector], max_fps: float = 15.0, tracker: PersonTracker|None = None)`:
    - `.start()`, `.stop(timeout: float = 1.0)`, `.is_running() -> bool`;
    - `.latest() -> AnalysisResult|None`;
    - `.select_at(x, y) -> int|None`, `.clear_target()` — потокобезопасны (под замком трекера);
    - `.error: str|None` — текст, если детектор не создался или упал. Тогда поток живёт дальше без анализа, а `latest()` возвращает `None`.
    - Детектор создаётся **внутри потока анализа**, чтобы загрузка модели не тормозила окно.
    - Цикл: взять `source.latest()`; если `seq` не изменился — ждать 5 мс; иначе `detect` → `tracker.update` → опубликовать результат. Между итерациями держать период не меньше `1/max_fps`. Исключение `detect` записывается в `error` и лог, цикл продолжается.
  - `CameraSession(cfg, scene_path=None, analysis: bool = True)`: после старта видео создаёт `self.analyzer`. Фабрика детектора: `SimDetector` для `kind="sim"` с синтетической сценой, иначе `PoseDetector`. В `stop()` анализ останавливается первым (`timeout=0.5`).
- [ ] **Step 1: Failing tests:**
```python
def test_analyzer_on_sim_tracks_and_selects():
    s = CameraSession(CameraConfig(id="s",name="Сим",ip="sim",kind="sim")); s.start()
    try:
        wait_until(lambda: s.analyzer.latest() and s.analyzer.latest().tracks, 3)
        r = s.analyzer.latest(); tr = r.tracks[0]
        cx, cy = (tr.box[0]+tr.box[2])/2, (tr.box[1]+tr.box[3])/2
        assert s.analyzer.select_at(cx, cy) == tr.id
        wait_until(lambda: s.analyzer.latest().target_state == "tracking", 2)
        assert r.detector == "Симулятор" and 0 < r.fps <= 16
    finally: s.stop()
def test_detector_failure_reported_not_fatal():           # Review Focus 1
    def bad(): raise RuntimeError("модель не найдена")
    a = Analyzer(fake_source, bad); a.start(); wait_until(lambda: a.error, 2)
    assert "модель" in a.error and a.latest() is None; a.stop()
def test_slow_detector_does_not_block_and_drops_frames():  # Review Focus 2
    # детектор спит 0.2 с; источник выдаёт 25 к/с → fps анализа ≈ 5, latest().seq растёт скачками; stop() < 1 с
def test_session_stop_stops_analyzer_quickly():           # Review Focus 5
    s = sim session; s.start(); time.sleep(1); t0 = time.monotonic(); s.stop(); assert time.monotonic()-t0 < 1.0
    assert not s.analyzer.is_running()
def test_pose_detector_on_photo_through_analyzer():  # реальная модель через анализатор (источник отдаёт people.jpg)
```
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Commit** `feat: background analyzer wired into camera session`

---

### Task 6: Интерфейс — рамки, клик по человеку, панель цели, статус

**Files:**
- Modify: `src/ptztracker/ui/preview.py`, `src/ptztracker/ui/main_window.py`
- Test: `tests/ui/test_preview_overlay.py`, дополнение `tests/ui/test_main_window.py`

**Interfaces:**
- Consumes: `Analyzer`, `AnalysisResult`, `CameraSession.analyzer`.
- Produces:
  - `PreviewWidget.set_analyzer(analyzer: Analyzer|None)`, `.show_skeleton: bool` (по умолчанию False), сигнал `targetClicked(int)` (id выбранной цели).
  - `PreviewWidget.widget_to_frame(pt: QPointF) -> tuple[float,float]|None`: преобразование из координат виджета в координаты кадра с учётом вписывания; клик по полям вне картинки даёт `None`.
  - Отрисовка поверх кадра по последнему `AnalysisResult`:
    - **цель** — синяя рамка 3 px и подпись «ВЕДУ». На рабочей крупности рамка почти во весь кадр, поэтому подпись рисуется **внутри** рамки у верхнего левого угла на полупрозрачной плашке и не уходит за край кадра (или «ЦЕЛЬ ПОТЕРЯНА» оранжевым в состоянии `lost`, в последнем известном месте);
    - остальные — тонкая пунктирная белая рамка;
    - при `show_skeleton` — линии плечи–бёдра–колени–щиколотки и точка носа.
  - Левый клик по превью: `widget_to_frame` → `analyzer.select_at`; при успехе испускается `targetClicked`. Курсор над рамкой — «рука».
  - Панель «Цель» в `MainWindow` (над «Камера»):
    - надпись «Цель: не выбрана — кликните по человеку на видео» / «Цель: №{id} — веду» / «Цель: №{id} — потеряна»;
    - кнопка «Сбросить цель»;
    - флажок «Показывать скелет».
  - Строка статуса добавляет «анализ {fps:.0f} к/с · {infer_ms:.0f} мс · {device}» или «Распознавание недоступно: {error}».
  - Esc по-прежнему только аварийный стоп; цель не сбрасывает.
- [ ] **Step 1: Failing tests:**
```python
def test_widget_to_frame_letterbox(qtbot):   # виджет 1000×1000, кадр 1280×720 → поля сверху/снизу; клик в поле → None
def test_click_on_person_selects_target(qtbot, tmp_path):
    w = MainWindow(ConfigStore(tmp_path/"c.json"), use_sim=True); show; wait until analyzer has a track
    tr = w.session.analyzer.latest().tracks[0]; pt = w.preview.frame_to_widget((tr.box[0]+tr.box[2])/2, (tr.box[1]+tr.box[3])/2)
    qtbot.mouseClick(w.preview, Qt.LeftButton, pos=pt.toPoint())
    qtbot.waitUntil(lambda: "веду" in w.target_label.text(), timeout=3000)
def test_reset_target_button(qtbot, tmp_path):   # после выбора → «Сбросить цель» → «не выбрана»
def test_overlay_paints_without_errors(qtbot, tmp_path):  # w.preview.grab() с включённым скелетом не падает
def test_status_shows_analysis(qtbot, tmp_path):   # в статусе появляется «анализ» и «Симулятор»
```
`PreviewWidget.frame_to_widget(x, y) -> QPointF` — обратное к `widget_to_frame`, используется в тестах и при отрисовке.
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [ ] **Step 5: Ручная проверка на Mac:** `.venv/bin/python -m ptztracker --sim`. Видна рамка вокруг человека, клик даёт «ВЕДУ». Камера двигается джойстиком, а рамка «едет» вместе с человеком.
- [ ] **Step 6: Commit** `feat: person boxes on preview, click to select target`

---

### Task 7: Сборка, README, проверка на реальном кадре

**Files:**
- Modify: `packaging/ptztracker.spec`, `README.md`, `tests/test_packaging.py`

**Interfaces:**
- Consumes: всё выше.
- Produces:
  - spec: `collect_all("onnxruntime")` (на Windows пакет называется так же, `onnxruntime`, но с DirectML внутри) плюс модель в datas (`("../src/ptztracker/models/yolo11n-pose.onnx", "ptztracker/models")`).
  - README: раздел «Выбор человека» (кликните по человеку → «ВЕДУ»; «Сбросить цель»; «Показывать скелет»; что значит «анализ N к/с · M мс · DirectML/CPU» и как проверить нагрузку рядом с vMix/OBS — посмотреть, не растут ли пропуски кадров в vMix/OBS при включённой программе). Отдельной строкой — лицензия модели (AGPL-3.0, внутреннее использование).
- [ ] **Step 1: Failing test:** `test_spec_bundles_model_and_onnxruntime` — в spec есть `yolo11n-pose.onnx` и `onnxruntime`; README содержит «Выбор человека».
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS + весь набор.
- [ ] **Step 5: Проверка сборки на Mac:** `pyinstaller packaging/ptztracker.spec --noconfirm`; собранный `PTZTracker --sim` 8 с без ошибок; в логе нет ошибок загрузки модели.
- [ ] **Step 6: Локальная проверка на реальных кадрах пользователя** (вне тестов, файл не коммитится): прогнать `PoseDetector` по `~/Downloads/ptz-diag-10_0_0_12-20261019-161151/frames/2.jpg`. Докладчик у кафедры должен найтись (score ≥ 0.5, плечи с conf ≥ 0.3). Затем то же на кадре рабочей крупности: вырезать из этого кадра область докладчика по пояс и увеличить до 1280×720. Он должен найтись, и плечи должны быть с conf ≥ 0.3. Записать в ledger время инференса на CPU Mac и число найденных людей.
- [ ] **Step 7: Commit** `build: bundle pose model and onnxruntime; README for target selection`

---

## После этапа 2

1. Пользователь пересобирает `.exe`, открывает камеру, кликает по докладчику и проверяет устойчивость рамки «ВЕДУ» во время доклада. Сообщает цифры «анализ … к/с · … мс · DirectML» при работающем vMix/OBS.
2. Замер MJPEG (обещан пользователем) решает, какой поток использовать для анализа на этапе 3.
3. План этапа 3: регулятор движения, композиции, зоны, профиль усилия, мастер калибровки с плотной сеткой кодов 56–70.
