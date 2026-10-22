"""Перезаписать эпизод-образец tests/data/episodes/sim_walk_30s.zip (30 с слежения на симуляторе).

Тест tests/recorder/test_stored_episode.py проверяет, что повтор эпизода даёт те же коды, что ушли в камеру.
После намеренного изменения регулятора старый эпизод так не повторяется — перезапишите его:

    .venv/bin/python tools/record_episode.py

Сценарий как у исходного эпизода: камера-симулятор без калибровки, сцена «walk», композиция и характер
по умолчанию, слежение 30 с, сохранение как по F9. Пароля у симулятора нет — в эпизод он не попадает.
"""

import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DST = ROOT / "tests" / "data" / "episodes" / "sim_walk_30s.zip"
FOLLOW_S = 30.0
MAX_BYTES = 3 * 1024 * 1024


def _wait(pred, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def main() -> int:
    from ptztracker.camera.session import CameraSession
    from ptztracker.config import AppConfig, CameraConfig
    from ptztracker.recorder.reader import Episode
    from ptztracker.recorder.replay import replay_controller

    work = Path(tempfile.mkdtemp(prefix="episode-"))
    s = CameraSession(CameraConfig(id="s", name="Сим", ip="sim", kind="sim"), app_cfg=AppConfig(record_dir=str(work)))
    s.start()
    try:
        if not _wait(lambda: s.analyzer.latest() is not None and len(s.analyzer.latest().tracks) > 0, 10):
            print("Симулятор не показал человека", file=sys.stderr)
            return 1
        tr = s.analyzer.latest().tracks[0]
        s.analyzer.select_at((tr.box[0] + tr.box[2]) / 2, (tr.box[1] + tr.box[3]) / 2)
        if not _wait(lambda: s.analyzer.latest().target_state == "tracking", 3) or not s.follower.enable():
            print("Слежение не включилось", file=sys.stderr)
            return 1
        time.sleep(FOLLOW_S)
        if not s.follower.enabled:
            print(f"Слежение выключилось само: {s.follower.last_reason}", file=sys.stderr)
            return 1
        path = s.save_moment("F9").result(timeout=30)
    finally:
        s.stop()
    size = path.stat().st_size
    if size > MAX_BYTES:
        print(f"Эпизод {size / 1e6:.1f} МБ — больше 3 МБ", file=sys.stderr)
        return 1
    res = replay_controller(Episode.open(path))
    diff = res.summary().max_code_diff
    if diff > 1:
        print(f"Повтор не совпал с записью (расхождение кодов {diff}) — эпизод не сохранён", file=sys.stderr)
        return 1
    shutil.copy2(path, DST)
    shutil.rmtree(work, ignore_errors=True)
    print(f"{DST.relative_to(ROOT)}: {size / 1e6:.2f} МБ, слежение {res.summary().following_s:.1f} с, "
          f"расхождение кодов {diff}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
