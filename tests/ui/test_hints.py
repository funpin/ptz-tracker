from ptztracker.ui.hints import HintBar, hint_for


def texts(pairs):
    return [t for _k, _c, t in pairs]


def test_no_target_keyboard_and_pad():
    title, pairs = hint_for("off", "Кликните по человеку на видео, затем нажмите Пробел.", has_target=False)
    assert title == "" and ("mouse", "L", "по человеку — выбрать") in pairs  # «Цель не выбрана» — сверху слева
    _t, pairs = hint_for("off", "", has_target=False, pad=True)
    assert ("pad", "rstick", "нажать — вести") in pairs and all(k != "mouse" for k, _c, _x in pairs)


def test_target_then_following():
    _t, pairs = hint_for("off", "", has_target=True)
    assert pairs[0] == ("key", "Пробел", "вести")
    title, pairs = hint_for("on", "Камера ведёт цель №3.", has_target=True, target_id=3)
    assert title == "Веду №3" and ("key", "Esc", "стоп") in pairs
    assert len(pairs) <= 3


def test_reason_and_special_states_keep_words():
    title, _p = hint_for("off", "Слежение выключено: ручное управление", has_target=True)
    assert title == "Слежение выключено: ручное управление"
    assert hint_for("lost", "…", has_target=True)[0] == "Цель потеряна — жду"
    assert hint_for("remote", "…", has_target=True)[1] == [("key", "Пробел", "продолжить")]


def test_zone_hover_overrides():
    _t, pairs = hint_for("on", "", has_target=True, target_id=1, zone_hover=True)
    assert texts(pairs) == ["тяните — переместить", "за скобку — размер", "сброс"]
    assert pairs[-1][:2] == ("mouse", "LL")


def test_hint_bar_text_and_plain(qtbot):
    b = HintBar()
    qtbot.addWidget(b)
    b.set_hint("Веду №3", [("key", "Esc", "стоп"), ("pad", "rstick", "выключить"), ("mouse", "W", "зум")])
    assert b.text() == "Веду №3 · Esc стоп · выключить · зум"
    assert not b.grab().isNull()
    b.setText("Уже следит Симулятор 1")  # простое сообщение — без кнопок
    assert b.text() == "Уже следит Симулятор 1" and b.pairs == []
