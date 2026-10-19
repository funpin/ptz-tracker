import argparse
import sys
import time
from pathlib import Path

from ptztracker import __version__


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ptztracker", description="PTZ Tracker — управление камерами AW-HE42")
    p.add_argument("--version", action="version", version=f"PTZ Tracker {__version__}")
    p.add_argument("--sim", action="store_true", help="добавить виртуальную камеру «Симулятор» и открыть её")
    d = p.add_argument_group("диагностика камеры")
    d.add_argument("--diagnose", action="store_true", help="прогнать диагностику камеры и сохранить отчёт")
    d.add_argument("--ip", help="IP-адрес камеры")
    d.add_argument("--user", help="логин камеры (если включена авторизация)")
    d.add_argument("--password", help="пароль камеры")
    d.add_argument("--rtsp", help="адрес видеопотока, если известен")
    d.add_argument("--no-move", action="store_true", help="не двигать камеру")
    d.add_argument("--out", help="папка для отчёта (по умолчанию — рабочий стол)")
    d.add_argument("--notify-port", type=int, default=31004, help=argparse.SUPPRESS)
    d.add_argument("--notify-wait", type=float, default=15.0, help=argparse.SUPPRESS)
    return p


def _countdown(msg: str, seconds: float) -> None:
    print("\n" + "=" * 60 + f"\n>>> {msg}\n" + "=" * 60)
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        print(f"\r    осталось {left:4.0f} с ", end="", flush=True)
        time.sleep(min(1.0, left))
    print("\r    готово.            ")


def run_diagnose_cli(args) -> int:
    from ptztracker.diagnostics.runner import DiagContext, default_report_dir, run_diagnostics

    allow_move = not args.no_move
    if allow_move:
        ans = input("Камера будет немного двигаться и вернётся на место. Убедитесь, что она НЕ в эфире. Продолжить с движением? [д/н] ")
        allow_move = ans.strip().lower().startswith(("д", "y"))
        if not allow_move:
            print("Хорошо, проверяю без движения.")
    ctx = DiagContext(
        host=args.ip,
        username=args.user,
        password=args.password,
        allow_move=allow_move,
        ask_user=_countdown,
        progress=lambda m: print(m, flush=True),
        out_dir=Path(args.out) if args.out else default_report_dir(),
        rtsp_override=args.rtsp,
        notify_port=args.notify_port,
        notify_wait_s=args.notify_wait,
    )
    path = run_diagnostics(ctx)
    print(f"\nОтчёт сохранён: {path}\nПришлите этот файл разработчику.")
    return 0


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
    if args.diagnose:
        if not args.ip:
            print("Укажите IP камеры: --ip 10.0.0.12", file=sys.stderr)
            return 2
        return run_diagnose_cli(args)
    return run_gui(args.sim)


def diag_main() -> int:
    """Точка входа PTZTracker-diag.exe: при запуске двойным кликом спрашивает IP сам."""
    argv = sys.argv[1:]
    if "--ip" not in argv:
        from ptztracker.config import ConfigStore, default_config_path

        cfg = ConfigStore(default_config_path()).load()
        last = next((c for c in cfg.cameras if c.id == cfg.last_camera_id), cfg.cameras[0] if cfg.cameras else None)
        default_ip = last.ip if last else "10.0.0.12"
        ip = input(f"IP камеры [{default_ip}]: ").strip() or default_ip
        argv += ["--ip", ip]
    if "--user" not in argv:
        import getpass

        user = input("Логин камеры (Enter — без логина): ").strip()
        if user:
            argv += ["--user", user, "--password", getpass.getpass("Пароль (не отображается): ")]
    try:
        return main(["--diagnose", *argv])
    finally:
        if sys.stdin and sys.stdin.isatty():
            input("\nНажмите Enter, чтобы закрыть окно…")


if __name__ == "__main__":
    sys.exit(main())
