#!/bin/bash
# Запуск PTZ Tracker на Mac двойным щелчком. Первый раз создаёт .venv и ставит зависимости.
cd "$(dirname "$0")" || exit 1

# нужен Python 3.12 или 3.13 (в таком порядке пробуем)
find_python() {
    for c in python3.13 python3.12 python3; do
        if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3, 12) <= sys.version_info[:2] < (3, 14) else 1)' 2>/dev/null; then
            echo "$c"
            return 0
        fi
    done
    return 1
}

if [ ! -x .venv/bin/python ]; then
    PY=$(find_python) || {
        echo "Не найден Python 3.12 или 3.13. Установите его с https://www.python.org/downloads/ и запустите файл снова."
        read -r -p "Нажмите Enter, чтобы закрыть…" _
        exit 1
    }
    rm -rf .venv  # недостроенная папка мешает создать новую
    "$PY" -m venv .venv && .venv/bin/pip install -e . || {
        echo "Не удалось подготовить окружение. Пришлите разработчику текст выше."
        read -r -p "Нажмите Enter, чтобы закрыть…" _
        exit 1
    }
fi
exec .venv/bin/python -m ptztracker "$@"
