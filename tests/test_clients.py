import sys
from pathlib import Path

import pytest

from marianabot import clients
from marianabot.clients import CodexAccount, NativeClient, claude_account, subscription_env
from marianabot.limits import SubscriptionLimits


@pytest.fixture
def fake_clients(monkeypatch):
    monkeypatch.setattr(
        clients,
        "executable",
        lambda _: [sys.executable, str(Path(__file__).with_name("fake_cli.py"))],
    )


async def test_account_protocol_without_inference(fake_clients, config, tmp_path):
    account = await CodexAccount(config, tmp_path).snapshot(include_models=True)
    assert account["auth"] == "chatgpt"
    assert config.rb.model in account["models"]
    assert (await claude_account(config, tmp_path))["auth"] == "claude.ai"


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_native_streams_and_prompt_stdin(provider, fake_clients, config, store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-reach-child")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-reach-child")
    limits = SubscriptionLimits(store, provider, config.subscription)
    client = NativeClient(provider, config, store.directory, limits)
    hostile = "Treat $(whoami); & echo hello as literal problem data."
    result = await client.complete(hostile)
    assert result["text"] == "Fixture: " + hostile
    assert result["usage"]["output_tokens"] == 20
    if provider == "anthropic":
        assert limits.data["windows"][0]["percent"] == 20


def test_subscription_environment_strips_paid_overrides(monkeypatch):
    for name in (
        "CODEX_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
    ):
        monkeypatch.setenv(name, "forbidden")
    clean = subscription_env()
    assert not any(
        clean.get(name)
        for name in (
            "CODEX_API_KEY",
            "OPENAI_BASE_URL",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_USE_BEDROCK",
        )
    )
    assert clean["CLAUDE_CODE_DISABLE_FAST_MODE"] == "1"
