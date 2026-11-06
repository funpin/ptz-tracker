import uuid

from ptztracker.ui.single_instance import claim


def test_second_copy_is_refused_and_first_hears_it(qtbot):
    name = f"ptztracker-test-{uuid.uuid4().hex[:8]}"
    first = claim(name)
    assert first is not None
    heard = []
    first.newConnection.connect(lambda: heard.append(True))
    assert claim(name) is None  # вторая копия не запускается
    qtbot.waitUntil(lambda: bool(heard), timeout=2000)  # первая узнаёт и поднимает окно
    first.close()
    again = claim(name)  # первая закрылась — можно снова
    assert again is not None
    again.close()
