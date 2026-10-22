"""run_mac.command: окружение считается готовым только после удачной установки и с Python 3.12–3.13.

Скрипт запускается в копии папки, а python3.13 в PATH — подделка: «venv» создаёт поддельные
.venv/bin/python и pip, их поведение задают переменные окружения.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="скрипт для Mac")

FAKE_PY = r"""#!/bin/bash
# поддельный системный python3.13
if [ "$1" = "-c" ]; then exit 0; fi
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
    echo venv >> "$FAKE_LOG"
    mkdir -p "$3/bin"
    cat > "$3/bin/python" <<'PY'
#!/bin/bash
if [ "$1" = "-c" ]; then [ -z "$FAKE_VENV_OLD" ]; exit $?; fi
echo "RUN $*"
PY
    cat > "$3/bin/pip" <<'PIP'
#!/bin/bash
echo pip >> "$FAKE_LOG"
[ -n "$FAKE_PIP_OK" ]
PIP
    chmod +x "$3/bin/python" "$3/bin/pip"
    exit 0
fi
exit 1
"""


@pytest.fixture
def launcher(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    shutil.copy(ROOT / "run_mac.command", app / "run_mac.command")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "python3.13").write_text(FAKE_PY)
    (fake_bin / "python3.13").chmod(0o755)
    log = tmp_path / "log"

    def run(**env):
        log.write_text("")
        full = {"PATH": f"{fake_bin}:/usr/bin:/bin", "FAKE_LOG": str(log), **env}
        p = subprocess.run(["bash", str(app / "run_mac.command"), "--sim"], input="\n", capture_output=True,
                           text=True, env=full, timeout=30)
        return p, log.read_text().split()

    run.venv = app / ".venv"
    return run


def test_failed_install_is_retried_on_next_launch(launcher):
    p, log = launcher()
    assert p.returncode == 1 and "Не удалось подготовить окружение" in p.stdout
    assert (launcher.venv / "bin" / "python").exists() and not (launcher.venv / ".ready").exists()

    p, log = launcher(FAKE_PIP_OK="1")  # следующий запуск пересобирает окружение, а не падает
    assert p.returncode == 0 and "RUN -m ptztracker --sim" in p.stdout
    assert log == ["venv", "pip"] and (launcher.venv / ".ready").exists()

    p, log = launcher(FAKE_PIP_OK="1")  # готовое окружение не пересобирается
    assert p.returncode == 0 and log == []


def test_venv_with_wrong_python_is_rebuilt(launcher):
    assert launcher(FAKE_PIP_OK="1")[0].returncode == 0
    p, log = launcher(FAKE_PIP_OK="1", FAKE_VENV_OLD="1")
    assert log == ["venv", "pip"]
    assert "RUN -m ptztracker --sim" in p.stdout
