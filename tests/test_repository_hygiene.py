"""В репозиторий не попадают данные с площадки и секреты."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ZIPS = {  # эпизоды без паролей (в meta username = null; кадров нет), для тестов регулятора на записях площадки
    "tests/data/episodes/sim_walk_30s.zip",
    "tests/data/episodes/2026-10-29-r1-waist.zip",
    "tests/data/episodes/2026-10-29-r4d-sharp.zip",
}


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return out.splitlines()


def test_no_site_data_or_secrets_tracked():
    bad = []
    for f in tracked():
        name = Path(f).name
        if f.endswith(".zip") and f not in ALLOWED_ZIPS:
            bad.append(f)  # отчёт диагностики или запись чёрного ящика с площадки
        if name == "config.json" or name == ".env" or name.endswith((".pem", ".key", ".p12")):
            bad.append(f)
    assert not bad, f"не для репозитория: {bad}"
