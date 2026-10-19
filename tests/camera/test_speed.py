import pytest

from ptztracker.camera.speed import velocity_to_code


@pytest.mark.parametrize(
    "v,c",
    [(0, 50), (0.01, 50), (-0.01, 50), (1, 99), (-1, 1), (0.5, 75), (-0.5, 25), (2, 99), (-2, 1), (0.03, 52)],
)
def test_velocity_to_code(v, c):
    assert velocity_to_code(v) == c
