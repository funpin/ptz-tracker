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
hdiutil create -volname "PTZ Tracker ${VERSION}" -srcfolder "$STAGE" -fs HFS+ -format UDZO -imagekey zlib-level=9 -ov "$OUT" >/dev/null
echo "$OUT"
