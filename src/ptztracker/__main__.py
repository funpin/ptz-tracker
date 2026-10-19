import sys

from ptztracker import __version__


def main(argv: list[str] | None = None) -> int:
    print(f"PTZ Tracker {__version__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
