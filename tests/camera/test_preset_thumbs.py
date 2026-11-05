"""Снимки пресетов: картинка, имя и поворот камеры на диске, по id камеры."""

import numpy as np

from ptztracker.camera.preset_thumbs import THUMB_SIZE, PresetThumbs


def test_save_and_load(tmp_path):
    t = PresetThumbs(tmp_path)
    img = np.full((720, 1280, 3), 80, np.uint8)
    t.save("cam-a", 3, img, pan=12.5)
    got = PresetThumbs(tmp_path).load("cam-a")  # с диска заново
    assert set(got) == {3}
    assert got[3].pan == 12.5 and got[3].name == "" and got[3].image.exists()
    import cv2

    h, w = cv2.imread(str(got[3].image)).shape[:2]
    assert (w, h) == THUMB_SIZE


def test_name_and_erase(tmp_path):
    t = PresetThumbs(tmp_path)
    t.save("cam-a", 1, np.zeros((360, 640, 3), np.uint8), pan=None)
    t.rename("cam-a", 1, "Трибуна")
    t.rename("cam-a", 2, "Без снимка")
    got = t.load("cam-a")
    assert got[1].name == "Трибуна" and got[2].name == "Без снимка" and got[2].image is None
    t.erase("cam-a", 1)
    assert 1 not in t.load("cam-a") and not list(tmp_path.rglob("1.jpg"))
    assert t.load("unknown") == {}


def test_cameras_are_separate_and_ids_safe(tmp_path):
    t = PresetThumbs(tmp_path)
    t.save("../evil", 1, np.zeros((10, 10, 3), np.uint8), pan=0.0)
    assert all(tmp_path in p.parents for p in tmp_path.rglob("*") if p.is_file())
    assert t.load("cam-b") == {}


def test_broken_index_is_ignored(tmp_path):
    t = PresetThumbs(tmp_path)
    t.save("cam-a", 1, np.zeros((10, 10, 3), np.uint8), pan=0.0)
    for f in tmp_path.rglob("index.json"):
        f.write_text("{oops")
    assert PresetThumbs(tmp_path).load("cam-a") == {}
