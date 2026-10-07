import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
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
from marianabot.engine import Engine
from marianabot.limits import SubscriptionLimits
from marianabot.reports import export_run


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


def stream_process(events, *, code=0, stderr=""):
    stdout = asyncio.StreamReader()
    stdout.feed_data(("\n".join(json.dumps(event) for event in events) + "\n").encode())
    stdout.feed_eof()
    errors = asyncio.StreamReader()
    errors.feed_data(stderr.encode())
    errors.feed_eof()
    return SimpleNamespace(
        returncode=code,
        stdout=stdout,
        stderr=errors,
        stdin=SimpleNamespace(write=lambda data: None, drain=AsyncMock(), close=lambda: None),
        wait=AsyncMock(return_value=code),
    )


async def test_claude_execution_error_retries_and_preserves_partial_history_and_usage(
    fake_clients, config, store, monkeypatch, tmp_path
):
    config.research.max_retries = 1
    error = {
        "type": "result",
        "subtype": "error_during_execution",
        "is_error": True,
        "errors": ["API Error: 500 api_error"],
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
    partial = "Unfinished review: https://partial.invalid/report"
    first = stream_process(
        [
            {
                "type": "stream_event",
                "event": {
                    "type": "message_start",
                    "message": {"id": "m1", "usage": {"input_tokens": 30}},
                },
            },
            {
                "type": "stream_event",
                "event": {"type": "message_delta", "usage": {"output_tokens": 8}},
            },
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": partial},
                },
            },
            error,
        ],
        stderr="Provider failed; Bearer private-token sk-secret",
    )
    second = stream_process(
        [
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Completed review",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            }
        ]
    )
    launch = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(clients, "launch", launch)
    run_id = store.create_run("Recover a failed critic", config, demo=True)
    engine = Engine(store, run_id)
    engine.clients["anthropic"] = NativeClient(
        "anthropic", config, store.directory, engine.limits["anthropic"]
    )
    waits = []

    async def skip_wait(seconds, reason):
        waits.append(reason)

    engine.wait = skip_wait
    result = await engine.call("JB", "critic", "Critique", {"plan": "Saved synthesis"})
    assert result["text"] == "Completed review"
    assert launch.await_count == 2 and len(waits) == 1
    calls = store.calls(run_id)
    assert [call["state"] for call in calls] == ["unknown", "done"]
    rejected = calls[0]["result"]
    assert rejected["retryable"] and not rejected["limited"]
    assert rejected["diagnostics"]["subtype"] == "error_during_execution"
    assert rejected["diagnostics"]["errors"] == error["errors"]
    assert rejected["diagnostics"]["partial_text"] == partial
    assert rejected["diagnostics"]["exit_code"] == 0
    assert "private-token" not in json.dumps(rejected)
    assert "sk-secret" not in json.dumps(rejected)
    assert calls[1]["result"]["prompt"] == rejected["prompt"]
    totals = store.usage_totals(run_id)["anthropic"]
    assert (totals["input_tokens"], totals["output_tokens"], totals["incomplete"]) == (40, 28, 1)
    assert store.cached(run_id, "critic")["text"] == "Completed review"
    report = export_run(store, run_id, tmp_path / "export")
    assert "partial.invalid" not in (report.parent / "citations.md").read_text()
    assert "partial.invalid" in (report.parent / "history.json").read_text()
    await engine.call("JB", "critic", "Critique", {})
    assert launch.await_count == 2


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_failure_after_success_is_rejected_even_with_zero_exit_code(
    provider, fake_clients, config, store, monkeypatch
):
    successful = (
        [
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Earlier output"}},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 8}},
        ]
        if provider == "openai"
        else [
            {"type": "result", "subtype": "success", "is_error": False, "result": "Earlier output"}
        ]
    )
    failure = {
        "type": "error",
        "error": {"type": "api_error", "message": "Unexpected failure", "status": 500},
    }
    monkeypatch.setattr(
        clients, "launch", AsyncMock(return_value=stream_process([*successful, failure]))
    )
    client = NativeClient(
        provider, config, store.directory, SubscriptionLimits(store, provider, config.subscription)
    )
    with pytest.raises(ClientError) as caught:
        await client.complete("Same request")
    assert caught.value.retryable
    assert caught.value.diagnostics["error"]["type"] == "api_error"
    assert caught.value.diagnostics["exit_code"] == 0


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_recovered_stream_error_does_not_discard_a_later_success(
    provider, fake_clients, config, store, monkeypatch
):
    failure = {"type": "error", "error": {"type": "api_error", "message": "Temporary failure"}}
    success = (
        [
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Recovered"}},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 8}},
        ]
        if provider == "openai"
        else [{"type": "result", "subtype": "success", "is_error": False, "result": "Recovered"}]
    )
    monkeypatch.setattr(
        clients, "launch", AsyncMock(return_value=stream_process([failure, *success]))
    )
    client = NativeClient(
        provider, config, store.directory, SubscriptionLimits(store, provider, config.subscription)
    )
    assert (await client.complete("Same request"))["text"] == "Recovered"


async def test_stderr_server_failure_is_classified_and_redacted(
    fake_clients, config, store, monkeypatch
):
    monkeypatch.setattr(
        clients,
        "launch",
        AsyncMock(
            return_value=stream_process([], code=1, stderr="API Error: 503. Bearer private-token")
        ),
    )
    client = NativeClient(
        "anthropic",
        config,
        store.directory,
        SubscriptionLimits(store, "anthropic", config.subscription),
    )
    with pytest.raises(ClientError) as caught:
        await client.complete("Saved request")
    assert caught.value.retryable
    assert caught.value.diagnostics["exit_code"] == 1
    assert "private-token" not in json.dumps(caught.value.diagnostics)


@pytest.mark.parametrize("result_subtype", ["success", "error_during_execution"])
async def test_assistant_api_error_is_not_hidden_by_a_generic_terminal_result(
    fake_clients, config, store, monkeypatch, result_subtype
):
    events = [
        {
            "type": "assistant",
            "error": "authentication_failed",
            "message": {"content": [{"type": "text", "text": "API Error: 401 Unauthorized"}]},
        },
        {
            "type": "result",
            "subtype": result_subtype,
            "is_error": result_subtype != "success",
            "errors": ["Unexpected execution failure"],
            "result": "API Error: 401 Unauthorized",
        },
    ]
    monkeypatch.setattr(clients, "launch", AsyncMock(return_value=stream_process(events)))
    client = NativeClient(
        "anthropic",
        config,
        store.directory,
        SubscriptionLimits(store, "anthropic", config.subscription),
    )
    with pytest.raises(ClientError) as caught:
        await client.complete("Saved request")
    assert not caught.value.retryable and not caught.value.limited
    assert caught.value.diagnostics["error"] == "authentication_failed"
    assert "API Error: 401" in caught.value.diagnostics["partial_text"]


async def test_later_clean_assistant_response_clears_an_api_error(
    fake_clients, config, store, monkeypatch
):
    events = [
        {
            "type": "assistant",
            "error": "api_error",
            "message": {"content": [{"type": "text", "text": "API Error: 500"}]},
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Recovered review"}]},
        },
        {"type": "result", "subtype": "success", "is_error": False, "result": "Recovered review"},
    ]
    monkeypatch.setattr(clients, "launch", AsyncMock(return_value=stream_process(events)))
    client = NativeClient(
        "anthropic",
        config,
        store.directory,
        SubscriptionLimits(store, "anthropic", config.subscription),
    )
    assert (await client.complete("Saved request"))["text"] == "Recovered review"
