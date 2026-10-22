import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _ephemeral_api_port(request, monkeypatch):
    """Окна в тестах (tests/ui, tests/api) поднимают API на свободном порту, а не на 8765:
    его может держать запущенная программа или соседний тест."""
    if request.node.path.parent.name not in ("ui", "api"):
        return
    from ptztracker.config import AppConfig, ConfigStore

    load = ConfigStore.load

    def load_ephemeral(self):
        cfg = load(self)
        if cfg.api_port == AppConfig().api_port:
            cfg.api_port = 0
        return cfg

    monkeypatch.setattr(ConfigStore, "load", load_ephemeral)
