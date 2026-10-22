#!/bin/bash
# Запуск PTZ Tracker на Mac двойным щелчком. Первый раз создаёт .venv и ставит зависимости.
cd "$(dirname "$0")" || exit 1

# нужен Python 3.12 или 3.13
PY_OK='import sys; sys.exit(0 if (3, 12) <= sys.version_info[:2] < (3, 14) else 1)'

find_python() {
    for c in python3.13 python3.12 python3; do
        if command -v "$c" >/dev/null 2>&1 && "$c" -c "$PY_OK" 2>/dev/null; then
            echo "$c"
            return 0
        fi
    done
    return 1
}

# окружение готово, только если установка дошла до конца (.ready) и Python в нём подходящий
venv_ready() {
    [ -f .venv/.ready ] && [ -x .venv/bin/python ] && .venv/bin/python -c "$PY_OK" 2>/dev/null
}

fail() {
    echo "$1"
    read -r -p "Нажмите Enter, чтобы закрыть…" _
    exit 1
}

if ! venv_ready; then
    PY=$(find_python) || fail "Не найден Python 3.12 или 3.13. Установите его с https://www.python.org/downloads/ и запустите файл снова."
    echo "Готовлю окружение (первый запуск или прошлая установка не закончилась) — это несколько минут…"
    rm -rf .venv  # недостроенная или старая папка мешает создать новую
    { "$PY" -m venv .venv && .venv/bin/pip install -e . && touch .venv/.ready; } \
        || fail "Не удалось подготовить окружение. Пришлите разработчику текст выше и запустите файл снова."
fi
exec .venv/bin/python -m ptztracker "$@"
