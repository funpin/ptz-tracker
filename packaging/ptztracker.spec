# PyInstaller.
# Windows: папка dist/PTZTracker с двумя программами —
#   PTZTracker.exe       — основное окно (без консоли)
#   PTZTracker-diag.exe  — диагностика камеры (консоль)
# macOS: dist/PTZ Tracker.app (диагностика — внутри: Contents/MacOS/PTZTracker-diag).
# Сборка: pyinstaller packaging/ptztracker.spec --noconfirm
import os
import re
import sys

from PyInstaller.utils.hooks import collect_all

SRC = os.path.join(SPECPATH, "..", "src")
EXCLUDES = ["tkinter", "matplotlib", "IPython", "PySide6.QtWebEngineCore", "PySide6.QtQuick", "PySide6.Qt3DCore"]

datas, binaries, hiddenimports = [], [], []
MAC = sys.platform == "darwin"
ICONS = os.path.join(SPECPATH, "icons")
with open(os.path.join(SRC, "ptztracker", "__init__.py"), encoding="utf-8") as f:
    VERSION = re.search(r'__version__ = "([^"]+)"', f.read()).group(1)

# модель распознавания людей (YOLO11n-pose, AGPL-3.0, как и весь проект)
datas += [(os.path.join(SRC, "ptztracker", "models", "yolo11n-pose.onnx"), "ptztracker/models")]
# onnxruntime: на Windows ставится onnxruntime-directml, но модуль называется так же — "onnxruntime"
for pkg in ("av", "onnxruntime"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += ["keyring.backends.Windows", "keyring.backends.macOS"]


def analysis(script):
    return Analysis(
        [os.path.join(SPECPATH, script)],
        pathex=[SRC],
        binaries=binaries,
        datas=datas,
        hiddenimports=hiddenimports,
        excludes=EXCLUDES,
        noarchive=False,
    )


gui = analysis("launch_gui.py")
diag = analysis("launch_diag.py")

gui_exe = EXE(
    PYZ(gui.pure),
    gui.scripts,
    [],
    exclude_binaries=True,
    name="PTZTracker",
    console=False,
    upx=False,
    icon=os.path.join(ICONS, "ptztracker.icns" if MAC else "ptztracker.ico"),
)
diag_exe = EXE(
    PYZ(diag.pure),
    diag.scripts,
    [],
    exclude_binaries=True,
    name="PTZTracker-diag",
    console=True,
    upx=False,
    icon=None if MAC else os.path.join(ICONS, "ptztracker.ico"),
)
coll = COLLECT(
    gui_exe,
    gui.binaries,
    gui.datas,
    diag_exe,
    diag.binaries,
    diag.datas,
    name="PTZTracker",
    upx=False,
)

if MAC:
    BUNDLE(
        coll,
        name="PTZ Tracker.app",
        icon=os.path.join(ICONS, "ptztracker.icns"),
        bundle_identifier="org.funpin.ptztracker",
        version=VERSION,
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            # обычное приложение с меню и значком в Dock; PyInstaller иначе берёт «фоновое» от консольной диагностики
            "LSBackgroundOnly": False,
            "LSMinimumSystemVersion": "12.0",
            # без этого macOS не спросит доступ к локальной сети, и камера не будет отвечать
            "NSLocalNetworkUsageDescription": "PTZ Tracker управляет PTZ-камерами и принимает их видео по локальной сети.",
        },
    )
