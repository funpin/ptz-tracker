import pytest
from video_helpers import make_h264


@pytest.fixture
def h264_file(tmp_path_factory):
    return make_h264(tmp_path_factory.mktemp("src") / "src.mp4")


@pytest.fixture
def h264_file_50(tmp_path_factory):
    return make_h264(tmp_path_factory.mktemp("src50") / "src50.mp4", fps=50, seconds=2)
