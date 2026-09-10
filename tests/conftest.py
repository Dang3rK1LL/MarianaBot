import pytest

from marianabot.config import Config
from marianabot.store import Store


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "state")
    yield instance
    instance.close()


@pytest.fixture
def config():
    cfg = Config()
    cfg.research.max_rounds = 2
    cfg.research.min_rounds = 1
    cfg.research.max_retries = 0
    cfg.rb.agents = cfg.jb.agents = 2
    cfg.subscription.request_spacing_seconds = 0
    return cfg
