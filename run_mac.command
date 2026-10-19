#!/bin/bash
# Запуск PTZ Tracker на Mac двойным щелчком. Первый раз создаёт .venv и ставит зависимости.
cd "$(dirname "$0")" || exit 1
if [ ! -d .venv ]; then
    python3 -m venv .venv && .venv/bin/pip install -e . || exit 1
fi
exec .venv/bin/python -m ptztracker "$@"
