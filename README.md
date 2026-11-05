<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/assets/logo-mark-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="./docs/assets/logo-mark-light.svg">
    <img src="./docs/assets/logo-mark-light.svg" alt="Логотип PTZ Tracker" width="140">
  </picture>
</p>

<h1 align="center">PTZ Tracker</h1>

<p align="center">
  <strong>Автоматическое ведение докладчика PTZ-камерой Panasonic AW-HE42.</strong>
</p>

<p align="center">
  <a href="https://github.com/funpin/ptz-tracker/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/funpin/ptz-tracker?style=flat-square&amp;label=release"></a>
  <a href="https://github.com/funpin/ptz-tracker/actions/workflows/windows-build.yml"><img alt="Build and tests" src="https://github.com/funpin/ptz-tracker/actions/workflows/windows-build.yml/badge.svg?branch=main"></a>
  <img alt="Windows | macOS" src="https://img.shields.io/badge/Windows%20%7C%20macOS-desktop-555555?style=flat-square">
  <img alt="Python 3.12–3.13" src="https://img.shields.io/badge/Python-3.12–3.13-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white">
  <img alt="PySide6" src="https://img.shields.io/badge/PySide6-Qt%206-41CD52?style=flat-square&amp;logo=qt&amp;logoColor=white">
  <img alt="ONNX Runtime" src="https://img.shields.io/badge/ONNX%20Runtime-DirectML%20%7C%20CoreML-005CED?style=flat-square&amp;logo=onnx&amp;logoColor=white">
  <a href="LICENSE"><img alt="License AGPL-3.0" src="https://img.shields.io/badge/license-AGPL--3.0-A42E2B?style=flat-square"></a>
</p>

---

PTZ Tracker находит людей на видео с камеры, оператор кликом выбирает докладчика, и камера сама плавно поворачивается и зумирует, удерживая его в выбранном кадре. Любое ручное действие или Esc мгновенно возвращают камеру оператору. В одном окне — до четырёх камер, геймпады, пресеты со снимками кадров и эфир от ATEM.

## Окно

<p align="center"><img src="./docs/assets/overview.png" alt="Окно PTZ Tracker: две камеры на симуляторе, сводка, положение камер, пресеты" width="100%"></p>

1. **TRACK** — слежение выбранной камеры (Пробел). Квадратики — камеры с анализом: лайм — ведёт, оранжевый — ищет цель; клик включает или выключает слежение той камеры.
2. **Журнал для разбора** — F9 сохраняет последние минуты работы в zip (не видео).
3. **Меню ☰** — общее: камеры, геймпады, ATEM, агент, обновления, справка.
4. **Шапка камеры** — номер клетки (у выбранной — белый) и камера ▾.
5. **Эфир и слежение** — PGM/PVW от ATEM; ВЕДУ, ИЩУ, ВЫКЛ, ОЖИДАНИЕ, БЕЗ АНАЛИЗА; СИМУЛЯТОР.
6. **Питание ⏻ и меню ⋯** — режим ожидания; быстрые настройки, поток, калибровка, диагностика. Здесь же — геймпады этой камеры с зарядом.
7. **Видео** — разрешение, кадры в секунду и скорость анализа.
8. **Сводка** — эфир, слежение, геймпады и калибровка всех камер (в свободной клетке).
9. **Положение камер** — куда смотрит каждая камера, насколько крупно и где её пресеты.
10. **Кадр** — пояс, рост, общий (клавиши 1–3) и «Мягко ↔ Резко».
11. **Ручное управление** — стик, зум, чувствительность, «Медленнее на крупном плане».
12. **Изображение** — ирис, gain, фокус, баланс белого; слежение не выключают. Редкое — затвор с Synchro, уровень чёрного, резкость и вход в меню Panasonic на камере — в **быстрых настройках** поверх видео (M или Options на геймпаде).
13. **СТОП КАМЕРЫ** — Esc, останавливает все камеры.
14. **Пресеты** — снимки кадров (P) и скорость переезда; потяните вверх — пресеты других камер.
15. **Справка** — F1: рисунки геймпада, клавиатуры и мыши с подсветкой.

Картинку пересобирает `python scripts/make_overview.py`; внешний вид — по [брендбуку](docs/BRANDBOOK.md).

Как устроено:

- **Камера.** Команды Panasonic по HTTP через очередь с приоритетами: аварийный стоп никогда не теряется.
- **Видео.** MJPEG/RTSP через PyAV. Для слежения нужен MJPEG: от команды до движения в кадре у него 344 мс, у H.264 — ~830 мс.
- **Распознавание.** YOLO11n-pose в ONNX Runtime: DirectML на Windows, CoreML на Mac, при сбое — процессор. Сопровождение цели — по образцу ByteTrack.
- **Регулятор.** Учитывает задержку видео и инерцию камеры, держит мёртвую и мягкую зоны и ограничивает ускорение. Отдельный сторож останавливает камеру, если её раскачивает.
- **Чёрный ящик.** Программа помнит последние 5 минут работы, F9 сохраняет их в zip. Эпизод повторяется без камеры — через регулятор и через распознавание.
- **Симулятор.** Виртуальная AW-HE42 с той же HTTP-командой и динамикой по замерам живой камеры — на ней проверяется всё.
- **Tally ATEM.** PGM/PVW в шапке каждой камеры и рамка вокруг видео; только показывает эфир.
- **Агент.** Локальный API (`127.0.0.1`) и команды `--lab` для Claude Code. Двигать камеру агент может только с двумя флажками оператора.

## Быстрый старт

Готовые сборки — на странице [последнего релиза](https://github.com/funpin/ptz-tracker/releases/latest): для **Windows** — установщик (новая версия ставится поверх старой) или portable-zip, для **macOS (Apple Silicon)** — DMG. Настройки при обновлении сохраняются. Как открыть неподписанное приложение и что делать дальше — в заметках к релизу и в [руководстве оператора](docs/OPERATOR.md).

Из исходников: `build.bat` собирает `.exe` на Windows; на Mac двойной щелчок по `run_mac.command` сам создаст окружение и запустит программу.

**Разработка** (Python 3.12–3.13):

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m ptztracker --sim        # окно с виртуальной камерой
.venv/bin/python -m ptztracker --replay запись.zip --view   # просмотр эпизода
```

## Проверки

- `.venv/bin/pytest -q -m "not slow"` — быстрый набор (~11 мин): протокол, планировщик, модели, регулятор в виртуальном времени, окна на offscreen-Qt;
- `.venv/bin/pytest -q` — полный набор (дольше): замкнутый контур, слежение и калибровка на симуляторе в реальном времени;
- `.venv/bin/ruff check src tests`.

CI на Windows прогоняет линтер и быстрый набор и собирает `.exe` (PyInstaller, onedir).

## Документация

- [руководство оператора](docs/OPERATOR.md) и [брендбук](docs/BRANDBOOK.md);
- [архитектура и инварианты безопасности](docs/ARCHITECTURE.md);
- [решения и замеры, на которых они стоят](docs/DECISIONS.md);
- [бэклог и открытые вопросы](docs/BACKLOG.md);
- [изменения по версиям](CHANGELOG.md) и [как выпускать релизы](docs/RELEASING.md);
- [исходный дизайн и факты живой камеры](docs/superpowers/specs/2026-10-19-ptz-tracker-design.md);
- [планы этапов](docs/superpowers/plans/);
- [правила для агента](AGENTS.md) и [правила участия](CONTRIBUTING.md).

## Лицензия

PTZ Tracker распространяется под [GNU AGPL-3.0](LICENSE). Вы можете свободно использовать, изучать, менять и распространять программу, если ваши изменения и производные работы — в том числе доступные пользователям по сети — тоже открыты под AGPL-3.0.

В программу входит модель распознавания YOLO11n-pose ([Ultralytics](https://github.com/ultralytics/ultralytics), AGPL-3.0) — `src/ptztracker/models/yolo11n-pose.onnx`. Если нужна другая лицензия, модель можно заменить моделью под Apache-2.0 (RTMO / RTMDet + RTMPose): меняется только `vision/pose_detector.py`.

© 2026 funpin
