import sys
from pathlib import Path

import pytest

from marianabot import clients
from marianabot.clients import (
    CodexAccount,
    NativeClient,
    claude_account,
    claude_models,
    subscription_env,
)
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
    assert account["model_details"][0]["supportedReasoningEfforts"]
    assert (await claude_account(config, tmp_path))["auth"] == "claude.ai"


async def test_claude_catalog_uses_only_initialization_and_resolves_picker_models(
    fake_clients, config, tmp_path
):
    config.jb.model = "unavailable-saved-model"
    models = await claude_models(config, tmp_path)
    assert models[0]["resolvedModel"] == "claude-opus-5-5"
    assert "xhigh" in models[0]["supportedEffortLevels"]
    assert models[1]["resolvedModel"] == "claude-haiku-4-5-20251001"


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_default_effort_does_not_pass_an_unsupported_level(
    provider, fake_clients, config, tmp_path
):
    config.rb.effort = config.jb.effort = "auto"
    args = NativeClient(provider, config, tmp_path, None).args(False)
    assert "--effort" not in args
    assert not any("model_reasoning_effort" in argument for argument in args)


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
        "MARIANA_DISCORD_BOT_TOKEN",
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
            "MARIANA_DISCORD_BOT_TOKEN",
        )
    )
    assert clean["CLAUDE_CODE_DISABLE_FAST_MODE"] == "1"
