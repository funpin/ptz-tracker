from ptztracker.multicam.analysis import CellState, allocate_analysis, tracking_refusal


def c(name="PTZ", allowed=True, on=False):
    return CellState(name, allowed, on)


def test_limit_one_selected_gets_it():
    assert allocate_analysis([c(), c()], 1, 1) == frozenset({1})


def test_tracking_first():
    assert allocate_analysis([c(on=True), c()], 1, 1) == frozenset({0})


def test_never_takes_from_tracking():
    assert allocate_analysis([c(on=True), c(on=True)], None, 1) == frozenset({0, 1})


def test_not_allowed_never():
    cells = [c(), c(allowed=False)]
    assert 1 not in allocate_analysis(cells, 1, 4)


def test_fill_rest_in_order():
    cells = [c(), c(), c(), c()]
    assert allocate_analysis(cells, 2, 3) == frozenset({2, 0, 1})


def test_refusal_text():
    cells = [c("PTZ 2", on=True), c()]
    assert tracking_refusal(cells, 1, 1) == (
        "Уже следит PTZ 2 — выключите там или увеличьте число камер в настройках"
    )
    assert tracking_refusal(cells, 1, 2) is None
    assert tracking_refusal(cells, 0, 1) is None


def test_refusal_not_allowed():
    assert tracking_refusal([c(allowed=False)], 0, 4) == (
        "Слежение на этой камере выключено в настройках камеры"
    )


def test_preferred_list_in_order():
    cells = [c(), c(), c()]
    assert allocate_analysis(cells, [2, 0], 1) == frozenset({2})  # первый в списке оператора
    assert allocate_analysis(cells, [2, 0], 2) == frozenset({2, 0})
