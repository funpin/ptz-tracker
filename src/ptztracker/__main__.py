import argparse
import sys

from ptztracker import __version__


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ptztracker", description="PTZ Tracker — управление камерами AW-HE42")
    p.add_argument("--version", action="version", version=f"PTZ Tracker {__version__}")
    p.add_argument("--sim", action="store_true", help="добавить виртуальную камеру «Симулятор» и открыть её")
    return p


def run_gui(use_sim: bool) -> int:
    from PySide6.QtWidgets import QApplication

    from ptztracker.config import ConfigStore, default_config_path
    from ptztracker.logging_setup import setup_logging
    from ptztracker.ui.main_window import MainWindow
    from ptztracker.ui.theme import apply_dark_theme

    setup_logging()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("PTZ Tracker")
    apply_dark_theme(app)
    w = MainWindow(ConfigStore(default_config_path()), use_sim=use_sim)
    w.show()
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return run_gui(args.sim)


if __name__ == "__main__":
    sys.exit(main())
