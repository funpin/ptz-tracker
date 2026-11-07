#!/bin/bash
# DMG для macOS: окно «перетащите PTZ Tracker в Программы».
# Использование: packaging/macos/make_dmg.sh 0.3.0   (после pyinstaller packaging/ptztracker.spec)
# Обновление — перетащить новую версию с заменой: настройки лежат в ~/Library и не трогаются.
set -euo pipefail
VERSION="${1:?версия, например 0.3.0}"
cd "$(dirname "$0")/../.."
APP="dist/PTZ Tracker.app"
OUT="dist/PTZTracker-${VERSION}-macos-arm64.dmg"
[ -d "$APP" ] || { echo "Нет $APP — сначала pyinstaller packaging/ptztracker.spec"; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/PTZ Tracker.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$OUT"
# hdiutil на машинах GitHub иногда отвечает «Resource busy» (образ держат службы macOS) — повторить через паузу
for attempt in 1 2 3 4 5; do
  if hdiutil create -volname "PTZ Tracker ${VERSION}" -srcfolder "$STAGE" -fs HFS+ -format UDZO -imagekey zlib-level=9 -ov "$OUT" >/dev/null; then
    echo "$OUT"
    exit 0
  fi
  echo "hdiutil не создал образ (попытка $attempt из 5), повтор через 10 с" >&2
  sleep 10
done
exit 1
