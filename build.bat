@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

py -3.12 --version >nul 2>&1
if errorlevel 1 (
  echo Нужен Python 3.12 для Windows: https://www.python.org/downloads/
  exit /b 1
)

if not exist .venv\Scripts\python.exe py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip || exit /b 1
.venv\Scripts\python -m pip install -e .[build] || exit /b 1
.venv\Scripts\pyinstaller packaging\ptztracker.spec --noconfirm || exit /b 1
powershell -NoProfile -Command "Compress-Archive -Path dist\PTZTracker\* -DestinationPath dist\PTZTracker-win64.zip -Force" || exit /b 1

echo.
echo Готово: dist\PTZTracker\PTZTracker.exe
echo Архив для переноса: dist\PTZTracker-win64.zip
