from PySide6.QtCore import QPoint, Qt

from ptztracker.ui.joystick import JoystickWidget


def test_joystick_spring_back(qtbot):
    j = JoystickWidget()
    qtbot.addWidget(j)
    j.resize(200, 200)
    got = []
    j.moved.connect(lambda x, y: got.append((x, y)))
    qtbot.mousePress(j, Qt.LeftButton, pos=QPoint(100, 100))
    qtbot.mouseMove(j, QPoint(190, 100))
    assert got[-1][0] > 0.8 and abs(got[-1][1]) < 0.05
    qtbot.mouseRelease(j, Qt.LeftButton, pos=QPoint(190, 100))
    assert got[-1] == (0.0, 0.0)


def test_joystick_up_is_positive_and_clamped(qtbot):
    j = JoystickWidget()
    qtbot.addWidget(j)
    j.resize(200, 200)
    got = []
    j.moved.connect(lambda x, y: got.append((x, y)))
    qtbot.mousePress(j, Qt.LeftButton, pos=QPoint(100, 100))
    qtbot.mouseMove(j, QPoint(100, 0))
    x, y = got[-1]
    assert y > 0.8 and (x * x + y * y) <= 1.0001
