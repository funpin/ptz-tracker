# PyInstaller: папка dist/PTZTracker с двумя программами —
#   PTZTracker.exe       — основное окно (без консоли)
#   PTZTracker-diag.exe  — диагностика камеры (консоль)
# Сборка: pyinstaller packaging/ptztracker.spec --noconfirm
import os

from PyInstaller.utils.hooks import collect_all

SRC = os.path.join(SPECPATH, "..", "src")
EXCLUDES = ["tkinter", "matplotlib", "IPython", "PySide6.QtWebEngineCore", "PySide6.QtQuick", "PySide6.Qt3DCore"]

datas, binaries, hiddenimports = [], [], []
# модель распознавания людей (YOLO11n-pose, AGPL-3.0, внутреннее использование)
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
)
diag_exe = EXE(
    PYZ(diag.pure),
    diag.scripts,
    [],
    exclude_binaries=True,
    name="PTZTracker-diag",
    console=True,
    upx=False,
)
COLLECT(
    gui_exe,
    gui.binaries,
    gui.datas,
    diag_exe,
    diag.binaries,
    diag.datas,
    name="PTZTracker",
    upx=False,
)
