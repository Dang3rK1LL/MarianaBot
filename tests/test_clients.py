import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from marianabot import clients
from marianabot.clients import (
    ClaudeAccount,
    ClientError,
    CodexAccount,
    NativeClient,
    claude_account,
    claude_models,
    subscription_env,
)
from marianabot.limits import SubscriptionLimits


@pytest.fixture
def fake_clients(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
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


async def test_claude_usage_reads_only_metadata_and_preserves_research_isolation(
    fake_clients, config, tmp_path, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-child")
    config.jb.model = "unavailable-saved-model"
    config.jb.effort = "unavailable-effort"
    account = await ClaudeAccount(config, tmp_path).snapshot()
    assert account["auth"] == "claude.ai"
    assert account["limits"]["five_hour"]["utilization"] == 5
    assert account["limits"]["seven_day"]["utilization"] == 12
    assert time.time() - account["observed"] < 5
    assert subscription_env()["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


@pytest.mark.parametrize("limits", [None, {}, {"error": {"type": "rate_limit_error"}}])
async def test_claude_missing_usage_does_not_become_zero(
    config, tmp_path, monkeypatch, limits, store
):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(
        clients,
        "_claude_metadata",
        AsyncMock(
            return_value={"metadata": {"rate_limits_available": True, "rate_limits": limits}}
        ),
    )
    if limits is None:
        with pytest.raises(ClientError, match="unavailable"):
            await ClaudeAccount(config, tmp_path).snapshot()
    else:
        result = await ClaudeAccount(config, tmp_path).snapshot()
        with pytest.raises(ValueError, match="no readable"):
            SubscriptionLimits(store, "anthropic", config.subscription).claude_snapshot(
                result["limits"]
            )


@pytest.mark.parametrize("age", [25, 360])
async def test_claude_cache_timestamp_is_preserved_and_stale_fallback_is_rejected(
    config, tmp_path, monkeypatch, age
):
    # Use the clock value from the failed CI run so this regression is deterministic.
    now = 1_791_289_933.9084523
    monkeypatch.setattr(clients.time, "time", lambda: now)
    fetched_at_ms = int((now - age) * 1000)
    limits = {"five_hour": {"utilization": 42, "resets_at": "2030-01-01T00:00:00Z"}}
    (tmp_path / ".claude.json").write_text(
        json.dumps(
            {"cachedUsageUtilization": {"fetchedAtMs": fetched_at_ms, "utilization": limits}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(
        clients,
        "_claude_metadata",
        AsyncMock(
            return_value={"metadata": {"rate_limits_available": True, "rate_limits": limits}}
        ),
    )
    if age > 90:
        with pytest.raises(ClientError, match="stale"):
            await ClaudeAccount(config, tmp_path).snapshot()
    else:
        result = await ClaudeAccount(config, tmp_path).snapshot()
        assert result["observed"] == pytest.approx(now - age, rel=0, abs=0.001)


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
