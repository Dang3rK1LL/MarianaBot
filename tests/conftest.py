import pytest

from marianabot.clients import ClientError
from marianabot.config import Config
from marianabot.store import Store


@pytest.fixture(autouse=True)
def offline_chat_account(monkeypatch):
    """UI tests must explicitly supply quota fixtures instead of using real logins."""

    class OfflineAccount:
        def __init__(self, *args):
            pass

        async def snapshot(self):
            raise ClientError("Offline account fixture")

    monkeypatch.setattr("marianabot.chat.CodexAccount", OfflineAccount)
    monkeypatch.setattr("marianabot.chat.ClaudeAccount", OfflineAccount)
    monkeypatch.setattr("marianabot.engine.ClaudeAccount", OfflineAccount)


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
