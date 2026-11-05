from ptztracker.gamepad.battery import BatteryEstimator


def test_estimator_needs_two_drops():
    e = BatteryEstimator()
    e.add(0, 80, False)
    assert e.remaining_s() is None
    e.add(100, 70, False)  # первое падение
    assert e.remaining_s() is None
    e.add(700, 70, False)
    e.add(1000, 60, False)  # 10 % за 900 с
    assert e.remaining_s() == 60 / (10 / 900)


def test_estimator_rate_uses_first_to_latest_drop():
    e = BatteryEstimator()
    for t, p in ((0, 90), (100, 80), (400, 70), (1000, 60)):
        e.add(t, p, False)
    assert e.remaining_s() == 60 / (20 / 900)


def test_estimator_charging_and_reset():
    e = BatteryEstimator()
    for t, p in ((0, 90), (100, 80), (500, 70)):
        e.add(t, p, False)
    assert e.remaining_s() is not None
    e.add(600, 70, True)
    assert e.remaining_s() is None
    e.add(700, 70, False)  # отключили зарядку: счёт заново
    assert e.remaining_s() is None
    e.add(900, 60, False)
    assert e.remaining_s() is None
    e.add(1500, 50, False)
    assert e.remaining_s() == 50 / (10 / 600)
    e.reset()
    assert e.remaining_s() is None


def test_estimator_unknown_resets():
    e = BatteryEstimator()
    for t, p in ((0, 90), (100, 80), (500, 70)):
        e.add(t, p, False)
    e.add(600, None, None)
    assert e.remaining_s() is None
