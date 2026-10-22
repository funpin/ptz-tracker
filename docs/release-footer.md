
---

### Как установить

**Windows 10/11 (x64).** Скачайте `PTZTracker-…-windows-x64.zip`, распакуйте в любую папку и запустите `PTZTracker\PTZTracker.exe`. Первый запуск длится 15–30 секунд — это не зависание. Диагностика камеры — `PTZTracker-diag.exe` в той же папке. Если Windows SmartScreen предупредит о неизвестном издателе: «Подробнее» → «Выполнить в любом случае».

**macOS (Apple Silicon, macOS 12+).** Скачайте `PTZTracker-…-macos-arm64.zip`, распакуйте и перенесите «PTZ Tracker» в «Программы». Приложение не подписано сертификатом Apple, поэтому первый раз откройте его так: правый клик → «Открыть» → «Открыть». Если macOS пишет, что приложение повреждено, выполните в Терминале:

```bash
xattr -dr com.apple.quarantine "/Applications/PTZ Tracker.app"
```

При первом подключении к камере разрешите доступ к локальной сети.

Пошагово — [руководство оператора](https://github.com/funpin/ptz-tracker/blob/main/docs/OPERATOR.md). Лицензия — AGPL-3.0.
